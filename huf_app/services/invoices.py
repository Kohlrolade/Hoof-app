"""Invoice, delivery note and numbering business logic."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import HTTPException

from ..db.core import execute, qall, qone
from ..utils.formatting import euro, fmt_date, now_ts, parse_float, today_str
from ..utils.labels import customer_label

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
