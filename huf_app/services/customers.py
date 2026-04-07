"""Customer creation helpers shared by multiple route modules."""

from __future__ import annotations

import sqlite3

from ..db.core import execute, qone
from ..utils.formatting import now_ts


def create_customer(
    conn: sqlite3.Connection,
    *,
    customer_number: str = '',
    type: str = 'private',
    first_name: str = '',
    last_name: str = '',
    company_name: str = '',
    street: str = '',
    postal_code: str = '',
    city: str = '',
    email: str = '',
    phone: str = '',
    default_payment_term_days: int = 14,
    note: str | None = None,
) -> int:
    """Insert one customer row and return its id."""
    if not customer_number:
        current = qone(conn, 'SELECT COUNT(*) AS c FROM customers')['c'] + 1000
        customer_number = f'K-{current}'

    return execute(
        conn,
        'INSERT INTO customers (customer_number, type, first_name, last_name, company_name, street, postal_code, city, email, phone, default_payment_term_days, note, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (
            customer_number,
            type,
            first_name or None,
            last_name or None,
            company_name or None,
            street,
            postal_code,
            city,
            email or None,
            phone or None,
            default_payment_term_days,
            note or None,
            1,
            now_ts(),
            now_ts(),
        ),
    )
