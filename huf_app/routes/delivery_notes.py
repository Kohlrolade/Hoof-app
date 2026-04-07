"""Delivery note workflows and entry editing routes."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..db.core import execute, get_conn, qall, qone
from ..presentation import render
from ..services.auth import get_current_user, require_permission
from ..services.customers import create_customer as create_customer_record
from ..services.invoices import (
    create_invoice_draft_from_group,
    group_total,
    next_number,
    recompute_group_status,
    suggested_service_for_horse,
)
from ..utils.formatting import now_ts
from ..utils.labels import customer_label, normalize_name

router = APIRouter()

@router.get('/delivery-notes', response_class=HTMLResponse)
def delivery_notes_page(request: Request, status: str | None = None):
    user = get_current_user(request)
    require_permission(user['id'], 'delivery_notes', 'view')
    with get_conn() as conn:
        sql = "SELECT d.*, l.name AS location_name, (SELECT COUNT(*) FROM delivery_note_customer_groups g WHERE g.delivery_note_id = d.id) AS group_count FROM delivery_notes d JOIN locations l ON l.id = d.location_id"
        params: tuple = ()
        if status:
            sql += ' WHERE d.status = ?'
            params = (status,)
        sql += ' ORDER BY d.service_date DESC, d.id DESC'
        rows = qall(conn, sql, params)
        locations = qall(conn, 'SELECT * FROM locations ORDER BY name')
    return render(request, 'delivery_notes.html', delivery_notes=rows, locations=locations, status_filter=status or '')


@router.post('/delivery-notes')
def create_delivery_note(request: Request, location_id: int = Form(...), service_date: str = Form(...), note: str = Form('')):
    user = get_current_user(request)
    require_permission(user['id'], 'delivery_notes', 'create')
    if not service_date:
        raise HTTPException(status_code=400, detail='Leistungsdatum fehlt.')
    with get_conn() as conn:
        year = int(service_date[:4])
        number = next_number(conn, 'delivery_note', 'LS', year)
        dn_id = execute(conn, 'INSERT INTO delivery_notes (delivery_note_number, location_id, service_date, status, created_by_user_id, note, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (number, location_id, service_date, 'draft', user['id'], note, now_ts(), now_ts()))
    return RedirectResponse(f'/delivery-notes/{dn_id}', status_code=303)


@router.get('/delivery-notes/{delivery_note_id}', response_class=HTMLResponse)
def delivery_note_detail(request: Request, delivery_note_id: int):
    user = get_current_user(request)
    require_permission(user['id'], 'delivery_notes', 'view')
    with get_conn() as conn:
        dn = qone(conn, "SELECT d.*, l.name AS location_name, l.city AS location_city FROM delivery_notes d JOIN locations l ON l.id = d.location_id WHERE d.id = ?", (delivery_note_id,))
        if not dn:
            raise HTTPException(status_code=404, detail='Tageslieferschein nicht gefunden.')
        groups_raw = qall(conn, "SELECT g.*, c.first_name, c.last_name, c.company_name FROM delivery_note_customer_groups g JOIN customers c ON c.id = g.customer_id WHERE g.delivery_note_id = ? ORDER BY g.id", (delivery_note_id,))
        groups = []
        for g in groups_raw:
            entries = qall(conn, "SELECT e.*, h.name AS horse_name FROM delivery_note_entries e JOIN horses h ON h.id = e.horse_id WHERE e.delivery_note_customer_group_id = ? ORDER BY e.sort_order, e.id", (g['id'],))
            gdict = dict(g)
            gdict['entries'] = entries
            gdict['total'] = group_total(conn, g['id'])
            gdict['customer_name'] = customer_label(g)
            gdict['horses'] = qall(conn, 'SELECT * FROM horses WHERE customer_id = ? ORDER BY name', (g['customer_id'],))
            link = qone(conn, 'SELECT * FROM invoice_source_links WHERE delivery_note_customer_group_id = ?', (g['id'],))
            gdict['invoice_id'] = link['invoice_id'] if link else None
            groups.append(gdict)
        customers = qall(conn, 'SELECT * FROM customers ORDER BY customer_number LIMIT 500')
    return render(request, 'delivery_note_detail.html', dn=dn, groups=groups, customers=customers, customer_label=customer_label)


@router.post('/delivery-notes/{delivery_note_id}/groups')
def add_customer_group(
    request: Request,
    delivery_note_id: int,
    customer_id: int | None = Form(None),
    payment_method: str = Form('bank_transfer'),
    note: str = Form(''),
    new_type: str = Form('private'),
    new_first_name: str = Form(''),
    new_last_name: str = Form(''),
    new_company_name: str = Form(''),
    new_email: str = Form(''),
    new_phone: str = Form(''),
    new_street: str = Form(''),
    new_postal_code: str = Form(''),
    new_city: str = Form(''),
):
    user = get_current_user(request)
    require_permission(user['id'], 'delivery_notes', 'edit')
    with get_conn() as conn:
        if not customer_id:
            label = normalize_name(new_first_name, new_last_name, new_company_name)
            if not label:
                raise HTTPException(status_code=400, detail='Kunde oder Schnellanlage nötig.')
            customer_id = create_customer_record(
                conn,
                type=new_type,
                first_name=new_first_name,
                last_name=new_last_name,
                company_name=new_company_name,
                street=new_street,
                postal_code=new_postal_code,
                city=new_city,
                email=new_email,
                phone=new_phone,
                default_payment_term_days=14,
                note='Schnellanlage aus Tageslieferschein',
            )
        execute(conn, 'INSERT INTO delivery_note_customer_groups (delivery_note_id, customer_id, payment_method, status, note, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)', (delivery_note_id, customer_id, payment_method, 'draft', note, now_ts(), now_ts()))
        conn.execute('UPDATE delivery_notes SET status = ?, updated_at = ? WHERE id = ?', ('saved', now_ts(), delivery_note_id))
    return RedirectResponse(f'/delivery-notes/{delivery_note_id}', status_code=303)


@router.post('/groups/{group_id}/entries')
def add_entry(
    request: Request,
    group_id: int,
    horse_id: int | None = Form(None),
    new_horse_name: str = Form(''),
    service_name: str = Form(...),
    quantity: float = Form(...),
    unit: str = Form(...),
    unit_price_gross: float = Form(...),
    vat_rate: float = Form(...),
    note: str = Form(''),
    suggested_service_name: str = Form(''),
):
    user = get_current_user(request)
    require_permission(user['id'], 'delivery_notes', 'edit')
    with get_conn() as conn:
        group = qone(conn, 'SELECT * FROM delivery_note_customer_groups WHERE id = ?', (group_id,))
        if not group:
            raise HTTPException(status_code=404, detail='Kundenblock nicht gefunden.')
        if group['status'] in ('invoiced', 'paid_cash', 'invoice_draft'):
            raise HTTPException(status_code=400, detail='Dieser Kundenblock ist bereits in Rechnung übernommen.')
        if not horse_id:
            if not new_horse_name:
                raise HTTPException(status_code=400, detail='Pferd fehlt.')
            dn = qone(conn, 'SELECT * FROM delivery_notes WHERE id = ?', (group['delivery_note_id'],))
            horse_id = execute(conn, 'INSERT INTO horses (name, customer_id, location_id, note, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)', (new_horse_name, group['customer_id'], dn['location_id'], 'Schnellanlage aus Tageslieferschein', 1, now_ts(), now_ts()))
        else:
            horse = qone(conn, 'SELECT * FROM horses WHERE id = ?', (horse_id,))
            if horse and horse['customer_id'] != group['customer_id']:
                raise HTTPException(status_code=400, detail='Das Pferd gehört zu einem anderen Kunden.')
        sort_order = qone(conn, 'SELECT COALESCE(MAX(sort_order), 0) + 1 AS n FROM delivery_note_entries WHERE delivery_note_customer_group_id = ?', (group_id,))['n']
        total = round(float(quantity) * float(unit_price_gross), 2)
        tpl = qone(conn, 'SELECT * FROM service_templates WHERE lower(name) = lower(?)', (service_name,))
        tpl_id = tpl['id'] if tpl else None
        execute(conn, 'INSERT INTO delivery_note_entries (delivery_note_customer_group_id, horse_id, service_template_id, suggested_service_name, actual_service_name, quantity, unit, unit_price_gross, vat_rate, total_price_gross, note, sort_order, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (group_id, horse_id, tpl_id, suggested_service_name, service_name, quantity, unit, unit_price_gross, vat_rate, total, note, sort_order, now_ts(), now_ts()))
        recompute_group_status(conn, group_id)
        dn_id = group['delivery_note_id']
    return RedirectResponse(f'/delivery-notes/{dn_id}', status_code=303)


@router.get('/entries/{entry_id}/edit', response_class=HTMLResponse)
def edit_entry_form(request: Request, entry_id: int):
    user = get_current_user(request)
    require_permission(user['id'], 'delivery_notes', 'edit')
    with get_conn() as conn:
        entry = qone(conn, 'SELECT e.*, h.name AS horse_name, g.customer_id, g.delivery_note_id FROM delivery_note_entries e JOIN horses h ON h.id = e.horse_id JOIN delivery_note_customer_groups g ON g.id = e.delivery_note_customer_group_id WHERE e.id = ?', (entry_id,))
        if not entry:
            raise HTTPException(status_code=404, detail='Eintrag nicht gefunden.')
    return render(request, 'entry_edit.html', entry=entry)


@router.post('/entries/{entry_id}/edit')
def edit_entry(
    request: Request,
    entry_id: int,
    service_name: str = Form(...),
    quantity: float = Form(...),
    unit: str = Form(...),
    unit_price_gross: float = Form(...),
    vat_rate: float = Form(...),
    note: str = Form(''),
):
    user = get_current_user(request)
    require_permission(user['id'], 'delivery_notes', 'edit')
    with get_conn() as conn:
        entry = qone(conn, 'SELECT e.*, g.delivery_note_id, g.id AS group_id, g.status AS group_status FROM delivery_note_entries e JOIN delivery_note_customer_groups g ON g.id = e.delivery_note_customer_group_id WHERE e.id = ?', (entry_id,))
        if not entry:
            raise HTTPException(status_code=404, detail='Eintrag nicht gefunden.')
        if entry['group_status'] in ('invoiced', 'paid_cash', 'invoice_draft'):
            raise HTTPException(status_code=400, detail='Block ist bereits in Rechnung übernommen.')
        total = round(float(quantity) * float(unit_price_gross), 2)
        conn.execute('UPDATE delivery_note_entries SET actual_service_name = ?, quantity = ?, unit = ?, unit_price_gross = ?, vat_rate = ?, total_price_gross = ?, note = ?, updated_at = ? WHERE id = ?', (service_name, quantity, unit, unit_price_gross, vat_rate, total, note, now_ts(), entry_id))
    return RedirectResponse(f"/delivery-notes/{entry['delivery_note_id']}", status_code=303)


@router.post('/entries/{entry_id}/delete')
def delete_entry(request: Request, entry_id: int):
    user = get_current_user(request)
    require_permission(user['id'], 'delivery_notes', 'edit')
    with get_conn() as conn:
        entry = qone(conn, 'SELECT e.*, g.delivery_note_id, g.id AS group_id, g.status AS group_status FROM delivery_note_entries e JOIN delivery_note_customer_groups g ON g.id = e.delivery_note_customer_group_id WHERE e.id = ?', (entry_id,))
        if not entry:
            raise HTTPException(status_code=404, detail='Eintrag nicht gefunden.')
        if entry['group_status'] in ('invoiced', 'paid_cash', 'invoice_draft'):
            raise HTTPException(status_code=400, detail='Block ist bereits in Rechnung übernommen.')
        conn.execute('DELETE FROM delivery_note_entries WHERE id = ?', (entry_id,))
        recompute_group_status(conn, entry['group_id'])
    return RedirectResponse(f"/delivery-notes/{entry['delivery_note_id']}", status_code=303)


@router.post('/groups/{group_id}/invoice-draft')
def create_invoice_draft_route(request: Request, group_id: int):
    user = get_current_user(request)
    require_permission(user['id'], 'invoices', 'create')
    with get_conn() as conn:
        invoice_id = create_invoice_draft_from_group(conn, group_id)
    return RedirectResponse(f'/invoices/{invoice_id}', status_code=303)


@router.get('/api/horse-suggestion/{horse_id}')
def horse_suggestion(horse_id: int, customer_id: int | None = None):
    with get_conn() as conn:
        return suggested_service_for_horse(conn, horse_id, customer_id)
