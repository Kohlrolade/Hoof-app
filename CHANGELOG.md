# Changelog

## v2-structured
- neue Paketstruktur unter `huf_app/`
- Konfiguration in `config.py` ausgelagert
- Datenbankzugriff in `db/core.py` zentralisiert
- Seed- und Bereinigungslogik in `db/seed.py`
- Geschäftslogik auf Services verteilt
- Routen in thematische Router aufgeteilt
- alter Startbefehl `uvicorn app:app --reload` kompatibel gehalten
- `itsdangerous` zu den Requirements ergänzt
- Smoke-Tests für Start und Kernseiten ergänzt
- alte Monolith-Datei als Referenz nach `legacy/legacy_app_reference.py` verschoben
