"""Mail composition and SMTP delivery."""

from __future__ import annotations

import smtplib
import sqlite3
from email.message import EmailMessage

from ..config import BASE_DIR
from ..db.core import execute, qone
from ..services.pdf_service import generate_invoice_pdf
from ..services.invoices import refresh_invoice
from ..utils.formatting import euro, fmt_date, now_ts
from ..utils.labels import customer_label

def build_email_from_template(conn: sqlite3.Connection, template_key: str, invoice: sqlite3.Row, customer: sqlite3.Row | None) -> tuple[str, str]:
    template = qone(conn, 'SELECT * FROM email_templates WHERE template_key = ?', (template_key,))
    subject = template['subject_template'] if template else f"Rechnung {invoice['invoice_number']}"
    body = template['body_template'] if template else 'anbei erhalten Sie Ihre Rechnung.'
    customer_name = customer_label(customer) if customer else ''
    replacements = {
        '{invoice_number}': invoice['invoice_number'] or '',
        '{customer_name}': customer_name,
        '{due_date}': fmt_date(invoice['due_date']),
        '{gross_total}': euro(invoice['gross_total']),
    }
    for k, v in replacements.items():
        subject = subject.replace(k, v)
        body = body.replace(k, v)
    return subject, body


def send_invoice_email(conn: sqlite3.Connection, invoice_id: int, recipient: str, subject: str, body: str) -> tuple[bool, str]:
    invoice = qone(conn, 'SELECT * FROM invoices WHERE id = ?', (invoice_id,))
    if not invoice:
        return False, 'Rechnung nicht gefunden.'
    company = qone(conn, 'SELECT * FROM company_settings WHERE id = 1')
    pdf_path = BASE_DIR / (invoice['pdf_path'] or '')
    if not pdf_path.exists():
        generate_invoice_pdf(conn, invoice_id)
        pdf_path = BASE_DIR / (qone(conn, 'SELECT pdf_path FROM invoices WHERE id = ?', (invoice_id,))['pdf_path'])
    smtp_host = (company['smtp_host'] or '').strip() if company else ''
    smtp_port = int(company['smtp_port'] or 587) if company else 587
    smtp_username = (company['smtp_username'] or '').strip() if company else ''
    smtp_password = (company['smtp_password'] or '').strip() if company else ''
    smtp_use_tls = bool(company['smtp_use_tls']) if company else True
    sender = smtp_username or company['email'] or 'demo@example.com'

    msg = EmailMessage()
    msg['From'] = sender
    msg['To'] = recipient
    msg['Subject'] = subject
    msg.set_content(body)
    if pdf_path.exists():
        with open(pdf_path, 'rb') as f:
            msg.add_attachment(f.read(), maintype='application', subtype='pdf', filename=pdf_path.name)

    if smtp_host and smtp_username and smtp_password:
        try:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
                if smtp_use_tls:
                    server.starttls()
                server.login(smtp_username, smtp_password)
                server.send_message(msg)
            conn.execute('UPDATE invoices SET sent_at = ?, updated_at = ? WHERE id = ?', (now_ts(), now_ts(), invoice_id))
            execute(conn, 'INSERT INTO invoice_email_log (invoice_id, recipient_email, subject, body_text, sent_at, status, error_message) VALUES (?, ?, ?, ?, ?, ?, ?)', (invoice_id, recipient, subject, body, now_ts(), 'sent', None))
            refresh_invoice(conn, invoice_id)
            return True, 'E-Mail gesendet.'
        except Exception as exc:
            execute(conn, 'INSERT INTO invoice_email_log (invoice_id, recipient_email, subject, body_text, sent_at, status, error_message) VALUES (?, ?, ?, ?, ?, ?, ?)', (invoice_id, recipient, subject, body, now_ts(), 'error', str(exc)))
            return False, f'Versand fehlgeschlagen: {exc}'
    execute(conn, 'INSERT INTO invoice_email_log (invoice_id, recipient_email, subject, body_text, sent_at, status, error_message) VALUES (?, ?, ?, ?, ?, ?, ?)', (invoice_id, recipient, subject, body, now_ts(), 'dry_run', 'SMTP nicht konfiguriert'))
    return True, 'SMTP ist noch nicht konfiguriert. Die Mail wurde als Testlauf protokolliert.'
