"""Bank import parsing and payment matching logic."""

from __future__ import annotations

import csv
import io
import re
import sqlite3

from fastapi import HTTPException

from ..db.core import execute, qone
from ..services.invoices import refresh_invoice
from ..utils.formatting import now_ts, parse_float, today_str

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
