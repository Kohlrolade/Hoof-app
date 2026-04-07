from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


class CleanSeedTest(unittest.TestCase):
    def _load_module(self, root_dir: Path, tmp_dir: Path):
        db_path = tmp_dir / 'test.db'
        pdf_dir = tmp_dir / 'generated_pdfs'
        pdf_dir.mkdir(parents=True, exist_ok=True)

        os.environ['HUF_APP_DB_PATH'] = str(db_path)
        os.environ['HUF_APP_PDF_DIR'] = str(pdf_dir)
        os.environ['HUF_APP_SAMPLE_BANK_IMPORT_PATH'] = str(tmp_dir / 'sample_bank_import.csv')

        for name in list(sys.modules):
            if name == 'app_under_test' or name == 'huf_app' or name.startswith('huf_app.'):
                sys.modules.pop(name, None)

        spec = importlib.util.spec_from_file_location('app_under_test', root_dir / 'app.py')
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        return module, db_path

    def test_init_db_seeds_no_customer_data(self) -> None:
        root_dir = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp_dir_name:
            tmp_dir = Path(tmp_dir_name)
            module, db_path = self._load_module(root_dir, tmp_dir)
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

    def test_dashboard_smoke(self) -> None:
        root_dir = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp_dir_name:
            tmp_dir = Path(tmp_dir_name)
            module, _ = self._load_module(root_dir, tmp_dir)
            module.init_db()

            client = TestClient(module.app)
            response = client.get('/')
            self.assertEqual(response.status_code, 200)
            response = client.get('/customers')
            self.assertEqual(response.status_code, 200)
            response = client.get('/settings')
            self.assertEqual(response.status_code, 200)


if __name__ == '__main__':
    unittest.main()
