"""Recreate a fresh local database without customer data."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from huf_app.config import DB_PATH, PDF_DIR, SAMPLE_BANK_IMPORT_PATH
from huf_app.db.seed import init_db


def main() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
    for pdf_file in PDF_DIR.glob('*.pdf'):
        pdf_file.unlink(missing_ok=True)
    SAMPLE_BANK_IMPORT_PATH.write_text(
        'booking_date,value_date,amount,payer_name,iban,purpose\n',
        encoding='utf-8',
    )
    init_db()
    print(f'Neue saubere Datenbank erstellt: {DB_PATH}')


if __name__ == '__main__':
    main()
