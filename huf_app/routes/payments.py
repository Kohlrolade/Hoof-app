"""Bank import, matching and reminder routes."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from ..db.core import execute, get_conn, qall, qone
from ..presentation import render
from ..services.auth import get_current_user, require_permission
from ..services.invoices import refresh_all_invoice_statuses
from ..services.mail_service import build_email_from_template, send_invoice_email
from ..services.payment_service import confirm_transaction_match, import_bank_csv
from ..utils.formatting import now_ts
from ..utils.labels import customer_label

router = APIRouter()

@router.get('/payments', response_class=HTMLResponse)
def payments_page(request: Request):
    user = get_current_user(request)
    require_permission(user['id'], 'payments', 'view')
    with get_conn() as conn:
        refresh_all_invoice_statuses(conn)
        open_invoices = qall(conn, "SELECT i.*, c.first_name, c.last_name, c.company_name FROM invoices i JOIN customers c ON c.id = i.customer_id WHERE i.status IN ('approved', 'sent', 'partially_paid', 'overdue') ORDER BY i.due_date ASC, i.id DESC")
        transactions = qall(conn, 'SELECT * FROM bank_transactions ORDER BY id DESC LIMIT 80')
        suggestions = qall(conn, "SELECT bt.*, i.invoice_number FROM bank_transactions bt LEFT JOIN invoices i ON i.id = bt.matched_invoice_id WHERE bt.match_status IN ('suggested', 'unmatched') ORDER BY bt.id DESC LIMIT 80")
        reminder_candidates = qall(conn, "SELECT i.*, c.first_name, c.last_name, c.company_name FROM invoices i JOIN customers c ON c.id = i.customer_id WHERE i.status = 'overdue' ORDER BY i.due_date ASC")
    return render(request, 'payments.html', open_invoices=open_invoices, transactions=transactions, suggestions=suggestions, reminder_candidates=reminder_candidates, customer_label=customer_label)


@router.post('/payments/import')
async def payments_import(request: Request, file: UploadFile = File(...)):
    user = get_current_user(request)
    require_permission(user['id'], 'payments', 'manage_payments')
    content = await file.read()
    with get_conn() as conn:
        import_bank_csv(conn, file.filename or 'import.csv', content, user['id'])
    return RedirectResponse('/payments', status_code=303)


@router.post('/payments/transactions/{transaction_id}/confirm')
def confirm_transaction(request: Request, transaction_id: int, invoice_id: int = Form(...)):
    user = get_current_user(request)
    require_permission(user['id'], 'payments', 'manage_payments')
    with get_conn() as conn:
        confirm_transaction_match(conn, transaction_id, invoice_id, user['id'])
    return RedirectResponse('/payments', status_code=303)


@router.post('/payments/transactions/{transaction_id}/ignore')
def ignore_transaction(request: Request, transaction_id: int):
    user = get_current_user(request)
    require_permission(user['id'], 'payments', 'manage_payments')
    with get_conn() as conn:
        conn.execute("UPDATE bank_transactions SET match_status = 'ignored' WHERE id = ?", (transaction_id,))
    return RedirectResponse('/payments', status_code=303)


@router.post('/payments/reminders/{invoice_id}')
def create_or_send_reminder(request: Request, invoice_id: int):
    user = get_current_user(request)
    require_permission(user['id'], 'payments', 'manage_payments')
    with get_conn() as conn:
        invoice = qone(conn, 'SELECT * FROM invoices WHERE id = ?', (invoice_id,))
        customer = qone(conn, 'SELECT * FROM customers WHERE id = ?', (invoice['customer_id'],)) if invoice else None
        if not invoice or not customer:
            raise HTTPException(status_code=404, detail='Rechnung nicht gefunden.')
        subject, body = build_email_from_template(conn, 'payment_reminder_1', invoice, customer)
        reminder = qone(conn, 'SELECT * FROM payment_reminders WHERE invoice_id = ? AND status IN ("suggested", "approved") ORDER BY id DESC LIMIT 1', (invoice_id,))
        if not reminder:
            execute(conn, 'INSERT INTO payment_reminders (invoice_id, reminder_level, suggested_at, approved_by_user_id, approved_at, sent_at, status, email_subject, email_body, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (invoice_id, 1, now_ts(), user['id'], now_ts(), None, 'approved', subject, body, now_ts()))
            reminder = qone(conn, 'SELECT * FROM payment_reminders WHERE invoice_id = ? ORDER BY id DESC LIMIT 1', (invoice_id,))
        ok, _ = send_invoice_email(conn, invoice_id, customer['email'] or '', reminder['email_subject'], reminder['email_body'])
        conn.execute('UPDATE payment_reminders SET sent_at = ?, status = ?, approved_by_user_id = ?, approved_at = ? WHERE id = ?', (now_ts(), 'sent' if ok else 'approved', user['id'], now_ts(), reminder['id']))
    return RedirectResponse('/payments', status_code=303)
