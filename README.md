# Huf-App – strukturierte Projektversion

Diese Version ist eine **saubere, modularisierte Kopie** deiner zuletzt bereinigten App.
Der alte Monolith wurde nicht gelöscht, sondern als Referenz unter `legacy/legacy_app_reference.py` abgelegt.

## Neue Struktur

```text
huf_app/
  config.py
  constants.py
  factory.py
  presentation.py
  schemas.py
  db/
    core.py
    seed.py
  services/
    auth.py
    invoices.py
    mail_service.py
    payment_service.py
    pdf_service.py
  routes/
    core.py
    master_data.py
    delivery_notes.py
    invoices.py
    payments.py
    admin.py
  utils/
    formatting.py
    labels.py
app.py
main.py
scripts/
tests/
legacy/
```

## Starten

```bash
python -m venv .venv
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python scripts/rebuild_clean_database.py
python -m uvicorn app:app --reload
```

Die alte Gewohnheit `uvicorn app:app --reload` bleibt absichtlich erhalten.

## Was bewusst drin geblieben ist

- deine eigenen Firmendaten in `company_settings`
- Benutzer/Rollen und Rechte
- generische Leistungsvorlagen
- neutrale E-Mail-Templates

## Was bewusst leer bleibt

- Kunden
- Pferde
- Rechnungen
- Lieferscheine
- Zahlungen und Imports
- kundenbezogene PDFs

## Testen

```bash
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

Hinweis: `requirements-dev.txt` enthält auch `httpx`, das für `fastapi.testclient` benötigt wird.
