"""Shared constants and seed data for the application."""

from __future__ import annotations

MODULES = [
    'dashboard', 'delivery_notes', 'invoices', 'payments', 'time_entries',
    'customers', 'horses', 'locations', 'settings'
]

SERVICE_TEMPLATE_SEEDS = [
    ('Hufbeschlag 4 Eisen', 2, 'Stk.', 150.0, 19.0),
    ('Ledersohlen', 2, 'Stk.', 20.0, 19.0),
    ('Hufbeschlag 2 Eisen 2 Hufe Barhufkorrektur', 1, 'Stk.', 90.0, 19.0),
    ('orthopädisches Polster', 2, 'Stk.', 20.0, 19.0),
    ('4 Hufe Barhufkorrektur', 1, 'Stk.', 50.0, 19.0),
    ('Arbeitszeitpauschale', 1, 'Stk.', 35.0, 19.0),
    ('Materialpauschale', 1, 'Psch.', 150.0, 19.0),
    ('Anfahrtspauschale', 1, 'km', 0.5, 19.0),
    ('orthopädisches Eisen', 2, 'Stk.', 10.0, 19.0),
    ('Klebebeschlag', 2, 'Stk.', 100.0, 19.0),
    ('Anfahrt', 1, 'Psch.', 50.0, 19.0),
    ('Kunststoffsohlen', 2, 'Stk.', 0.0, 19.0),
    ('2 Hufe Klebebeschlag 2 Hufe Barhufkorrektur', 1, 'Stk.', 170.0, 19.0),
    ('orthopädische Einlagen', 4, 'Stk.', 5.0, 19.0),
    ('orthopädische Eisen', 2, 'Stk.', 10.0, 19.0),
    ('Hufbeschlag 2 Eisen', 1, 'Stk.', 90.0, 19.0),
]

EMAIL_TEMPLATE_SEEDS = [
    (
        'invoice_send',
        'Rechnung {invoice_number}',
        'Guten Tag {customer_name},\n\nanbei erhalten Sie Ihre Rechnung {invoice_number} über {gross_total}.\n\n'
        'Bitte überweisen Sie den Betrag bis spätestens {due_date}.\n\nViele Grüße\nMarvin Binder',
    ),
    (
        'payment_reminder_1',
        'Zahlungserinnerung zu Rechnung {invoice_number}',
        'Guten Tag {customer_name},\n\nzu unserer Rechnung {invoice_number} konnten wir bisher keinen Zahlungseingang '
        'feststellen.\nBitte prüfen Sie die Zahlung bis {due_date}.\n\nViele Grüße\nMarvin Binder',
    ),
]
