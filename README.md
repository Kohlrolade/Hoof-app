# Hufschmied Betriebs-App v1.0

Diese Version ist jetzt **bereinigt und startklar für die Weiterentwicklung**:

- keine Kunden-, Pferde-, Rechnungs- oder Zahlungsdaten mehr im Projekt
- deine eigenen Firmendaten von Marvin Binder bleiben erhalten
- saubere Start-Datenbank ohne Altlasten
- generische Leistungsvorlagen bleiben als Arbeitsbasis erhalten
- zusätzliche Projekt-Dokumente für IDE-/Agenten-Workflows sind angelegt

## Funktionsumfang

- Tageslieferscheine je Stall/Location und Datum
- Kundenblöcke im Tageslieferschein
- Leistungsvorschläge pro Pferd anhand letzter Leistung / Kundenstandard
- Rechnungen aus Kundenblöcken
- PDF-Erzeugung
- Mailversand von Rechnungen mit SMTP-Konfiguration oder Dry-Run-Protokoll
- Zahlungsimport per CSV
- Zuordnung von Bankbuchungen zu Rechnungen
- Zahlungserinnerungen
- Arbeitszeiterfassung
- Benutzerrollen: Inhaber, Büro, Mitarbeiter

## Bereinigter Datenstand

Bewusst **entfernt** wurden:

- Kundennamen
- Pferdenamen
- Rechnungsnummern
- Kundennummern
- Tageslieferscheine
- Rechnungs-PDFs
- Beispiel-Zahlungsdaten

Bewusst **behalten** wurden:

- Marvin Binder als Inhaber
- Firmendaten / Bankdaten / Kontaktdaten
- Rollen und Berechtigungen
- generische Leistungsvorlagen
- Mailvorlagen

## Projektdateien für den Weiterbau

- `PROJECT_CONTEXT.md` – fachlicher Überblick und Arbeitsregeln
- `ARCHITECTURE.md` – aktuelle Struktur und Zielstruktur
- `DB_SCHEMA.md` – Tabellenübersicht
- `TODO.md` – priorisierte nächste Schritte
- `CHANGELOG.md` – dokumentierte Änderungen an dieser Bereinigung
- `scripts/rebuild_clean_database.py` – setzt eine frische, leere Datenbank auf
- `scripts/sanitize_customer_data.py` – löscht Geschäftsdaten aus einer vorhandenen DB

## Start

```bash
cd huf_app_v1_patched
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app:app --reload
```

Danach im Browser öffnen:

```text
http://127.0.0.1:8000
```

## Datenbank neu aufsetzen

Wenn du wieder eine komplett leere Arbeitsdatenbank willst:

```bash
python scripts/rebuild_clean_database.py
```

Wenn du nur kundenbezogene Geschäftsdaten aus einer bestehenden DB entfernen willst:

```bash
python scripts/sanitize_customer_data.py
```

## Mailversand

Ohne SMTP-Daten wird der Versand **nicht real versendet**, sondern als Testlauf protokolliert.

SMTP-Daten können unter **Einstellungen** eingetragen werden.

## Entwicklungsworkflow mit IDE / Agent

Empfehlung:

1. Projekt in Git verwalten
2. diese Markdown-Dateien im Repo aktuell halten
3. Änderungen in kleinen, klaren Schritten machen
4. einen Coding-Agent direkt auf das Repo arbeiten lassen
5. vor dem Teilen immer `scripts/sanitize_customer_data.py` ausführen

## Nächste sinnvolle Ausbaustufen

- `app.py` schrittweise in `routes/`, `services/`, `db/` und `pdf/` zerlegen
- kleine Regressionstests für Rechnungen, Tageslieferscheine und Zahlungsmatching ergänzen
- Editierfunktionen und Validierung weiter ausbauen
- CSV-/Excel-Import später gezielt und sicher neu ergänzen
