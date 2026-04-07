# Datenbankschema

Die SQLite-Tabellen entsprechen weiterhin dem bewährten Schema der bestehenden App.
Der Unterschied liegt jetzt in der Struktur des Codes:

- Schema und Seed-Logik: `huf_app/db/seed.py`
- DB-Connection/Helper: `huf_app/db/core.py`
- Geschäftslogik: `huf_app/services/*`

Wichtige Tabellen:
- `users`
- `permissions`
- `customers`
- `locations`
- `horses`
- `service_templates`
- `company_settings`
- `number_sequences`
- `email_templates`
- `delivery_notes`
- `delivery_note_customer_groups`
- `delivery_note_entries`
- `invoices`
- `invoice_lines`
- `invoice_source_links`
- `invoice_email_log`
- `bank_imports`
- `bank_transactions`
- `invoice_payments`
- `payment_reminders`
- `time_entries`
