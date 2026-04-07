# Architektur

## Ziel
Der bisherige Einzeldatei-Ansatz wurde in eine schrittweise wartbare Struktur überführt, ohne die vorhandenen Workflows zu brechen.

## Schichten

### 1. Konfiguration
`huf_app/config.py`

Zentrale Pfade, Secrets und Runtime-Optionen.

### 2. Datenbank
`huf_app/db/core.py` und `huf_app/db/seed.py`

- SQLite-Verbindung und Query-Helper
- Schema-Erstellung
- sauberes Seeding
- Datenbereinigung

### 3. Services
`huf_app/services/*`

Enthält Geschäftslogik für:
- Berechtigungen
- Rechnungen und Lieferscheine
- PDF-Erzeugung
- Mailversand
- Zahlungsimport und Matching

### 4. Darstellung
`huf_app/presentation.py`

Jinja-Templates und gemeinsamer Kontext.

### 5. Routen
`huf_app/routes/*`

Nach Funktionsbereichen getrennte Router statt einer riesigen `app.py`.

### 6. Einstiegspunkt
`huf_app/factory.py`, `main.py`, `app.py`

- `factory.py`: baut die FastAPI-App zusammen
- `main.py`: klarer Einstiegspunkt
- `app.py`: Kompatibilität für den bisherigen Uvicorn-Befehl
