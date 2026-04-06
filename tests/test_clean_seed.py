from __future__ import annotations

import importlib.util
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path


class CleanSeedTest(unittest.TestCase):
    def test_init_db_seeds_no_customer_data(self) -> None:
        root_dir = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / 'test.db'
            pdf_dir = Path(tmp_dir) / 'generated_pdfs'
            pdf_dir.mkdir(parents=True, exist_ok=True)

            os.environ['HUF_APP_DB_PATH'] = str(db_path)
            os.environ['HUF_APP_PDF_DIR'] = str(pdf_dir)
            os.environ['HUF_APP_SAMPLE_BANK_IMPORT_PATH'] = str(Path(tmp_dir) / 'sample_bank_import.csv')

            spec = importlib.util.spec_from_file_location('app_under_test', root_dir / 'app.py')
            module = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            spec.loader.exec_module(module)

            module.init_db()

            conn = sqlite3.connect(db_path)
            try:
                customers = conn.execute('SELECT COUNT(*) FROM customers').fetchone()[0]
                horses = conn.execute('SELECT COUNT(*) FROM horses').fetchone()[0]
                invoices = conn.execute('SELECT COUNT(*) FROM invoices').fetchone()[0]
                users = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
                services = conn.execute('SELECT COUNT(*) FROM service_templates').fetchone()[0]
            finally:
                conn.close()

            self.assertEqual(customers, 0)
            self.assertEqual(horses, 0)
            self.assertEqual(invoices, 0)
            self.assertGreaterEqual(users, 1)
            self.assertGreaterEqual(services, 1)


if __name__ == '__main__':
    unittest.main()
