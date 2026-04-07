"""Invoice review, approval, PDF and manual payment routes."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from ..config import BASE_DIR
from ..db.core import execute, get_conn, qall, qone
from ..presentation import render
from ..services.auth import get_current_user, require_permission
from ..services.invoices import next_number, refresh_all_invoice_statuses, refresh_invoice, recompute_group_status
from ..services.mail_service import build_email_from_template, send_invoice_email
from ..services.pdf_service import generate_invoice_pdf
from ..utils.formatting import now_ts
from ..utils.labels import customer_label

router = APIRouter()

@router.get('/invoices', response_class=HTMLResponse)
def invoices_page(request: Request, status: str | None = None):
    user = get_current_user(request)
    require_permission(user['id'], 'invoices', 'view')
    with get_conn() as conn:
        refresh_all_invoice_statuses(conn)
        sql = "SELECT i.*, c.first_name, c.last_name, c.company_name FROM invoices i JOIN customers c ON c.id = i.customer_id"
        params: tuple = ()
        if status:
            sql += ' WHERE i.status = ?'
            params = (status,)
        sql += ' ORDER BY COALESCE(i.invoice_date, i.created_at) DESC, i.id DESC'
        rows = qall(conn, sql, params)
    return render(request, 'invoices.html', invoices=rows, status_filter=status or '', customer_label=customer_label)


@router.get('/invoices/{invoice_id}', response_class=HTMLResponse)
def invoice_detail(request: Request, invoice_id: int):
    user = get_current_user(request)
    require_permission(user['id'], 'invoices', 'view')
    with get_conn() as conn:
        refresh_invoice(conn, invoice_id)
        invoice = qone(conn, 'SELECT * FROM invoices WHERE id = ?', (invoice_id,))
        if not invoice:
            raise HTTPException(status_code=404, detail='Rechnung nicht gefunden.')
        customer = qone(conn, 'SELECT * FROM customers WHERE id = ?', (invoice['customer_id'],))
        lines = qall(conn, 'SELECT * FROM invoice_lines WHERE invoice_id = ? ORDER BY sort_order, id', (invoice_id,))
        payments = qall(conn, 'SELECT * FROM invoice_payments WHERE invoice_id = ? ORDER BY payment_date DESC, id DESC', (invoice_id,))
        logs = qall(conn, 'SELECT * FROM invoice_email_log WHERE invoice_id = ? ORDER BY id DESC', (invoice_id,))
        subject, body = build_email_from_template(conn, 'invoice_send', invoice, customer)
        group_link = qone(conn, 'SELECT * FROM invoice_source_links WHERE invoice_id = ?', (invoice_id,))
    return render(request, 'invoice_detail.html', invoice=invoice, customer=customer, lines=lines, payments=payments, logs=logs, email_subject=subject, email_body=body, group_link=group_link, customer_label=customer_label)


@router.post('/invoices/{invoice_id}/approve')
def approve_invoice(request: Request, invoice_id: int, invoice_date: str = Form(...), payment_term_days: int = Form(...)):
    user = get_current_user(request)
    require_permission(user['id'], 'invoices', 'approve')
    with get_conn() as conn:
        invoice = qone(conn, 'SELECT * FROM invoices WHERE id = ?', (invoice_id,))
        if not invoice:
            raise HTTPException(status_code=404, detail='Rechnung nicht gefunden.')
        due_date = (datetime.strptime(invoice_date, '%Y-%m-%d').date() + timedelta(days=payment_term_days)).isoformat()
        number = invoice['invoice_number'] or next_number(conn, 'invoice', 'R', int(invoice_date[:4]))
        conn.execute('UPDATE invoices SET invoice_number = ?, invoice_date = ?, payment_term_days = ?, due_date = ?, approved_by_user_id = ?, approved_at = ?, updated_at = ? WHERE id = ?', (number, invoice_date, payment_term_days, due_date, user['id'], now_ts(), now_ts(), invoice_id))
        refresh_invoice(conn, invoice_id)
        generate_invoice_pdf(conn, invoice_id)
        link = qone(conn, 'SELECT * FROM invoice_source_links WHERE invoice_id = ?', (invoice_id,))
        if link:
            group = qone(conn, 'SELECT * FROM delivery_note_customer_groups WHERE id = ?', (link['delivery_note_customer_group_id'],))
            status = 'invoiced'
            if group and group['payment_method'] == 'cash':
                execute(conn, 'INSERT INTO invoice_payments (invoice_id, bank_transaction_id, payment_date, amount, payment_method, note, created_by_user_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (invoice_id, None, invoice_date, invoice['gross_total'], 'cash', 'Barzahlung aus Tageslieferschein', user['id'], now_ts()))
                refresh_invoice(conn, invoice_id)
                status = 'paid_cash'
            conn.execute('UPDATE delivery_note_customer_groups SET status = ?, updated_at = ? WHERE id = ?', (status, now_ts(), link['delivery_note_customer_group_id']))
            recompute_group_status(conn, link['delivery_note_customer_group_id'])
    return RedirectResponse(f'/invoices/{invoice_id}', status_code=303)


@router.post('/invoices/{invoice_id}/send')
def send_invoice(request: Request, invoice_id: int, recipient: str = Form(...), subject: str = Form(...), body: str = Form(...)):
    user = get_current_user(request)
    require_permission(user['id'], 'invoices', 'send')
    with get_conn() as conn:
        ok, message = send_invoice_email(conn, invoice_id, recipient, subject, body)
    return RedirectResponse(f'/invoices/{invoice_id}?msg={"ok" if ok else "error"}', status_code=303)


@router.get('/invoices/{invoice_id}/pdf')
def invoice_pdf(request: Request, invoice_id: int):
    user = get_current_user(request)
    require_permission(user['id'], 'invoices', 'view')
    with get_conn() as conn:
        invoice = qone(conn, 'SELECT * FROM invoices WHERE id = ?', (invoice_id,))
        if not invoice:
            raise HTTPException(status_code=404, detail='Rechnung nicht gefunden.')
        if not invoice['pdf_path'] or not (BASE_DIR / invoice['pdf_path']).exists():
            generate_invoice_pdf(conn, invoice_id)
            invoice = qone(conn, 'SELECT * FROM invoices WHERE id = ?', (invoice_id,))
        return FileResponse(BASE_DIR / invoice['pdf_path'], media_type='application/pdf', filename=Path(invoice['pdf_path']).name)


@router.post('/invoices/{invoice_id}/payments')
def manual_payment(request: Request, invoice_id: int, payment_date: str = Form(...), amount: float = Form(...), payment_method: str = Form(...), note: str = Form('')):
    user = get_current_user(request)
    require_permission(user['id'], 'payments', 'manage_payments')
    with get_conn() as conn:
        execute(conn, 'INSERT INTO invoice_payments (invoice_id, bank_transaction_id, payment_date, amount, payment_method, note, created_by_user_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (invoice_id, None, payment_date, amount, payment_method, note, user['id'], now_ts()))
        refresh_invoice(conn, invoice_id)
    return RedirectResponse(f'/invoices/{invoice_id}', status_code=303)
