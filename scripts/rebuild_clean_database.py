"""Recreate a fresh local database without customer data."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import app  # noqa: E402


def main() -> None:
    if app.DB_PATH.exists():
        app.DB_PATH.unlink()
    for pdf_file in app.PDF_DIR.glob('*.pdf'):
        pdf_file.unlink(missing_ok=True)
    app.SAMPLE_BANK_IMPORT_PATH.write_text(
        'booking_date,value_date,amount,payer_name,iban,purpose\n',
        encoding='utf-8',
    )
    app.init_db()
    print(f'Neue saubere Datenbank erstellt: {app.DB_PATH}')


if __name__ == '__main__':
    main()
