"""Remove customer-related business data from the local SQLite database."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from huf_app.config import PDF_DIR, SAMPLE_BANK_IMPORT_PATH
from huf_app.db.core import get_conn
from huf_app.db.seed import clear_business_data, init_db


def remove_generated_pdfs() -> int:
    removed = 0
    for pdf_file in PDF_DIR.glob('*.pdf'):
        pdf_file.unlink(missing_ok=True)
        removed += 1
    return removed


def reset_sample_bank_import() -> None:
    SAMPLE_BANK_IMPORT_PATH.write_text(
        'booking_date,value_date,amount,payer_name,iban,purpose\n',
        encoding='utf-8',
    )


def main() -> None:
    init_db()
    with get_conn() as conn:
        before = {
            'customers': conn.execute('SELECT COUNT(*) FROM customers').fetchone()[0],
            'horses': conn.execute('SELECT COUNT(*) FROM horses').fetchone()[0],
            'delivery_notes': conn.execute('SELECT COUNT(*) FROM delivery_notes').fetchone()[0],
            'invoices': conn.execute('SELECT COUNT(*) FROM invoices').fetchone()[0],
        }
        clear_business_data(conn)
        after = {
            'customers': conn.execute('SELECT COUNT(*) FROM customers').fetchone()[0],
            'horses': conn.execute('SELECT COUNT(*) FROM horses').fetchone()[0],
            'delivery_notes': conn.execute('SELECT COUNT(*) FROM delivery_notes').fetchone()[0],
            'invoices': conn.execute('SELECT COUNT(*) FROM invoices').fetchone()[0],
        }
    pdf_count = remove_generated_pdfs()
    reset_sample_bank_import()
    print('Bereinigung abgeschlossen.')
    print('Vorher:', before)
    print('Nachher:', after)
    print(f'Entfernte PDFs: {pdf_count}')


if __name__ == '__main__':
    main()
