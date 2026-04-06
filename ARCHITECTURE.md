# ARCHITECTURE

## Aktuelle Struktur
Der Stand ist bewusst noch monolithisch:

- `app.py` enthält Routen, Datenbankzugriff, Berechnungen, PDF-Erzeugung und Seed-Logik
- `templates/` enthält Jinja-Templates
- `static/` enthält CSS und Assets
- `generated_pdfs/` ist Laufzeit-Ausgabe
- `app.db` ist die lokale SQLite-Datenbank

## Warum das im Moment okay ist
Für einen stabilen Neustart nach dem gelöschten Chat ist ein lauffähiger, dokumentierter Monolith besser als ein riskanter Schnell-Refactor.

## Zielstruktur für die nächsten Schritte
Empfohlene spätere Aufteilung:

- `routes/`
  - `dashboard.py`
  - `customers.py`
  - `delivery_notes.py`
  - `invoices.py`
  - `payments.py`
  - `time_entries.py`
  - `settings.py`
- `services/`
  - `invoice_service.py`
  - `delivery_note_service.py`
  - `payment_service.py`
  - `mail_service.py`
  - `pdf_service.py`
- `db/`
  - `connection.py`
  - `queries.py`
  - `seed.py`
- `models/` oder `schemas/`
- `tests/`

## Sofort priorisierte Trennlinien
1. DB-Helfer und Seed-Logik aus `app.py` herauslösen
2. Rechnungslogik auslagern
3. PDF-Logik auslagern
4. Zahlungsimport und Matching auslagern

## Bereits umgesetzt
- bereinigte Seed-Logik ohne Kundendaten
- Umgebungsvariablen für DB/PDF/Template-Pfade
- Scripts für Datenbereinigung und Neuaufbau
