# PROJECT_CONTEXT

## Zweck
Diese App unterstützt den Betriebsablauf eines Hufschmieds von Tageslieferschein bis Rechnung, Zahlung und Arbeitszeit.

## Aktueller Status
Das Projekt wurde auf einen **sauberen Starterzustand** zurückgesetzt. Kundenbezogene Echtdaten wurden entfernt. Die Datenbank enthält nur noch:
- interne Benutzer
- Rollen und Berechtigungen
- Firmenstammdaten von Marvin Binder
- generische Leistungsvorlagen
- Mailvorlagen

## Wichtige Regeln für weitere Entwicklung
- Keine echten Kundendaten im Repo committen.
- Vor dem Teilen des Projekts immer `scripts/sanitize_customer_data.py` ausführen.
- Neue Business-Logik immer mit kurzer Begründung im Code kommentieren.
- Größere Änderungen zuerst in `CHANGELOG.md` und `TODO.md` notieren.
- Architektur schrittweise verbessern, kein hektischer Komplett-Umbau.

## Kernmodule
- Dashboard
- Kunden
- Pferde
- Locations
- Tageslieferscheine
- Rechnungen
- Zahlungen
- Arbeitszeiten
- Einstellungen

## Technischer Stack
- FastAPI
- Jinja2 Templates
- SQLite
- ReportLab für PDFs

## Datenpolitik
- Kundennamen, Pferdenamen, Rechnungsnummern und Kundennummern gehören nicht in geteilte Projektstände.
- Firmenangaben von Marvin Binder dürfen im Projekt verbleiben.
