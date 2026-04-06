# DB_SCHEMA

## Stammdaten
- `users` – interne Benutzer
- `permissions` – Rechte je Benutzer und Modul
- `company_settings` – Firmendaten, SMTP, Bankdaten
- `service_templates` – generische Leistungen
- `email_templates` – Mailvorlagen
- `number_sequences` – Zähler für Rechnungen und Lieferscheine

## Geschäftsobjekte
- `customers` – Kundenstammdaten
- `locations` – Ställe / Orte
- `horses` – Pferde je Kunde / Ort
- `customer_service_defaults` – Kundenstandards für Leistungen

## Tagesgeschäft
- `delivery_notes` – Tageslieferscheine
- `delivery_note_customer_groups` – Kundenblöcke im Tageslieferschein
- `delivery_note_entries` – einzelne Leistungen pro Pferd

## Rechnungen und Zahlungen
- `invoices` – Rechnungsstammdaten
- `invoice_lines` – Rechnungspositionen
- `invoice_source_links` – Verknüpfung Lieferschein → Rechnung
- `invoice_email_log` – Versandprotokolle
- `bank_imports` – Importläufe
- `bank_transactions` – einzelne Bankbuchungen
- `invoice_payments` – bestätigte Zahlungen
- `payment_reminders` – Zahlungserinnerungen

## Zeit
- `time_entries` – Arbeitszeiten

## Hinweis
Die Tabellen bleiben vollständig erhalten, damit du später ohne Schema-Verlust eigene Kundendaten neu erfassen kannst.
