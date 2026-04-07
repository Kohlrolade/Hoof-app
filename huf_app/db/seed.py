"""Database schema creation and safe seed helpers.

Customer, horse, invoice and payment history remain excluded from the seed so a
fresh checkout never contains production-like business data.
"""

from __future__ import annotations

import sqlite3
from datetime import date

from ..config import SAMPLE_BANK_IMPORT_PATH
from ..constants import EMAIL_TEMPLATE_SEEDS, MODULES, SERVICE_TEMPLATE_SEEDS
from ..db.core import execute, get_conn, qone
from ..services.invoices import refresh_all_invoice_statuses
from ..utils.formatting import now_ts

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
