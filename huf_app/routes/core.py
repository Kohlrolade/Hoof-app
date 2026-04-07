"""Core navigation and dashboard routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..db.core import get_conn, qall, qone
from ..presentation import render
from ..services.auth import get_current_user, require_permission
from ..services.invoices import refresh_all_invoice_statuses
from ..utils.labels import customer_label

router = APIRouter()

@router.get('/switch-user/{user_id}')
def switch_user(request: Request, user_id: int):
    with get_conn() as conn:
        user = qone(conn, 'SELECT * FROM users WHERE id = ? AND is_active = 1', (user_id,))
    if user:
        request.session['user_id'] = user_id
    return RedirectResponse('/', status_code=303)


@router.get('/favicon.ico')
def favicon():
    return RedirectResponse('/static/favicon.ico', status_code=307)


@router.get('/', response_class=HTMLResponse)
def dashboard(request: Request):
    user = get_current_user(request)
    require_permission(user['id'], 'dashboard', 'view')
    with get_conn() as conn:
        refresh_all_invoice_statuses(conn)
        stats = {
            'delivery_notes_open': qone(conn, "SELECT COUNT(*) AS c FROM delivery_notes WHERE status IN ('draft', 'saved', 'partially_invoiced')")['c'],
            'invoices_draft': qone(conn, "SELECT COUNT(*) AS c FROM invoices WHERE status IN ('draft', 'approved')")['c'],
            'invoices_overdue': qone(conn, "SELECT COUNT(*) AS c FROM invoices WHERE status = 'overdue'")['c'],
            'payments_unmatched': qone(conn, "SELECT COUNT(*) AS c FROM bank_transactions WHERE match_status IN ('unmatched', 'suggested')")['c'],
            'time_entries_open': qone(conn, "SELECT COUNT(*) AS c FROM time_entries WHERE status IN ('draft', 'submitted')")['c'],
        }
        recent_delivery_notes = qall(conn, "SELECT d.*, l.name AS location_name FROM delivery_notes d JOIN locations l ON l.id = d.location_id ORDER BY d.service_date DESC, d.id DESC LIMIT 8")
        recent_invoices = qall(conn, "SELECT i.*, c.first_name, c.last_name, c.company_name FROM invoices i JOIN customers c ON c.id = i.customer_id ORDER BY COALESCE(i.invoice_date, i.created_at) DESC, i.id DESC LIMIT 8")
        users = qall(conn, 'SELECT * FROM users WHERE is_active = 1 ORDER BY role_key, display_name')
    return render(request, 'dashboard.html', stats=stats, recent_delivery_notes=recent_delivery_notes, recent_invoices=recent_invoices, users=users, customer_label=customer_label)
