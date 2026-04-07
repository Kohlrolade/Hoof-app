"""Delivery note workflows and entry editing routes."""

from __future__ import annotations

import re

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..db.core import execute, get_conn, qall, qone
from ..presentation import render
from ..services.auth import get_current_user, require_permission
from ..services.customers import create_customer as create_customer_record
from ..services.invoices import (
    create_invoice_draft_from_group,
    recompute_group_status,
    suggested_service_for_horse,
)
from ..utils.formatting import now_ts
from ..utils.labels import customer_label, normalize_name

router = APIRouter()


def _ensure_default_delivery_note_location(conn) -> int:
    """Keep delivery note creation independent from explicit location selection."""
    row = qone(conn, "SELECT id FROM locations WHERE name = 'Unbekannt / flexibel' LIMIT 1")
    if row:
        return row['id']
    return execute(
        conn,
        'INSERT INTO locations (name, street, postal_code, city, contact_person, phone, note, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (
            'Unbekannt / flexibel',
            '',
            '',
            '',
            '',
            '',
            'Automatisch angelegt für Tageslieferscheine ohne feste Stallzuordnung.',
            1,
            now_ts(),
            now_ts(),
        ),
    )


def _parse_lookup_id(value: str, prefix: str) -> int | None:
    if not value:
        return None
    match = re.search(rf'#{prefix}(\d+)', value)
    if not match:
        return None
    return int(match.group(1))


@router.get('/delivery-notes', response_class=HTMLResponse)
def delivery_notes_page(request: Request):
    user = get_current_user(request)
    require_permission(user['id'], 'delivery_notes', 'view')
    with get_conn() as conn:
        rows = qall(
            conn,
            """SELECT
                   d.*,
                   u.display_name AS created_by_name,
                   (
                       SELECT COUNT(*)
                       FROM delivery_note_customer_groups g2
                       WHERE g2.delivery_note_id = d.id
                   ) AS position_count
               FROM delivery_notes d
               LEFT JOIN users u ON u.id = d.created_by_user_id
               WHERE d.status IN ('draft', 'saved')
               ORDER BY d.service_date DESC, d.id DESC""",
        )
        creators = qall(
            conn,
            "SELECT id, display_name FROM users WHERE is_active = 1 AND role_key IN ('owner', 'employee') ORDER BY display_name",
        )
    return render(request, 'delivery_notes.html', delivery_notes=rows, creators=creators)


@router.post('/delivery-notes')
def create_delivery_note(
    request: Request,
    service_date: str = Form(...),
    created_by_user_id: int | None = Form(None),
    note: str = Form(''),
):
    user = get_current_user(request)
    require_permission(user['id'], 'delivery_notes', 'create')
    if not service_date:
        raise HTTPException(status_code=400, detail='Leistungsdatum fehlt.')
    with get_conn() as conn:
        creator_id = created_by_user_id or user['id']
        creator = qone(conn, 'SELECT id, display_name FROM users WHERE id = ? AND is_active = 1', (creator_id,))
        if not creator:
            raise HTTPException(status_code=400, detail='Anlegender Benutzer wurde nicht gefunden.')
        location_id = _ensure_default_delivery_note_location(conn)
        base_number = f'{service_date} - {creator["display_name"]}'
        number = base_number
        suffix = 2
        while qone(conn, 'SELECT id FROM delivery_notes WHERE delivery_note_number = ?', (number,)):
            number = f'{base_number} ({suffix})'
            suffix += 1
        dn_id = execute(
            conn,
            'INSERT INTO delivery_notes (delivery_note_number, location_id, service_date, status, created_by_user_id, note, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (number, location_id, service_date, 'draft', creator['id'], note, now_ts(), now_ts()),
        )
    return RedirectResponse(f'/delivery-notes/{dn_id}', status_code=303)


@router.get('/delivery-notes/{delivery_note_id}', response_class=HTMLResponse)
def delivery_note_detail(request: Request, delivery_note_id: int):
    user = get_current_user(request)
    require_permission(user['id'], 'delivery_notes', 'view')
    with get_conn() as conn:
        dn = qone(
            conn,
            "SELECT d.*, u.display_name AS created_by_name FROM delivery_notes d LEFT JOIN users u ON u.id = d.created_by_user_id WHERE d.id = ?",
            (delivery_note_id,),
        )
        if not dn:
            raise HTTPException(status_code=404, detail='Tageslieferschein nicht gefunden.')
        if dn['status'] not in ('draft', 'saved'):
            raise HTTPException(status_code=400, detail='Dieser Tageslieferschein ist nicht mehr bearbeitbar.')
        groups_raw = qall(
            conn,
            """SELECT g.*, c.customer_number, c.first_name, c.last_name, c.company_name
               FROM delivery_note_customer_groups g
               JOIN customers c ON c.id = g.customer_id
               WHERE g.delivery_note_id = ?
               ORDER BY g.id""",
            (delivery_note_id,),
        )
        positions = []
        for idx, group in enumerate(groups_raw, start=1):
            group_entries = qall(
                conn,
                """SELECT
                       e.*,
                       h.name AS horse_name,
                       h.id AS horse_id,
                       l.name AS location_name
                   FROM delivery_note_entries e
                   JOIN horses h ON h.id = e.horse_id
                   LEFT JOIN locations l ON l.id = h.location_id
                   WHERE e.delivery_note_customer_group_id = ?
                   ORDER BY e.sort_order, e.id""",
                (group['id'],),
            )
            horses_map: dict[int, dict] = {}
            horses = []
            position_total = 0.0
            for entry in group_entries:
                hid = entry['horse_id']
                if hid not in horses_map:
                    horses_map[hid] = {
                        'horse_id': hid,
                        'horse_name': entry['horse_name'],
                        'location_name': entry['location_name'],
                        'services': [],
                        'horse_total': 0.0,
                    }
                    horses.append(horses_map[hid])
                line_total = float(entry['total_price_gross'] or 0)
                horses_map[hid]['services'].append(
                    {
                        'entry_id': entry['id'],
                        'name': entry['actual_service_name'],
                        'quantity': entry['quantity'],
                        'unit': entry['unit'],
                        'unit_price_gross': entry['unit_price_gross'],
                        'total_price_gross': entry['total_price_gross'],
                        'note': entry['note'],
                    }
                )
                horses_map[hid]['horse_total'] += line_total
                position_total += line_total
            positions.append(
                {
                    'position_no': idx,
                    'group_id': group['id'],
                    'customer_name': customer_label(group),
                    'customer_number': group['customer_number'],
                    'status': group['status'],
                    'note': group['note'],
                    'horses': horses,
                    'total': position_total,
                }
            )
        customers = qall(conn, 'SELECT * FROM customers ORDER BY customer_number LIMIT 500')
        horses_lookup = qall(
            conn,
            """SELECT
                   h.id,
                   h.name,
                   h.customer_id,
                   h.location_id,
                   c.customer_number,
                   c.first_name,
                   c.last_name,
                   c.company_name,
                   l.name AS location_name
               FROM horses h
               JOIN customers c ON c.id = h.customer_id
               LEFT JOIN locations l ON l.id = h.location_id
               ORDER BY h.name LIMIT 1000""",
        )
        locations_lookup = qall(conn, 'SELECT id, name, city FROM locations ORDER BY name LIMIT 500')
        service_templates = qall(
            conn,
            """SELECT
                   name,
                   default_quantity,
                   default_unit,
                   default_unit_price_gross,
                   default_vat_rate
               FROM service_templates
               WHERE is_active = 1
               ORDER BY name""",
        )
    return render(
        request,
        'delivery_note_detail.html',
        dn=dn,
        positions=positions,
        customers=customers,
        horses_lookup=horses_lookup,
        locations_lookup=locations_lookup,
        service_templates=service_templates,
        customer_label=customer_label,
    )


@router.post('/delivery-notes/{delivery_note_id}/items')
def add_items_to_delivery_note(
    request: Request,
    delivery_note_id: int,
    position_lookup: str = Form(...),
    owner_lookup: str = Form(''),
    payload_json: str = Form(...),
):
    user = get_current_user(request)
    require_permission(user['id'], 'delivery_notes', 'edit')
    with get_conn() as conn:
        dn = qone(conn, 'SELECT * FROM delivery_notes WHERE id = ?', (delivery_note_id,))
        if not dn:
            raise HTTPException(status_code=404, detail='Tageslieferschein nicht gefunden.')
        if dn['status'] not in ('draft', 'saved'):
            raise HTTPException(status_code=400, detail='Dieser Tageslieferschein ist nicht mehr bearbeitbar.')

        selected_customer_id = _parse_lookup_id(position_lookup, 'C')
        selected_horse_id = _parse_lookup_id(position_lookup, 'H')
        selected_location_id = _parse_lookup_id(position_lookup, 'L')
        chosen_owner_id = _parse_lookup_id(owner_lookup, 'C')

        customer_id: int | None = None
        if selected_customer_id:
            customer_id = selected_customer_id
        elif selected_horse_id:
            horse_from_lookup = qone(conn, 'SELECT customer_id FROM horses WHERE id = ?', (selected_horse_id,))
            if not horse_from_lookup:
                raise HTTPException(status_code=400, detail='Ausgewähltes Pferd nicht gefunden.')
            customer_id = horse_from_lookup['customer_id']
        elif selected_location_id:
            if not chosen_owner_id:
                raise HTTPException(status_code=400, detail='Bitte Besitzer aus der Stall-Liste auswählen.')
            customer_id = chosen_owner_id
        if not customer_id:
            raise HTTPException(status_code=400, detail='Bitte Kunde, Pferd oder Stall aus der Suche auswählen.')

        customer_exists = qone(conn, 'SELECT id FROM customers WHERE id = ?', (customer_id,))
        if not customer_exists:
            raise HTTPException(status_code=400, detail='Ausgewählter Besitzer wurde nicht gefunden.')

        group = qone(
            conn,
            "SELECT * FROM delivery_note_customer_groups WHERE delivery_note_id = ? AND customer_id = ? AND status IN ('draft', 'saved') ORDER BY id DESC LIMIT 1",
            (delivery_note_id, customer_id),
        )
        if group:
            group_id = group['id']
        else:
            group_id = execute(
                conn,
                'INSERT INTO delivery_note_customer_groups (delivery_note_id, customer_id, payment_method, status, note, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (delivery_note_id, customer_id, 'bank_transfer', 'draft', 'Automatisch über Schnell-Erfassung', now_ts(), now_ts()),
            )

        try:
            import json

            payload = json.loads(payload_json)
        except Exception:
            raise HTTPException(status_code=400, detail='Ungültige Positionsdaten.')
        horses_payload = payload.get('horses', []) if isinstance(payload, dict) else []
        if not horses_payload:
            raise HTTPException(status_code=400, detail='Bitte mindestens ein Pferd mit Leistung hinzufügen.')

        sort_order = qone(
            conn,
            'SELECT COALESCE(MAX(sort_order), 0) + 1 AS n FROM delivery_note_entries WHERE delivery_note_customer_group_id = ?',
            (group_id,),
        )['n']
        inserted = 0
        for horse_block in horses_payload:
            horse_lookup_value = (horse_block.get('horse_lookup') or '').strip()
            horse_id = _parse_lookup_id(horse_lookup_value, 'H')
            if not horse_id:
                continue
            horse = qone(conn, 'SELECT * FROM horses WHERE id = ?', (horse_id,))
            if not horse:
                continue
            if horse['customer_id'] != customer_id:
                raise HTTPException(status_code=400, detail='Das gewählte Pferd gehört nicht zum gewählten Besitzer.')
            services_payload = horse_block.get('services', [])
            if not isinstance(services_payload, list):
                continue
            for service in services_payload:
                current_service_name = str(service.get('service_name') or '').strip()
                if not current_service_name:
                    continue
                current_unit = str(service.get('unit') or 'Stk.').strip() or 'Stk.'
                current_note = str(service.get('line_note') or '').strip()
                try:
                    current_quantity = int(float(service.get('quantity') or 1))
                except Exception:
                    current_quantity = 1
                if current_quantity <= 0:
                    current_quantity = 1
                try:
                    current_price = float(service.get('unit_price_gross') or 0)
                except Exception:
                    current_price = 0.0
                total = round(current_quantity * current_price, 2)
                tpl = qone(conn, 'SELECT * FROM service_templates WHERE lower(name) = lower(?)', (current_service_name,))
                tpl_id = tpl['id'] if tpl else None
                current_vat = float((tpl['default_vat_rate'] if tpl else 19) or 19)
                execute(
                    conn,
                    'INSERT INTO delivery_note_entries (delivery_note_customer_group_id, horse_id, service_template_id, suggested_service_name, actual_service_name, quantity, unit, unit_price_gross, vat_rate, total_price_gross, note, sort_order, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (
                        group_id,
                        horse['id'],
                        tpl_id,
                        current_service_name,
                        current_service_name,
                        current_quantity,
                        current_unit,
                        current_price,
                        current_vat,
                        total,
                        current_note,
                        sort_order,
                        now_ts(),
                        now_ts(),
                    ),
                )
                sort_order += 1
                inserted += 1

        if inserted == 0:
            raise HTTPException(status_code=400, detail='Bitte mindestens eine Leistung angeben.')

        recompute_group_status(conn, group_id)
    return RedirectResponse(f'/delivery-notes/{delivery_note_id}', status_code=303)


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
    lookup_customer: str = Form(''),
    lookup_horse: str = Form(''),
    lookup_location: str = Form(''),
):
    user = get_current_user(request)
    require_permission(user['id'], 'delivery_notes', 'edit')
    with get_conn() as conn:
        customer_id = customer_id or _parse_lookup_id(lookup_customer, 'C')
        horse_lookup_id = _parse_lookup_id(lookup_horse, 'H')
        location_lookup_id = _parse_lookup_id(lookup_location, 'L')

        if not customer_id and horse_lookup_id:
            horse = qone(conn, 'SELECT id, customer_id FROM horses WHERE id = ?', (horse_lookup_id,))
            if horse:
                customer_id = horse['customer_id']

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
        if location_lookup_id:
            exists = qone(conn, 'SELECT id FROM locations WHERE id = ?', (location_lookup_id,))
            if not exists:
                raise HTTPException(status_code=400, detail='Ausgewählter Stall wurde nicht gefunden.')
        group_id = execute(conn, 'INSERT INTO delivery_note_customer_groups (delivery_note_id, customer_id, payment_method, status, note, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)', (delivery_note_id, customer_id, payment_method, 'draft', note, now_ts(), now_ts()))
        conn.execute('UPDATE delivery_notes SET status = ?, updated_at = ? WHERE id = ?', ('saved', now_ts(), delivery_note_id))
    redirect_url = f'/delivery-notes/{delivery_note_id}?focus_group={group_id}'
    if horse_lookup_id:
        redirect_url += f'&horse_id={horse_lookup_id}'
    return RedirectResponse(redirect_url, status_code=303)


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
