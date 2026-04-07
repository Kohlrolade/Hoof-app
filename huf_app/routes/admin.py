"""Time entry and settings routes."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..constants import MODULES
from ..db.core import execute, get_conn, qall, qone
from ..presentation import render
from ..services.auth import get_current_user, require_permission
from ..utils.formatting import now_ts

router = APIRouter()

@router.get('/time-entries', response_class=HTMLResponse)
def time_entries_page(request: Request):
    user = get_current_user(request)
    require_permission(user['id'], 'time_entries', 'view')
    with get_conn() as conn:
        users = qall(conn, 'SELECT * FROM users WHERE is_active = 1 ORDER BY display_name')
        if user['role_key'] == 'employee':
            entries = qall(conn, 'SELECT t.*, u.display_name FROM time_entries t JOIN users u ON u.id = t.user_id WHERE t.user_id = ? ORDER BY entry_date DESC, id DESC LIMIT 120', (user['id'],))
        else:
            entries = qall(conn, 'SELECT t.*, u.display_name FROM time_entries t JOIN users u ON u.id = t.user_id ORDER BY entry_date DESC, id DESC LIMIT 200')
    return render(request, 'time_entries.html', entries=entries, users=users)


@router.post('/time-entries')
def create_time_entry(request: Request, user_id: int = Form(...), entry_date: str = Form(...), start_time: str = Form(...), end_time: str = Form(...), break_minutes: int = Form(0), note: str = Form('')):
    user = get_current_user(request)
    require_permission(user['id'], 'time_entries', 'create')
    if user['role_key'] == 'employee':
        user_id = user['id']
    fmt = '%H:%M'
    delta = datetime.strptime(end_time, fmt) - datetime.strptime(start_time, fmt)
    minutes = max(0, int(delta.total_seconds() // 60) - int(break_minutes))
    with get_conn() as conn:
        execute(conn, 'INSERT INTO time_entries (user_id, entry_date, start_time, end_time, break_minutes, work_minutes, note, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (user_id, entry_date, start_time, end_time, break_minutes, minutes, note, 'submitted' if user['role_key'] == 'employee' else 'approved', now_ts(), now_ts()))
    return RedirectResponse('/time-entries', status_code=303)


@router.get('/settings', response_class=HTMLResponse)
def settings_page(request: Request):
    user = get_current_user(request)
    require_permission(user['id'], 'settings', 'view')
    with get_conn() as conn:
        company = qone(conn, 'SELECT * FROM company_settings WHERE id = 1')
        users = qall(conn, 'SELECT * FROM users WHERE is_active = 1 ORDER BY role_key, display_name')
        perms = qall(conn, 'SELECT * FROM permissions ORDER BY user_id, module_key')
        templates_rows = qall(conn, 'SELECT * FROM email_templates ORDER BY template_key')
    perms_map: dict[int, dict[str, sqlite3.Row]] = {}
    for p in perms:
        perms_map.setdefault(p['user_id'], {})[p['module_key']] = p
    return render(request, 'settings.html', company=company, users=users, perms_map=perms_map, modules=MODULES, templates_rows=templates_rows)


@router.post('/settings/company')
def update_company_settings(
    request: Request,
    company_name: str = Form(...),
    owner_name: str = Form(...),
    street: str = Form(''),
    postal_code: str = Form(''),
    city: str = Form(''),
    phone: str = Form(''),
    email: str = Form(''),
    tax_number: str = Form(''),
    bank_name: str = Form(''),
    iban: str = Form(''),
    bic: str = Form(''),
    invoice_payment_reference_template: str = Form('{invoice_number} {last_name}'),
    default_payment_term_days: int = Form(14),
    smtp_host: str = Form(''),
    smtp_port: int = Form(587),
    smtp_username: str = Form(''),
    smtp_password: str = Form(''),
    smtp_use_tls: str = Form('1'),
):
    user = get_current_user(request)
    require_permission(user['id'], 'settings', 'edit')
    with get_conn() as conn:
        conn.execute('UPDATE company_settings SET company_name = ?, owner_name = ?, street = ?, postal_code = ?, city = ?, phone = ?, email = ?, tax_number = ?, bank_name = ?, iban = ?, bic = ?, invoice_payment_reference_template = ?, default_payment_term_days = ?, smtp_host = ?, smtp_port = ?, smtp_username = ?, smtp_password = ?, smtp_use_tls = ?, updated_at = ? WHERE id = 1', (company_name, owner_name, street, postal_code, city, phone, email, tax_number, bank_name, iban, bic, invoice_payment_reference_template, default_payment_term_days, smtp_host, smtp_port, smtp_username, smtp_password, 1 if smtp_use_tls == '1' else 0, now_ts()))
    return RedirectResponse('/settings', status_code=303)


@router.post('/settings/templates/{template_id}')
def update_template(request: Request, template_id: int, subject_template: str = Form(...), body_template: str = Form(...)):
    user = get_current_user(request)
    require_permission(user['id'], 'settings', 'edit')
    with get_conn() as conn:
        conn.execute('UPDATE email_templates SET subject_template = ?, body_template = ?, updated_at = ? WHERE id = ?', (subject_template, body_template, now_ts(), template_id))
    return RedirectResponse('/settings', status_code=303)


@router.post('/settings/permissions/{user_id}')
def update_permissions(request: Request, user_id: int):
    current_user = get_current_user(request)
    require_permission(current_user['id'], 'settings', 'edit')
    form = request._form if hasattr(request, '_form') else None
    raise HTTPException(status_code=405, detail='Bitte das HTML-Formular der Berechtigungsseite verwenden.')
