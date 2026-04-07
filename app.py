"""Compatibility entrypoint.

The old project used ``uvicorn app:app --reload``. This file keeps that command
working while the real implementation now lives inside the structured package.
"""

from huf_app.factory import create_app
from huf_app.config import (
    BASE_DIR,
    DB_PATH,
    PDF_DIR,
    SAMPLE_BANK_IMPORT_PATH,
    SESSION_SECRET,
    STATIC_DIR,
    TEMPLATE_DIR,
)
from huf_app.db.core import execute, get_conn, qall, qone
from huf_app.db.seed import clear_business_data, init_db
from huf_app.services.invoices import refresh_all_invoice_statuses, refresh_invoice
from huf_app.services.pdf_service import generate_invoice_pdf

app = create_app()
