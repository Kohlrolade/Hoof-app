"""Customer, location and horse master data routes."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..db.core import execute, get_conn, qall
from ..presentation import render
from ..services.auth import get_current_user, require_permission
from ..services.customers import create_customer as create_customer_record
from ..utils.formatting import now_ts
from ..utils.labels import customer_label, normalize_name

router = APIRouter()

@router.get('/customers', response_class=HTMLResponse)
def customers_page(request: Request, q: str | None = None):
    user = get_current_user(request)
    require_permission(user['id'], 'customers', 'view')
    with get_conn() as conn:
        if q:
            rows = qall(conn, "SELECT * FROM customers WHERE (coalesce(first_name,'') || ' ' || coalesce(last_name,'') || ' ' || coalesce(company_name,'')) LIKE ? OR customer_number LIKE ? ORDER BY customer_number", (f'%{q}%', f'%{q}%'))
        else:
            rows = qall(conn, 'SELECT * FROM customers ORDER BY customer_number LIMIT 300')
    return render(request, 'customers.html', customers=rows, q=q or '', customer_label=customer_label)


@router.post('/customers')
def create_customer(
    request: Request,
    customer_number: str = Form(''),
    type: str = Form('private'),
    first_name: str = Form(''),
    last_name: str = Form(''),
    company_name: str = Form(''),
    street: str = Form(''),
    postal_code: str = Form(''),
    city: str = Form(''),
    email: str = Form(''),
    phone: str = Form(''),
    note: str = Form(''),
):
    user = get_current_user(request)
    require_permission(user['id'], 'customers', 'create')
    if not normalize_name(first_name, last_name, company_name):
        raise HTTPException(status_code=400, detail='Name oder Firma fehlt.')
    with get_conn() as conn:
        create_customer_record(
            conn,
            customer_number=customer_number,
            type=type,
            first_name=first_name,
            last_name=last_name,
            company_name=company_name,
            street=street,
            postal_code=postal_code,
            city=city,
            email=email,
            phone=phone,
            default_payment_term_days=14,
            note=note,
        )
    return RedirectResponse('/customers', status_code=303)


@router.get('/locations', response_class=HTMLResponse)
def locations_page(request: Request):
    user = get_current_user(request)
    require_permission(user['id'], 'locations', 'view')
    with get_conn() as conn:
        rows = qall(conn, 'SELECT * FROM locations ORDER BY name')
    return render(request, 'locations.html', locations=rows)


@router.post('/locations')
def create_location(request: Request, name: str = Form(...), street: str = Form(''), postal_code: str = Form(''), city: str = Form(''), contact_person: str = Form(''), phone: str = Form(''), note: str = Form('')):
    user = get_current_user(request)
    require_permission(user['id'], 'locations', 'create')
    with get_conn() as conn:
        execute(conn, 'INSERT INTO locations (name, street, postal_code, city, contact_person, phone, note, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (name, street, postal_code, city, contact_person, phone, note, 1, now_ts(), now_ts()))
    return RedirectResponse('/locations', status_code=303)


@router.get('/horses', response_class=HTMLResponse)
def horses_page(request: Request, customer_id: int | None = None):
    user = get_current_user(request)
    require_permission(user['id'], 'horses', 'view')
    with get_conn() as conn:
        customers = qall(conn, 'SELECT * FROM customers ORDER BY customer_number LIMIT 300')
        if customer_id:
            rows = qall(conn, "SELECT h.*, c.first_name, c.last_name, c.company_name, l.name AS location_name FROM horses h JOIN customers c ON c.id = h.customer_id LEFT JOIN locations l ON l.id = h.location_id WHERE h.customer_id = ? ORDER BY h.name", (customer_id,))
        else:
            rows = qall(conn, "SELECT h.*, c.first_name, c.last_name, c.company_name, l.name AS location_name FROM horses h JOIN customers c ON c.id = h.customer_id LEFT JOIN locations l ON l.id = h.location_id ORDER BY h.name LIMIT 300")
        locations = qall(conn, 'SELECT * FROM locations ORDER BY name')
    return render(request, 'horses.html', horses=rows, customers=customers, locations=locations, selected_customer_id=customer_id, customer_label=customer_label)


@router.post('/horses')
def create_horse(request: Request, name: str = Form(...), customer_id: int = Form(...), location_id: int | None = Form(None), note: str = Form('')):
    user = get_current_user(request)
    require_permission(user['id'], 'horses', 'create')
    with get_conn() as conn:
        execute(conn, 'INSERT INTO horses (name, customer_id, location_id, note, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)', (name, customer_id, location_id, note, 1, now_ts(), now_ts()))
    return RedirectResponse('/horses', status_code=303)
