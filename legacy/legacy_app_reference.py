"""FastAPI application for the Hufschmied business workflow.

This file intentionally keeps the current single-file structure, but now includes
clearer seed logic, data-sanitizing helpers and environment-based configuration so
future refactoring into modules can happen safely from an IDE or coding agent.
"""

from __future__ import annotations

import csv
import io
import os
import re
import sqlite3
import smtplib
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from starlette.middleware.sessions import SessionMiddleware

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv('HUF_APP_DB_PATH', str(BASE_DIR / 'app.db')))
TEMPLATE_DIR = Path(os.getenv('HUF_APP_TEMPLATE_DIR', str(BASE_DIR / 'templates')))
STATIC_DIR = Path(os.getenv('HUF_APP_STATIC_DIR', str(BASE_DIR / 'static')))
PDF_DIR = Path(os.getenv('HUF_APP_PDF_DIR', str(BASE_DIR / 'generated_pdfs')))
SAMPLE_BANK_IMPORT_PATH = Path(os.getenv('HUF_APP_SAMPLE_BANK_IMPORT_PATH', str(BASE_DIR / 'sample_bank_import.csv')))
SESSION_SECRET = os.getenv('HUF_APP_SESSION_SECRET', 'change-me-in-production')

PDF_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title='Hufschmied Betriebs-App v1.0')
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)
app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

MODULES = [
    'dashboard', 'delivery_notes', 'invoices', 'payments', 'time_entries',
    'customers', 'horses', 'locations', 'settings'
]


def now_ts() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def today_str() -> str:
    return date.today().isoformat()


def euro(value: float | int | None) -> str:
    if value is None:
        return '-'
    return f"{float(value):,.2f} €".replace(',', 'X').replace('.', ',').replace('X', '.')


def fmt_date(value: str | None) -> str:
    if not value:
        return '-'
    try:
        value = value.split(' ')[0]
        y, m, d = value.split('-')
        return f'{d}.{m}.{y}'
    except Exception:
        return value


def parse_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == '':
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace('€', '').replace(' ', '')
    if ',' in text:
        text = text.replace('.', '').replace(',', '.')
    try:
        return float(text)
    except ValueError:
        return default


@contextmanager
def get_conn():
    """Provide a SQLite connection with commit/close handling."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def qone(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> sqlite3.Row | None:
    return conn.execute(sql, params).fetchone()


def qall(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


def execute(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    cur = conn.execute(sql, params)
    return cur.lastrowid


def normalize_name(first: str | None, last: str | None, company: str | None) -> str:
    company = (company or '').strip()
    if company:
        return company
    return ' '.join(part for part in [(first or '').strip(), (last or '').strip()] if part).strip()


def customer_label(row: sqlite3.Row | None) -> str:
    if not row:
        return '-'
    return normalize_name(row['first_name'], row['last_name'], row['company_name']) or f"Kunde {row['id']}"


def location_label(row: sqlite3.Row | None) -> str:
    if not row:
        return '-'
    city = f" ({row['city']})" if row['city'] else ''
    return f"{row['name']}{city}"


def get_current_user(request: Request) -> sqlite3.Row:
    user_id = request.session.get('user_id')
    with get_conn() as conn:
        user = None
        if user_id:
            user = qone(conn, 'SELECT * FROM users WHERE id = ? AND is_active = 1', (user_id,))
        if not user:
            user = qone(conn, "SELECT * FROM users WHERE role_key = 'owner' ORDER BY id LIMIT 1")
            if user:
                request.session['user_id'] = user['id']
        if not user:
            raise HTTPException(status_code=500, detail='Kein Benutzer vorhanden.')
        return user


def can(user_id: int, module_key: str, action: str) -> bool:
    if module_key not in MODULES:
        return False
    fields = {
        'view': 'can_view',
        'create': 'can_create',
        'edit': 'can_edit',
        'cancel': 'can_cancel',
        'approve': 'can_approve',
        'send': 'can_send',
        'manage_payments': 'can_manage_payments',
        'see_prices': 'can_see_prices',
        'edit_prices': 'can_edit_prices',
    }
    field = fields.get(action)
    if not field:
        return False
    with get_conn() as conn:
        row = qone(conn, f'SELECT {field} FROM permissions WHERE user_id = ? AND module_key = ?', (user_id, module_key))
        return bool(row[field]) if row else False


def require_permission(user_id: int, module_key: str, action: str) -> None:
    if not can(user_id, module_key, action):
        raise HTTPException(status_code=403, detail='Dafür fehlt die Berechtigung.')


def render(request: Request, template_name: str, **context):
    user = get_current_user(request)
    permissions = {}
    for m in MODULES:
        for a in ['view', 'create', 'edit', 'approve', 'send', 'manage_payments', 'see_prices', 'edit_prices']:
            permissions[f'{m}_{a}'] = can(user['id'], m, a)
    context.update({'request': request, 'user': user, 'permissions': permissions, 'euro': euro, 'fmt_date': fmt_date, 'today': today_str()})
    return templates.TemplateResponse(request, template_name, context)


def calc_invoice_status(conn: sqlite3.Connection, invoice_id: int) -> str:
    """Derive the current invoice status from approvals, due date and payments."""
    invoice = qone(conn, 'SELECT * FROM invoices WHERE id = ?', (invoice_id,))
    if not invoice:
        return 'draft'
    paid = qone(conn, 'SELECT COALESCE(SUM(amount), 0) AS total FROM invoice_payments WHERE invoice_id = ?', (invoice_id,))['total']
    gross = invoice['gross_total'] or 0
    if paid >= gross and gross > 0:
        return 'paid'
    if paid > 0:
        return 'partially_paid'
    if invoice['approved_at'] and invoice['due_date'] and invoice['due_date'] < today_str():
        return 'overdue'
    if invoice['sent_at']:
        return 'sent'
    if invoice['approved_at']:
        return 'approved'
    return 'draft'


def refresh_invoice(conn: sqlite3.Connection, invoice_id: int) -> None:
    lines = qall(conn, 'SELECT * FROM invoice_lines WHERE invoice_id = ? ORDER BY sort_order, id', (invoice_id,))
    gross = sum(float(line['line_total_gross'] or 0) for line in lines)
    vat_total = sum((float(line['line_total_gross'] or 0) * float(line['vat_rate'] or 19) / (100 + float(line['vat_rate'] or 19))) for line in lines)
    net_total = gross - vat_total
    status = calc_invoice_status(conn, invoice_id)
    conn.execute(
        'UPDATE invoices SET net_total = ?, vat_total = ?, gross_total = ?, status = ?, updated_at = ? WHERE id = ?',
        (round(net_total, 2), round(vat_total, 2), round(gross, 2), status, now_ts(), invoice_id),
    )


def refresh_all_invoice_statuses(conn: sqlite3.Connection) -> None:
    for row in qall(conn, 'SELECT id FROM invoices'):
        refresh_invoice(conn, row['id'])


def next_number(conn: sqlite3.Connection, sequence_key: str, prefix: str, year: int | None = None) -> str:
    year = year or date.today().year
    row = qone(conn, 'SELECT * FROM number_sequences WHERE sequence_key = ? AND year = ?', (sequence_key, year))
    if not row:
        execute(conn, 'INSERT INTO number_sequences (sequence_key, year, current_value, prefix, updated_at) VALUES (?, ?, ?, ?, ?)', (sequence_key, year, 0, prefix, now_ts()))
        row = qone(conn, 'SELECT * FROM number_sequences WHERE sequence_key = ? AND year = ?', (sequence_key, year))
    new_value = int(row['current_value'] or 0) + 1
    conn.execute('UPDATE number_sequences SET current_value = ?, updated_at = ? WHERE id = ?', (new_value, now_ts(), row['id']))
    return f'{prefix}-{year}-{new_value}'


def parse_standards(raw: str | None) -> list[tuple[str, float, str, float]]:
    if not raw:
        return []
    parts = [p.strip() for p in str(raw).split(';') if str(p).strip()]
    chunks: list[tuple[str, float, str, float]] = []
    i = 0
    while i + 3 < len(parts):
        service = parts[i]
        qty = parse_float(parts[i + 1], 1)
        unit = parts[i + 2]
        price = parse_float(parts[i + 3], 0)
        chunks.append((service, qty, unit, price))
        i += 4
    return chunks


def suggested_service_for_horse(conn: sqlite3.Connection, horse_id: int, customer_id: int | None = None) -> dict[str, Any]:
    latest = qone(
        conn,
        """SELECT actual_service_name, quantity, unit, unit_price_gross, vat_rate
           FROM delivery_note_entries
           WHERE horse_id = ?
           ORDER BY created_at DESC, id DESC
           LIMIT 1""",
        (horse_id,),
    )
    if latest:
        return {
            'service_name': latest['actual_service_name'],
            'quantity': latest['quantity'],
            'unit': latest['unit'],
            'unit_price_gross': latest['unit_price_gross'],
            'vat_rate': latest['vat_rate'],
            'source': 'letzte Leistung',
        }
    if customer_id:
        row = qone(
            conn,
            """SELECT st.name, csd.default_quantity, csd.default_unit, csd.default_unit_price_gross, csd.default_vat_rate
               FROM customer_service_defaults csd
               JOIN service_templates st ON st.id = csd.service_template_id
               WHERE csd.customer_id = ?
               ORDER BY csd.id LIMIT 1""",
            (customer_id,),
        )
        if row:
            return {
                'service_name': row['name'],
                'quantity': row['default_quantity'],
                'unit': row['default_unit'],
                'unit_price_gross': row['default_unit_price_gross'],
                'vat_rate': row['default_vat_rate'],
                'source': 'Kundenstandard',
            }
    tpl = qone(conn, 'SELECT * FROM service_templates WHERE is_active = 1 ORDER BY id LIMIT 1')
    if tpl:
        return {
            'service_name': tpl['name'],
            'quantity': tpl['default_quantity'],
            'unit': tpl['default_unit'],
            'unit_price_gross': tpl['default_unit_price_gross'],
            'vat_rate': tpl['default_vat_rate'],
            'source': 'allgemeine Vorlage',
        }
    return {'service_name': '', 'quantity': 1, 'unit': 'Stk.', 'unit_price_gross': 0, 'vat_rate': 19, 'source': 'leer'}


def group_total(conn: sqlite3.Connection, group_id: int) -> float:
    row = qone(conn, 'SELECT COALESCE(SUM(total_price_gross), 0) AS total FROM delivery_note_entries WHERE delivery_note_customer_group_id = ?', (group_id,))
    return float(row['total'] if row else 0)


def recompute_group_status(conn: sqlite3.Connection, group_id: int) -> None:
    group = qone(conn, 'SELECT * FROM delivery_note_customer_groups WHERE id = ?', (group_id,))
    if not group:
        return
    invoice_link = qone(conn, 'SELECT * FROM invoice_source_links WHERE delivery_note_customer_group_id = ?', (group_id,))
    if invoice_link:
        invoice = qone(conn, 'SELECT * FROM invoices WHERE id = ?', (invoice_link['invoice_id'],))
        status = 'paid_cash' if invoice and invoice['status'] == 'paid' and group['payment_method'] == 'cash' else 'invoiced'
    else:
        count = qone(conn, 'SELECT COUNT(*) AS c FROM delivery_note_entries WHERE delivery_note_customer_group_id = ?', (group_id,))['c']
        status = 'saved' if count else 'draft'
    conn.execute('UPDATE delivery_note_customer_groups SET status = ?, updated_at = ? WHERE id = ?', (status, now_ts(), group_id))
    dn_id = group['delivery_note_id']
    statuses = {r['status'] for r in qall(conn, 'SELECT status FROM delivery_note_customer_groups WHERE delivery_note_id = ?', (dn_id,))}
    dn_status = 'draft'
    if statuses and statuses.issubset({'invoiced', 'paid_cash'}):
        dn_status = 'fully_invoiced'
    elif 'invoiced' in statuses or 'paid_cash' in statuses:
        dn_status = 'partially_invoiced'
    elif 'saved' in statuses:
        dn_status = 'saved'
    elif statuses == {'cancelled'}:
        dn_status = 'cancelled'
    conn.execute('UPDATE delivery_notes SET status = ?, updated_at = ? WHERE id = ?', (dn_status, now_ts(), dn_id))


def create_invoice_draft_from_group(conn: sqlite3.Connection, group_id: int) -> int:
    """Create an invoice draft from one customer block of a delivery note."""
    link = qone(conn, 'SELECT * FROM invoice_source_links WHERE delivery_note_customer_group_id = ?', (group_id,))
    if link:
        return link['invoice_id']
    group = qone(
        conn,
        """SELECT g.*, d.service_date, d.id AS delivery_note_id
           FROM delivery_note_customer_groups g
           JOIN delivery_notes d ON d.id = g.delivery_note_id
           WHERE g.id = ?""",
        (group_id,),
    )
    if not group:
        raise HTTPException(status_code=404, detail='Kundenblock nicht gefunden.')
    customer = qone(conn, 'SELECT * FROM customers WHERE id = ?', (group['customer_id'],))
    payment_term = int(customer['default_payment_term_days'] or 14) if customer else 14
    invoice_id = execute(
        conn,
        """INSERT INTO invoices
           (invoice_number, customer_id, service_date, invoice_date, payment_term_days, due_date, status, net_total, vat_total, gross_total,
            pdf_path, approved_by_user_id, approved_at, sent_at, email_recipient, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            None, group['customer_id'], group['service_date'], today_str(), payment_term,
            (date.today() + timedelta(days=payment_term)).isoformat(), 'draft', 0, 0, 0, None, None, None, None,
            customer['email'] if customer else None, now_ts(), now_ts(),
        ),
    )
    entries = qall(conn, 'SELECT * FROM delivery_note_entries WHERE delivery_note_customer_group_id = ? ORDER BY sort_order, id', (group_id,))
    for idx, entry in enumerate(entries, start=1):
        horse = qone(conn, 'SELECT * FROM horses WHERE id = ?', (entry['horse_id'],))
        description = f"{horse['name']} – {entry['actual_service_name']}" if horse else entry['actual_service_name']
        execute(
            conn,
            """INSERT INTO invoice_lines
               (invoice_id, horse_id, description, quantity, unit, unit_price_gross, vat_rate, line_total_gross, sort_order, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (invoice_id, entry['horse_id'], description, entry['quantity'], entry['unit'], entry['unit_price_gross'], entry['vat_rate'], entry['total_price_gross'], idx, now_ts(), now_ts()),
        )
    execute(conn, 'INSERT INTO invoice_source_links (invoice_id, delivery_note_id, delivery_note_customer_group_id, created_at) VALUES (?, ?, ?, ?)', (invoice_id, group['delivery_note_id'], group_id, now_ts()))
    conn.execute('UPDATE delivery_note_customer_groups SET status = ?, updated_at = ? WHERE id = ?', ('invoice_draft', now_ts(), group_id))
    refresh_invoice(conn, invoice_id)
    return invoice_id


def generate_invoice_pdf(conn: sqlite3.Connection, invoice_id: int) -> str:
    """Render the current invoice into a PDF file inside the configured PDF directory."""
    invoice = qone(conn, 'SELECT * FROM invoices WHERE id = ?', (invoice_id,))
    customer = qone(conn, 'SELECT * FROM customers WHERE id = ?', (invoice['customer_id'],)) if invoice else None
    company = qone(conn, 'SELECT * FROM company_settings WHERE id = 1')
    lines = qall(conn, 'SELECT * FROM invoice_lines WHERE invoice_id = ? ORDER BY sort_order, id', (invoice_id,))
    if not invoice or not company:
        raise HTTPException(status_code=404, detail='Rechnung nicht gefunden.')
    file_name = f"{invoice['invoice_number'] or f'Entwurf-{invoice_id}'}.pdf".replace('/', '-')
    file_path = PDF_DIR / file_name
    c = canvas.Canvas(str(file_path), pagesize=A4)
    width, height = A4

    def draw_text(x_mm: float, y_mm: float, text: str, size: int = 10, bold: bool = False, align: str = 'left'):
        c.setFont('Helvetica-Bold' if bold else 'Helvetica', size)
        x = x_mm * mm
        y = height - y_mm * mm
        if align == 'right':
            c.drawRightString(x, y, text)
        elif align == 'center':
            c.drawCentredString(x, y, text)
        else:
            c.drawString(x, y, text)

    draw_text(15, 15, company['owner_name'] or company['company_name'] or 'Hufbeschlag', 20, True)
    draw_text(15, 22, company['company_name'] or '', 10)
    draw_text(15, 29, f"{company['street'] or ''} | {company['postal_code'] or ''} {company['city'] or ''}", 9)

    y = 50
    if customer:
        draw_text(15, y, customer_label(customer), 16, True)
        y += 8
        if customer['street']:
            draw_text(15, y, customer['street'], 12)
            y += 7
        city_line = ' '.join(filter(None, [customer['postal_code'], customer['city']]))
        if city_line:
            draw_text(15, y, city_line, 12)

    c.setFillColorRGB(0.95, 0.95, 0.95)
    c.rect(120 * mm, height - 78 * mm, 75 * mm, 38 * mm, fill=1, stroke=0)
    c.setFillColor(colors.black)
    draw_text(132, 52, 'Rechnung', 14)
    info = [
        ('Rechnungsnummer:', invoice['invoice_number'] or 'wird bei Freigabe vergeben'),
        ('Rechnungsdatum:', fmt_date(invoice['invoice_date'])),
        ('Leistungsdatum:', fmt_date(invoice['service_date'])),
        ('Kundennummer:', customer['customer_number'] if customer else '-'),
        ('Zahlungsziel:', f"{invoice['payment_term_days']} Tage"),
        ('Fälligkeitsdatum:', fmt_date(invoice['due_date'])),
    ]
    info_y = 60
    for label, value in info:
        draw_text(126, info_y, label, 10)
        draw_text(190, info_y, str(value), 10, False, 'right')
        info_y += 6

    top = 110
    c.line(10 * mm, height - top * mm, 200 * mm, height - top * mm)
    draw_text(15, top + 7, 'Bezeichnung', 11)
    draw_text(122, top + 7, 'Anzahl', 11, False, 'right')
    draw_text(140, top + 7, 'Einheit', 11, False, 'right')
    draw_text(168, top + 7, 'Einzelpreis', 11, False, 'right')
    draw_text(193, top + 7, 'Gesamtpreis', 11, False, 'right')
    draw_text(168, top + 13, 'Brutto', 9, False, 'right')
    draw_text(193, top + 13, 'Brutto', 9, False, 'right')
    c.line(10 * mm, height - (top + 16) * mm, 200 * mm, height - (top + 16) * mm)

    row_y = top + 27
    for line in lines:
        desc = line['description'] or ''
        wrapped = [desc[i:i+52] for i in range(0, len(desc), 52)] or ['']
        first_line_y = row_y
        for sub in wrapped:
            draw_text(15, row_y, sub, 10)
            row_y += 6
        draw_text(122, first_line_y, f"{line['quantity']}".replace('.', ','), 10, False, 'right')
        draw_text(140, first_line_y, line['unit'] or '', 10, False, 'right')
        draw_text(168, first_line_y, euro(line['unit_price_gross']), 10, False, 'right')
        draw_text(193, first_line_y, euro(line['line_total_gross']), 10, False, 'right')
        row_y += 4

    total_top = 230
    c.line(10 * mm, height - total_top * mm, 200 * mm, height - total_top * mm)
    draw_text(183, total_top + 7, 'Endbetrag', 14, False, 'right')
    draw_text(145, total_top + 18, 'Nettobetrag', 10, False, 'right')
    vat_label = f"mwst. {int((lines[0]['vat_rate'] if lines else 19) or 19)}%"
    draw_text(168, total_top + 18, vat_label, 10, False, 'right')
    draw_text(193, total_top + 18, 'Brutto', 10, False, 'right')
    c.line(10 * mm, height - (total_top + 22) * mm, 200 * mm, height - (total_top + 22) * mm)
    draw_text(145, total_top + 33, euro(invoice['net_total']), 12, False, 'right')
    draw_text(168, total_top + 33, euro(invoice['vat_total']), 12, False, 'right')
    draw_text(193, total_top + 33, euro(invoice['gross_total']), 12, True, 'right')

    foot_y = 275
    draw_text(15, foot_y, 'Kontaktinformation', 12, True)
    draw_text(85, foot_y, 'Kontodaten', 12, True)
    draw_text(155, foot_y, 'Überweisungsbetreff', 12, True)
    yy = foot_y + 7
    for item in [company['owner_name'], company['street'], f"{company['postal_code']} {company['city']}".strip(), company['phone'], f"Steuernummer {company['tax_number']}" if company['tax_number'] else '']:
        if item:
            draw_text(15, yy, item, 10)
            yy += 6
    yy = foot_y + 12
    for item in [company['bank_name'], company['iban'], company['bic']]:
        if item:
            draw_text(110, yy, item, 12, False, 'center')
            yy += 8
    ref_tpl = company['invoice_payment_reference_template'] or '{invoice_number} {last_name}'
    reference = ref_tpl.replace('{invoice_number}', invoice['invoice_number'] or '').replace('{last_name}', (customer['last_name'] or customer['company_name'] or '').strip() if customer else '')
    draw_text(190, foot_y + 14, reference, 12, False, 'right')
    c.save()
    conn.execute('UPDATE invoices SET pdf_path = ?, updated_at = ? WHERE id = ?', (str(file_path.relative_to(BASE_DIR)), now_ts(), invoice_id))
    return str(file_path)


def build_email_from_template(conn: sqlite3.Connection, template_key: str, invoice: sqlite3.Row, customer: sqlite3.Row | None) -> tuple[str, str]:
    template = qone(conn, 'SELECT * FROM email_templates WHERE template_key = ?', (template_key,))
    subject = template['subject_template'] if template else f"Rechnung {invoice['invoice_number']}"
    body = template['body_template'] if template else 'anbei erhalten Sie Ihre Rechnung.'
    customer_name = customer_label(customer) if customer else ''
    replacements = {
        '{invoice_number}': invoice['invoice_number'] or '',
        '{customer_name}': customer_name,
        '{due_date}': fmt_date(invoice['due_date']),
        '{gross_total}': euro(invoice['gross_total']),
    }
    for k, v in replacements.items():
        subject = subject.replace(k, v)
        body = body.replace(k, v)
    return subject, body


def send_invoice_email(conn: sqlite3.Connection, invoice_id: int, recipient: str, subject: str, body: str) -> tuple[bool, str]:
    invoice = qone(conn, 'SELECT * FROM invoices WHERE id = ?', (invoice_id,))
    if not invoice:
        return False, 'Rechnung nicht gefunden.'
    company = qone(conn, 'SELECT * FROM company_settings WHERE id = 1')
    pdf_path = BASE_DIR / (invoice['pdf_path'] or '')
    if not pdf_path.exists():
        generate_invoice_pdf(conn, invoice_id)
        pdf_path = BASE_DIR / (qone(conn, 'SELECT pdf_path FROM invoices WHERE id = ?', (invoice_id,))['pdf_path'])
    smtp_host = (company['smtp_host'] or '').strip() if company else ''
    smtp_port = int(company['smtp_port'] or 587) if company else 587
    smtp_username = (company['smtp_username'] or '').strip() if company else ''
    smtp_password = (company['smtp_password'] or '').strip() if company else ''
    smtp_use_tls = bool(company['smtp_use_tls']) if company else True
    sender = smtp_username or company['email'] or 'demo@example.com'

    msg = EmailMessage()
    msg['From'] = sender
    msg['To'] = recipient
    msg['Subject'] = subject
    msg.set_content(body)
    if pdf_path.exists():
        with open(pdf_path, 'rb') as f:
            msg.add_attachment(f.read(), maintype='application', subtype='pdf', filename=pdf_path.name)

    if smtp_host and smtp_username and smtp_password:
        try:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
                if smtp_use_tls:
                    server.starttls()
                server.login(smtp_username, smtp_password)
                server.send_message(msg)
            conn.execute('UPDATE invoices SET sent_at = ?, updated_at = ? WHERE id = ?', (now_ts(), now_ts(), invoice_id))
            execute(conn, 'INSERT INTO invoice_email_log (invoice_id, recipient_email, subject, body_text, sent_at, status, error_message) VALUES (?, ?, ?, ?, ?, ?, ?)', (invoice_id, recipient, subject, body, now_ts(), 'sent', None))
            refresh_invoice(conn, invoice_id)
            return True, 'E-Mail gesendet.'
        except Exception as exc:
            execute(conn, 'INSERT INTO invoice_email_log (invoice_id, recipient_email, subject, body_text, sent_at, status, error_message) VALUES (?, ?, ?, ?, ?, ?, ?)', (invoice_id, recipient, subject, body, now_ts(), 'error', str(exc)))
            return False, f'Versand fehlgeschlagen: {exc}'
    execute(conn, 'INSERT INTO invoice_email_log (invoice_id, recipient_email, subject, body_text, sent_at, status, error_message) VALUES (?, ?, ?, ?, ?, ?, ?)', (invoice_id, recipient, subject, body, now_ts(), 'dry_run', 'SMTP nicht konfiguriert'))
    return True, 'SMTP ist noch nicht konfiguriert. Die Mail wurde als Testlauf protokolliert.'


def import_bank_csv(conn: sqlite3.Connection, file_name: str, content: bytes, imported_by_user_id: int) -> int:
    text = content.decode('utf-8-sig', errors='ignore')
    lines = text.splitlines()
    sample = '\n'.join(lines[: min(3, len(lines))])
    dialect = csv.Sniffer().sniff(sample)
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    import_id = execute(conn, 'INSERT INTO bank_imports (file_name, imported_at, imported_by_user_id, status) VALUES (?, ?, ?, ?)', (file_name, now_ts(), imported_by_user_id, 'imported'))
    for raw in reader:
        normalized = {str(k).strip().lower(): (v or '').strip() for k, v in raw.items() if k}
        booking_date = normalized.get('booking_date') or normalized.get('buchungsdatum') or normalized.get('buchungstag') or normalized.get('datum')
        value_date = normalized.get('value_date') or normalized.get('wertstellung')
        amount = parse_float(normalized.get('amount') or normalized.get('betrag'))
        payer_name = normalized.get('payer_name') or normalized.get('zahler') or normalized.get('name zahlungspflichtiger') or normalized.get('beguenstigter / zahlungspflichtiger')
        iban = normalized.get('iban') or normalized.get('kontonummer/iban')
        purpose = normalized.get('purpose') or normalized.get('verwendungszweck') or normalized.get('buchungstext') or normalized.get('verwendungszweck / grund')
        matched_invoice_id = None
        match_status = 'unmatched'
        m = re.search(r'(R-\d{4}-\d+)', purpose or '')
        if m:
            invoice = qone(conn, 'SELECT id FROM invoices WHERE invoice_number = ?', (m.group(1),))
            if invoice:
                matched_invoice_id = invoice['id']
                match_status = 'suggested'
        execute(conn, 'INSERT INTO bank_transactions (bank_import_id, booking_date, value_date, amount, payer_name, iban, purpose, matched_invoice_id, match_status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (import_id, booking_date, value_date, amount, payer_name, iban, purpose, matched_invoice_id, match_status, now_ts()))
    return import_id


def confirm_transaction_match(conn: sqlite3.Connection, transaction_id: int, invoice_id: int, user_id: int) -> None:
    tx = qone(conn, 'SELECT * FROM bank_transactions WHERE id = ?', (transaction_id,))
    if not tx:
        raise HTTPException(status_code=404, detail='Buchung nicht gefunden.')
    if tx['match_status'] == 'confirmed':
        return
    execute(conn, 'INSERT INTO invoice_payments (invoice_id, bank_transaction_id, payment_date, amount, payment_method, note, created_by_user_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (invoice_id, transaction_id, tx['booking_date'] or today_str(), tx['amount'], 'bank_transfer', tx['purpose'], user_id, now_ts()))
    conn.execute('UPDATE bank_transactions SET matched_invoice_id = ?, match_status = ? WHERE id = ?', (invoice_id, 'confirmed', transaction_id))
    refresh_invoice(conn, invoice_id)


def seed_permissions_for_user(conn: sqlite3.Connection, user_id: int, role_key: str) -> None:
    """Create default permission rows for one user role."""
    fields = ['can_view', 'can_create', 'can_edit', 'can_cancel', 'can_approve', 'can_send', 'can_manage_payments', 'can_see_prices', 'can_edit_prices']
    rights = {m: {k: 0 for k in fields} for m in MODULES}
    if role_key == 'owner':
        for m in MODULES:
            for f in fields:
                rights[m][f] = 1
    elif role_key == 'office':
        for m in ['dashboard', 'customers', 'horses', 'locations', 'delivery_notes', 'invoices', 'payments', 'time_entries']:
            rights[m]['can_view'] = 1
        for m in ['customers', 'horses', 'locations', 'delivery_notes', 'invoices', 'payments', 'time_entries']:
            rights[m]['can_create'] = 1
            rights[m]['can_edit'] = 1
        rights['invoices']['can_approve'] = 1
        rights['invoices']['can_send'] = 1
        rights['payments']['can_manage_payments'] = 1
        rights['delivery_notes']['can_see_prices'] = 1
        rights['delivery_notes']['can_edit_prices'] = 1
    else:
        for m in ['dashboard', 'customers', 'horses', 'locations', 'delivery_notes', 'time_entries']:
            rights[m]['can_view'] = 1
        for m in ['customers', 'horses', 'locations', 'delivery_notes', 'time_entries']:
            rights[m]['can_create'] = 1
            rights[m]['can_edit'] = 1
        rights['delivery_notes']['can_see_prices'] = 1
        rights['delivery_notes']['can_edit_prices'] = 1
    for module_key, vals in rights.items():
        execute(conn, 'INSERT INTO permissions (user_id, module_key, can_view, can_create, can_edit, can_cancel, can_approve, can_send, can_manage_payments, can_see_prices, can_edit_prices) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (user_id, module_key, vals['can_view'], vals['can_create'], vals['can_edit'], vals['can_cancel'], vals['can_approve'], vals['can_send'], vals['can_manage_payments'], vals['can_see_prices'], vals['can_edit_prices']))



SERVICE_TEMPLATE_SEEDS: list[tuple[str, float, str, float, float]] = [
    ('Hufbeschlag 4 Eisen', 2, 'Stk.', 150.0, 19.0),
    ('Ledersohlen', 2, 'Stk.', 20.0, 19.0),
    ('Hufbeschlag 2 Eisen 2 Hufe Barhufkorrektur', 1, 'Stk.', 90.0, 19.0),
    ('orthopädisches Polster', 2, 'Stk.', 20.0, 19.0),
    ('4 Hufe Barhufkorrektur', 1, 'Stk.', 50.0, 19.0),
    ('Arbeitszeitpauschale', 1, 'Stk.', 35.0, 19.0),
    ('Materialpauschale', 1, 'Psch.', 150.0, 19.0),
    ('Anfahrtspauschale', 1, 'km', 0.5, 19.0),
    ('orthopädisches Eisen', 2, 'Stk.', 10.0, 19.0),
    ('Klebebeschlag', 2, 'Stk.', 100.0, 19.0),
    ('Anfahrt', 1, 'Psch.', 50.0, 19.0),
    ('Kunststoffsohlen', 2, 'Stk.', 0.0, 19.0),
    ('2 Hufe Klebebeschlag 2 Hufe Barhufkorrektur', 1, 'Stk.', 170.0, 19.0),
    ('orthopädische Einlagen', 4, 'Stk.', 5.0, 19.0),
    ('orthopädische Eisen', 2, 'Stk.', 10.0, 19.0),
    ('Hufbeschlag 2 Eisen', 1, 'Stk.', 90.0, 19.0),
]

EMAIL_TEMPLATE_SEEDS: list[tuple[str, str, str]] = [
    (
        'invoice_send',
        'Rechnung {invoice_number}',
        'Guten Tag {customer_name},\n\nanbei erhalten Sie Ihre Rechnung {invoice_number} über {gross_total}.\n\n'
        'Bitte überweisen Sie den Betrag bis spätestens {due_date}.\n\nViele Grüße\nMarvin Binder',
    ),
    (
        'payment_reminder_1',
        'Zahlungserinnerung zu Rechnung {invoice_number}',
        'Guten Tag {customer_name},\n\nzu unserer Rechnung {invoice_number} konnten wir bisher keinen Zahlungseingang '
        'feststellen.\nBitte prüfen Sie die Zahlung bis {due_date}.\n\nViele Grüße\nMarvin Binder',
    ),
]


def ensure_sample_bank_import_template() -> None:
    """Keep only a blank import template in the repository."""
    if not SAMPLE_BANK_IMPORT_PATH.exists():
        SAMPLE_BANK_IMPORT_PATH.write_text(
            'booking_date,value_date,amount,payer_name,iban,purpose\n',
            encoding='utf-8',
        )


def seed_service_templates(conn: sqlite3.Connection) -> None:
    """Seed generic services only.

    Intentionally no horse names, customer names or invoice-derived lines are seeded.
    """
    for name, qty, unit, price, vat in SERVICE_TEMPLATE_SEEDS:
        execute(
            conn,
            'INSERT OR IGNORE INTO service_templates (name, default_quantity, default_unit, default_unit_price_gross, default_vat_rate, description, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (name, qty, unit, price, vat, None, 1, now_ts(), now_ts()),
        )


def seed_base_reference_data(conn: sqlite3.Connection) -> None:
    """Seed non-sensitive reference data used by the application UI."""
    for tpl_key, subject, body in EMAIL_TEMPLATE_SEEDS:
        execute(
            conn,
            'INSERT OR IGNORE INTO email_templates (template_key, subject_template, body_template, updated_at) VALUES (?, ?, ?, ?)',
            (tpl_key, subject, body, now_ts()),
        )
    seed_service_templates(conn)
    for key, prefix in [('invoice', 'R'), ('delivery_note', 'LS')]:
        execute(
            conn,
            'INSERT OR IGNORE INTO number_sequences (sequence_key, year, current_value, prefix, updated_at) VALUES (?, ?, ?, ?, ?)',
            (key, date.today().year, 0, prefix, now_ts()),
        )
    ensure_sample_bank_import_template()


def seed_database(conn: sqlite3.Connection) -> None:
    """Create a clean starter database without any customer or invoice history."""
    owner_id = execute(
        conn,
        'INSERT INTO users (display_name, email, role_key, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)',
        ('Marvin Binder', 'marvin.binder@outlook.de', 'owner', 1, now_ts(), now_ts()),
    )
    office_id = execute(
        conn,
        'INSERT INTO users (display_name, email, role_key, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)',
        ('Büro', 'buero@example.local', 'office', 1, now_ts(), now_ts()),
    )
    employee_id = execute(
        conn,
        'INSERT INTO users (display_name, email, role_key, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)',
        ('Mitarbeiter Demo', 'mitarbeiter@example.local', 'employee', 1, now_ts(), now_ts()),
    )
    for uid, role in [(owner_id, 'owner'), (office_id, 'office'), (employee_id, 'employee')]:
        seed_permissions_for_user(conn, uid, role)
    execute(
        conn,
        'INSERT INTO company_settings (id, company_name, owner_name, street, postal_code, city, phone, email, tax_number, bank_name, iban, bic, invoice_footer_text, invoice_payment_reference_template, default_payment_term_days, smtp_host, smtp_port, smtp_username, smtp_password, smtp_use_tls, updated_at) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (
            'Hufbeschlag',
            'Marvin Binder',
            'Elsenborner Str. 102',
            '52156',
            'Monschau',
            '+49 (0)170-3096222',
            'marvin.binder@outlook.de',
            '202/5027/1798',
            'Postbank',
            'DE92 2501 0030 0630 9813 07',
            'PBNKDEFF',
            'Vielen Dank für Ihren Auftrag.',
            '{invoice_number} {last_name}',
            14,
            '',
            587,
            '',
            '',
            1,
            now_ts(),
        ),
    )
    seed_base_reference_data(conn)


def clear_business_data(conn: sqlite3.Connection, reset_sequences: bool = True) -> None:
    """Delete customer-related data while keeping owner, users and company settings."""
    tables_in_delete_order = [
        'invoice_email_log',
        'invoice_payments',
        'payment_reminders',
        'bank_transactions',
        'bank_imports',
        'invoice_source_links',
        'invoice_lines',
        'invoices',
        'delivery_note_entries',
        'delivery_note_customer_groups',
        'delivery_notes',
        'customer_service_defaults',
        'horses',
        'locations',
        'customers',
    ]
    for table_name in tables_in_delete_order:
        conn.execute(f'DELETE FROM {table_name}')
    if reset_sequences:
        conn.execute("DELETE FROM number_sequences WHERE sequence_key IN ('invoice', 'delivery_note')")
        for key, prefix in [('invoice', 'R'), ('delivery_note', 'LS')]:
            execute(
                conn,
                'INSERT INTO number_sequences (sequence_key, year, current_value, prefix, updated_at) VALUES (?, ?, ?, ?, ?)',
                (key, date.today().year, 0, prefix, now_ts()),
            )
    allowed_templates = [item[0] for item in SERVICE_TEMPLATE_SEEDS]
    placeholders = ','.join('?' for _ in allowed_templates)
    conn.execute(f'DELETE FROM service_templates WHERE name NOT IN ({placeholders})', tuple(allowed_templates))
    ensure_sample_bank_import_template()


def init_db() -> None:
    """Create the schema and seed only the safe starter data on first boot."""
    with get_conn() as conn:
        conn.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, display_name TEXT NOT NULL, email TEXT UNIQUE, role_key TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT, updated_at TEXT);
            CREATE TABLE IF NOT EXISTS permissions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, module_key TEXT NOT NULL, can_view INTEGER NOT NULL DEFAULT 0, can_create INTEGER NOT NULL DEFAULT 0, can_edit INTEGER NOT NULL DEFAULT 0, can_cancel INTEGER NOT NULL DEFAULT 0, can_approve INTEGER NOT NULL DEFAULT 0, can_send INTEGER NOT NULL DEFAULT 0, can_manage_payments INTEGER NOT NULL DEFAULT 0, can_see_prices INTEGER NOT NULL DEFAULT 0, can_edit_prices INTEGER NOT NULL DEFAULT 0, UNIQUE(user_id, module_key));
            CREATE TABLE IF NOT EXISTS customers (id INTEGER PRIMARY KEY AUTOINCREMENT, customer_number TEXT UNIQUE, type TEXT, first_name TEXT, last_name TEXT, company_name TEXT, street TEXT, postal_code TEXT, city TEXT, email TEXT, phone TEXT, default_payment_term_days INTEGER DEFAULT 14, note TEXT, is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT, updated_at TEXT);
            CREATE TABLE IF NOT EXISTS locations (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, street TEXT, postal_code TEXT, city TEXT, contact_person TEXT, phone TEXT, note TEXT, is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT, updated_at TEXT);
            CREATE TABLE IF NOT EXISTS horses (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, customer_id INTEGER NOT NULL, location_id INTEGER, note TEXT, is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT, updated_at TEXT);
            CREATE TABLE IF NOT EXISTS service_templates (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, default_quantity REAL, default_unit TEXT, default_unit_price_gross REAL, default_vat_rate REAL, description TEXT, is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT, updated_at TEXT);
            CREATE TABLE IF NOT EXISTS customer_service_defaults (id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER NOT NULL, service_template_id INTEGER NOT NULL, default_quantity REAL, default_unit TEXT, default_unit_price_gross REAL, default_vat_rate REAL, note TEXT, created_at TEXT, updated_at TEXT, UNIQUE(customer_id, service_template_id));
            CREATE TABLE IF NOT EXISTS company_settings (id INTEGER PRIMARY KEY CHECK (id = 1), company_name TEXT, owner_name TEXT, street TEXT, postal_code TEXT, city TEXT, phone TEXT, email TEXT, tax_number TEXT, bank_name TEXT, iban TEXT, bic TEXT, invoice_footer_text TEXT, invoice_payment_reference_template TEXT, default_payment_term_days INTEGER, smtp_host TEXT, smtp_port INTEGER, smtp_username TEXT, smtp_password TEXT, smtp_use_tls INTEGER DEFAULT 1, updated_at TEXT);
            CREATE TABLE IF NOT EXISTS number_sequences (id INTEGER PRIMARY KEY AUTOINCREMENT, sequence_key TEXT NOT NULL, year INTEGER NOT NULL, current_value INTEGER NOT NULL DEFAULT 0, prefix TEXT NOT NULL, updated_at TEXT, UNIQUE(sequence_key, year));
            CREATE TABLE IF NOT EXISTS email_templates (id INTEGER PRIMARY KEY AUTOINCREMENT, template_key TEXT UNIQUE, subject_template TEXT, body_template TEXT, updated_at TEXT);
            CREATE TABLE IF NOT EXISTS delivery_notes (id INTEGER PRIMARY KEY AUTOINCREMENT, delivery_note_number TEXT UNIQUE, location_id INTEGER NOT NULL, service_date TEXT NOT NULL, status TEXT NOT NULL, created_by_user_id INTEGER NOT NULL, note TEXT, created_at TEXT, updated_at TEXT);
            CREATE TABLE IF NOT EXISTS delivery_note_customer_groups (id INTEGER PRIMARY KEY AUTOINCREMENT, delivery_note_id INTEGER NOT NULL, customer_id INTEGER NOT NULL, payment_method TEXT NOT NULL, status TEXT NOT NULL, note TEXT, created_at TEXT, updated_at TEXT);
            CREATE TABLE IF NOT EXISTS delivery_note_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, delivery_note_customer_group_id INTEGER NOT NULL, horse_id INTEGER NOT NULL, service_template_id INTEGER, suggested_service_name TEXT, actual_service_name TEXT NOT NULL, quantity REAL NOT NULL, unit TEXT NOT NULL, unit_price_gross REAL NOT NULL, vat_rate REAL NOT NULL, total_price_gross REAL NOT NULL, note TEXT, sort_order INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT);
            CREATE TABLE IF NOT EXISTS invoices (id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_number TEXT UNIQUE, customer_id INTEGER NOT NULL, service_date TEXT NOT NULL, invoice_date TEXT NOT NULL, payment_term_days INTEGER NOT NULL DEFAULT 14, due_date TEXT NOT NULL, status TEXT NOT NULL, net_total REAL NOT NULL DEFAULT 0, vat_total REAL NOT NULL DEFAULT 0, gross_total REAL NOT NULL DEFAULT 0, pdf_path TEXT, approved_by_user_id INTEGER, approved_at TEXT, sent_at TEXT, email_recipient TEXT, created_at TEXT, updated_at TEXT);
            CREATE TABLE IF NOT EXISTS invoice_lines (id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER NOT NULL, horse_id INTEGER, description TEXT NOT NULL, quantity REAL NOT NULL, unit TEXT NOT NULL, unit_price_gross REAL NOT NULL, vat_rate REAL NOT NULL, line_total_gross REAL NOT NULL, sort_order INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT);
            CREATE TABLE IF NOT EXISTS invoice_source_links (id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER NOT NULL, delivery_note_id INTEGER NOT NULL, delivery_note_customer_group_id INTEGER NOT NULL, created_at TEXT, UNIQUE(delivery_note_customer_group_id));
            CREATE TABLE IF NOT EXISTS invoice_email_log (id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER NOT NULL, recipient_email TEXT, subject TEXT, body_text TEXT, sent_at TEXT, status TEXT, error_message TEXT);
            CREATE TABLE IF NOT EXISTS bank_imports (id INTEGER PRIMARY KEY AUTOINCREMENT, file_name TEXT NOT NULL, imported_at TEXT, imported_by_user_id INTEGER, status TEXT);
            CREATE TABLE IF NOT EXISTS bank_transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, bank_import_id INTEGER, booking_date TEXT, value_date TEXT, amount REAL, payer_name TEXT, iban TEXT, purpose TEXT, matched_invoice_id INTEGER, match_status TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS invoice_payments (id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER NOT NULL, bank_transaction_id INTEGER, payment_date TEXT NOT NULL, amount REAL NOT NULL, payment_method TEXT NOT NULL, note TEXT, created_by_user_id INTEGER, created_at TEXT);
            CREATE TABLE IF NOT EXISTS payment_reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER NOT NULL, reminder_level INTEGER NOT NULL DEFAULT 1, suggested_at TEXT, approved_by_user_id INTEGER, approved_at TEXT, sent_at TEXT, status TEXT NOT NULL, email_subject TEXT, email_body TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS time_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, entry_date TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL, break_minutes INTEGER NOT NULL DEFAULT 0, work_minutes INTEGER NOT NULL DEFAULT 0, note TEXT, status TEXT NOT NULL, created_at TEXT, updated_at TEXT);
            """
        )
        count = qone(conn, 'SELECT COUNT(*) AS c FROM users')['c']
        if count == 0:
            seed_database(conn)
        refresh_all_invoice_statuses(conn)


@app.on_event('startup')
def startup_event():
    init_db()


@app.get('/switch-user/{user_id}')
def switch_user(request: Request, user_id: int):
    with get_conn() as conn:
        user = qone(conn, 'SELECT * FROM users WHERE id = ? AND is_active = 1', (user_id,))
    if user:
        request.session['user_id'] = user_id
    return RedirectResponse('/', status_code=303)



@app.get('/favicon.ico')
def favicon():
    return RedirectResponse('/static/favicon.ico', status_code=307)

@app.get('/', response_class=HTMLResponse)
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


@app.get('/customers', response_class=HTMLResponse)
def customers_page(request: Request, q: str | None = None):
    user = get_current_user(request)
    require_permission(user['id'], 'customers', 'view')
    with get_conn() as conn:
        if q:
            rows = qall(conn, "SELECT * FROM customers WHERE (coalesce(first_name,'') || ' ' || coalesce(last_name,'') || ' ' || coalesce(company_name,'')) LIKE ? OR customer_number LIKE ? ORDER BY customer_number", (f'%{q}%', f'%{q}%'))
        else:
            rows = qall(conn, 'SELECT * FROM customers ORDER BY customer_number LIMIT 300')
    return render(request, 'customers.html', customers=rows, q=q or '', customer_label=customer_label)


@app.post('/customers')
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
        if not customer_number:
            current = qone(conn, 'SELECT COUNT(*) AS c FROM customers')['c'] + 1000
            customer_number = f'K-{current}'
        execute(conn, 'INSERT INTO customers (customer_number, type, first_name, last_name, company_name, street, postal_code, city, email, phone, default_payment_term_days, note, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (customer_number, type, first_name or None, last_name or None, company_name or None, street, postal_code, city, email or None, phone or None, 14, note or None, 1, now_ts(), now_ts()))
    return RedirectResponse('/customers', status_code=303)


@app.get('/locations', response_class=HTMLResponse)
def locations_page(request: Request):
    user = get_current_user(request)
    require_permission(user['id'], 'locations', 'view')
    with get_conn() as conn:
        rows = qall(conn, 'SELECT * FROM locations ORDER BY name')
    return render(request, 'locations.html', locations=rows)


@app.post('/locations')
def create_location(request: Request, name: str = Form(...), street: str = Form(''), postal_code: str = Form(''), city: str = Form(''), contact_person: str = Form(''), phone: str = Form(''), note: str = Form('')):
    user = get_current_user(request)
    require_permission(user['id'], 'locations', 'create')
    with get_conn() as conn:
        execute(conn, 'INSERT INTO locations (name, street, postal_code, city, contact_person, phone, note, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (name, street, postal_code, city, contact_person, phone, note, 1, now_ts(), now_ts()))
    return RedirectResponse('/locations', status_code=303)


@app.get('/horses', response_class=HTMLResponse)
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


@app.post('/horses')
def create_horse(request: Request, name: str = Form(...), customer_id: int = Form(...), location_id: int | None = Form(None), note: str = Form('')):
    user = get_current_user(request)
    require_permission(user['id'], 'horses', 'create')
    with get_conn() as conn:
        execute(conn, 'INSERT INTO horses (name, customer_id, location_id, note, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)', (name, customer_id, location_id, note, 1, now_ts(), now_ts()))
    return RedirectResponse('/horses', status_code=303)


@app.get('/delivery-notes', response_class=HTMLResponse)
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


@app.post('/delivery-notes')
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


@app.get('/delivery-notes/{delivery_note_id}', response_class=HTMLResponse)
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


@app.post('/delivery-notes/{delivery_note_id}/groups')
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
            current = qone(conn, 'SELECT COUNT(*) AS c FROM customers')['c'] + 1000
            customer_number = f'K-{current}'
            customer_id = execute(conn, 'INSERT INTO customers (customer_number, type, first_name, last_name, company_name, street, postal_code, city, email, phone, default_payment_term_days, note, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (customer_number, new_type, new_first_name or None, new_last_name or None, new_company_name or None, new_street, new_postal_code, new_city, new_email or None, new_phone or None, 14, 'Schnellanlage aus Tageslieferschein', 1, now_ts(), now_ts()))
        execute(conn, 'INSERT INTO delivery_note_customer_groups (delivery_note_id, customer_id, payment_method, status, note, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)', (delivery_note_id, customer_id, payment_method, 'draft', note, now_ts(), now_ts()))
        conn.execute('UPDATE delivery_notes SET status = ?, updated_at = ? WHERE id = ?', ('saved', now_ts(), delivery_note_id))
    return RedirectResponse(f'/delivery-notes/{delivery_note_id}', status_code=303)


@app.post('/groups/{group_id}/entries')
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


@app.get('/entries/{entry_id}/edit', response_class=HTMLResponse)
def edit_entry_form(request: Request, entry_id: int):
    user = get_current_user(request)
    require_permission(user['id'], 'delivery_notes', 'edit')
    with get_conn() as conn:
        entry = qone(conn, 'SELECT e.*, h.name AS horse_name, g.customer_id, g.delivery_note_id FROM delivery_note_entries e JOIN horses h ON h.id = e.horse_id JOIN delivery_note_customer_groups g ON g.id = e.delivery_note_customer_group_id WHERE e.id = ?', (entry_id,))
        if not entry:
            raise HTTPException(status_code=404, detail='Eintrag nicht gefunden.')
    return render(request, 'entry_edit.html', entry=entry)


@app.post('/entries/{entry_id}/edit')
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


@app.post('/entries/{entry_id}/delete')
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


@app.post('/groups/{group_id}/invoice-draft')
def create_invoice_draft_route(request: Request, group_id: int):
    user = get_current_user(request)
    require_permission(user['id'], 'invoices', 'create')
    with get_conn() as conn:
        invoice_id = create_invoice_draft_from_group(conn, group_id)
    return RedirectResponse(f'/invoices/{invoice_id}', status_code=303)


@app.get('/api/horse-suggestion/{horse_id}')
def horse_suggestion(horse_id: int, customer_id: int | None = None):
    with get_conn() as conn:
        return suggested_service_for_horse(conn, horse_id, customer_id)


@app.get('/invoices', response_class=HTMLResponse)
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


@app.get('/invoices/{invoice_id}', response_class=HTMLResponse)
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


@app.post('/invoices/{invoice_id}/approve')
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


@app.post('/invoices/{invoice_id}/send')
def send_invoice(request: Request, invoice_id: int, recipient: str = Form(...), subject: str = Form(...), body: str = Form(...)):
    user = get_current_user(request)
    require_permission(user['id'], 'invoices', 'send')
    with get_conn() as conn:
        ok, message = send_invoice_email(conn, invoice_id, recipient, subject, body)
    return RedirectResponse(f'/invoices/{invoice_id}?msg={"ok" if ok else "error"}', status_code=303)


@app.get('/invoices/{invoice_id}/pdf')
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


@app.post('/invoices/{invoice_id}/payments')
def manual_payment(request: Request, invoice_id: int, payment_date: str = Form(...), amount: float = Form(...), payment_method: str = Form(...), note: str = Form('')):
    user = get_current_user(request)
    require_permission(user['id'], 'payments', 'manage_payments')
    with get_conn() as conn:
        execute(conn, 'INSERT INTO invoice_payments (invoice_id, bank_transaction_id, payment_date, amount, payment_method, note, created_by_user_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (invoice_id, None, payment_date, amount, payment_method, note, user['id'], now_ts()))
        refresh_invoice(conn, invoice_id)
    return RedirectResponse(f'/invoices/{invoice_id}', status_code=303)


@app.get('/payments', response_class=HTMLResponse)
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


@app.post('/payments/import')
async def payments_import(request: Request, file: UploadFile = File(...)):
    user = get_current_user(request)
    require_permission(user['id'], 'payments', 'manage_payments')
    content = await file.read()
    with get_conn() as conn:
        import_bank_csv(conn, file.filename or 'import.csv', content, user['id'])
    return RedirectResponse('/payments', status_code=303)


@app.post('/payments/transactions/{transaction_id}/confirm')
def confirm_transaction(request: Request, transaction_id: int, invoice_id: int = Form(...)):
    user = get_current_user(request)
    require_permission(user['id'], 'payments', 'manage_payments')
    with get_conn() as conn:
        confirm_transaction_match(conn, transaction_id, invoice_id, user['id'])
    return RedirectResponse('/payments', status_code=303)


@app.post('/payments/transactions/{transaction_id}/ignore')
def ignore_transaction(request: Request, transaction_id: int):
    user = get_current_user(request)
    require_permission(user['id'], 'payments', 'manage_payments')
    with get_conn() as conn:
        conn.execute("UPDATE bank_transactions SET match_status = 'ignored' WHERE id = ?", (transaction_id,))
    return RedirectResponse('/payments', status_code=303)


@app.post('/payments/reminders/{invoice_id}')
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


@app.get('/time-entries', response_class=HTMLResponse)
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


@app.post('/time-entries')
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


@app.get('/settings', response_class=HTMLResponse)
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


@app.post('/settings/company')
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


@app.post('/settings/templates/{template_id}')
def update_template(request: Request, template_id: int, subject_template: str = Form(...), body_template: str = Form(...)):
    user = get_current_user(request)
    require_permission(user['id'], 'settings', 'edit')
    with get_conn() as conn:
        conn.execute('UPDATE email_templates SET subject_template = ?, body_template = ?, updated_at = ? WHERE id = ?', (subject_template, body_template, now_ts(), template_id))
    return RedirectResponse('/settings', status_code=303)


@app.post('/settings/permissions/{user_id}')
def update_permissions(request: Request, user_id: int):
    current_user = get_current_user(request)
    require_permission(current_user['id'], 'settings', 'edit')
    form = request._form if hasattr(request, '_form') else None
    raise HTTPException(status_code=405, detail='Bitte das HTML-Formular der Berechtigungsseite verwenden.')
