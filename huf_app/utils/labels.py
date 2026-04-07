"""Human-readable labels for customers, horses and locations."""

from __future__ import annotations

import sqlite3

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
