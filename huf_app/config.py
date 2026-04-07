"""Central runtime configuration.

This module keeps file paths, environment variables and application-wide options
in one place so they are no longer spread across a monolithic entry file.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv('HUF_APP_DB_PATH', str(BASE_DIR / 'app.db')))
TEMPLATE_DIR = Path(os.getenv('HUF_APP_TEMPLATE_DIR', str(BASE_DIR / 'templates')))
STATIC_DIR = Path(os.getenv('HUF_APP_STATIC_DIR', str(BASE_DIR / 'static')))
PDF_DIR = Path(os.getenv('HUF_APP_PDF_DIR', str(BASE_DIR / 'generated_pdfs')))
SAMPLE_BANK_IMPORT_PATH = Path(os.getenv('HUF_APP_SAMPLE_BANK_IMPORT_PATH', str(BASE_DIR / 'sample_bank_import.csv')))
SESSION_SECRET = os.getenv('HUF_APP_SESSION_SECRET', 'change-me-in-production')
APP_TITLE = os.getenv('HUF_APP_TITLE', 'Hufschmied Betriebs-App v2.0')
DEBUG = os.getenv('HUF_APP_DEBUG', '0') == '1'

# Ensure runtime folders exist before the app starts serving requests.
PDF_DIR.mkdir(parents=True, exist_ok=True)
