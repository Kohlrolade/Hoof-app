"""Formatting and parsing helpers shared by routes and services."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

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
