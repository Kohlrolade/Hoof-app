"""PDF rendering for invoices."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from ..config import BASE_DIR, PDF_DIR
from ..db.core import qall, qone
from ..utils.formatting import euro, fmt_date, now_ts

def generate_invoice_pdf(conn: sqlite3.Connection, invoice_id: int) -> str:
    """Render the current invoice into a PDF file inside the configured PDF directory."""
    invoice = qone(conn, 'SELECT * FROM invoices WHERE id = ?', (invoice_id,))
    customer = qone(conn, 'SELECT * FROM customers WHERE id = ?', (invoice['customer_id'],)) if invoice else None
    company = qone(conn, 'SELECT * FROM company_settings WHERE id = 1')
    lines = qall(conn, 'SELECT * FROM invoice_lines WHERE invoice_id = ? ORDER BY sort_order, id', (invoice_id,))
    if not invoice or not company:
        raise HTTPException(status_code=404, detail='Rechnung nicht gefunden.')
    file_name = f"{invoice['invoice_number'] or f'Entwurf-{invoice_id}'}.pdf".replace('/', '-')
    file_path = PDF_DIR / file_name
    c = canvas.Canvas(str(file_path), pagesize=A4)
    width, height = A4

    def draw_text(x_mm: float, y_mm: float, text: str, size: int = 10, bold: bool = False, align: str = 'left'):
        c.setFont('Helvetica-Bold' if bold else 'Helvetica', size)
        x = x_mm * mm
        y = height - y_mm * mm
        if align == 'right':
            c.drawRightString(x, y, text)
        elif align == 'center':
            c.drawCentredString(x, y, text)
        else:
            c.drawString(x, y, text)

    draw_text(15, 15, company['owner_name'] or company['company_name'] or 'Hufbeschlag', 20, True)
    draw_text(15, 22, company['company_name'] or '', 10)
    draw_text(15, 29, f"{company['street'] or ''} | {company['postal_code'] or ''} {company['city'] or ''}", 9)

    y = 50
    if customer:
        draw_text(15, y, customer_label(customer), 16, True)
        y += 8
        if customer['street']:
            draw_text(15, y, customer['street'], 12)
            y += 7
        city_line = ' '.join(filter(None, [customer['postal_code'], customer['city']]))
        if city_line:
            draw_text(15, y, city_line, 12)

    c.setFillColorRGB(0.95, 0.95, 0.95)
    c.rect(120 * mm, height - 78 * mm, 75 * mm, 38 * mm, fill=1, stroke=0)
    c.setFillColor(colors.black)
    draw_text(132, 52, 'Rechnung', 14)
    info = [
        ('Rechnungsnummer:', invoice['invoice_number'] or 'wird bei Freigabe vergeben'),
        ('Rechnungsdatum:', fmt_date(invoice['invoice_date'])),
        ('Leistungsdatum:', fmt_date(invoice['service_date'])),
        ('Kundennummer:', customer['customer_number'] if customer else '-'),
        ('Zahlungsziel:', f"{invoice['payment_term_days']} Tage"),
        ('Fälligkeitsdatum:', fmt_date(invoice['due_date'])),
    ]
    info_y = 60
    for label, value in info:
        draw_text(126, info_y, label, 10)
        draw_text(190, info_y, str(value), 10, False, 'right')
        info_y += 6

    top = 110
    c.line(10 * mm, height - top * mm, 200 * mm, height - top * mm)
    draw_text(15, top + 7, 'Bezeichnung', 11)
    draw_text(122, top + 7, 'Anzahl', 11, False, 'right')
    draw_text(140, top + 7, 'Einheit', 11, False, 'right')
    draw_text(168, top + 7, 'Einzelpreis', 11, False, 'right')
    draw_text(193, top + 7, 'Gesamtpreis', 11, False, 'right')
    draw_text(168, top + 13, 'Brutto', 9, False, 'right')
    draw_text(193, top + 13, 'Brutto', 9, False, 'right')
    c.line(10 * mm, height - (top + 16) * mm, 200 * mm, height - (top + 16) * mm)

    row_y = top + 27
    for line in lines:
        desc = line['description'] or ''
        wrapped = [desc[i:i+52] for i in range(0, len(desc), 52)] or ['']
        first_line_y = row_y
        for sub in wrapped:
            draw_text(15, row_y, sub, 10)
            row_y += 6
        draw_text(122, first_line_y, f"{line['quantity']}".replace('.', ','), 10, False, 'right')
        draw_text(140, first_line_y, line['unit'] or '', 10, False, 'right')
        draw_text(168, first_line_y, euro(line['unit_price_gross']), 10, False, 'right')
        draw_text(193, first_line_y, euro(line['line_total_gross']), 10, False, 'right')
        row_y += 4

    total_top = 230
    c.line(10 * mm, height - total_top * mm, 200 * mm, height - total_top * mm)
    draw_text(183, total_top + 7, 'Endbetrag', 14, False, 'right')
    draw_text(145, total_top + 18, 'Nettobetrag', 10, False, 'right')
    vat_label = f"mwst. {int((lines[0]['vat_rate'] if lines else 19) or 19)}%"
    draw_text(168, total_top + 18, vat_label, 10, False, 'right')
    draw_text(193, total_top + 18, 'Brutto', 10, False, 'right')
    c.line(10 * mm, height - (total_top + 22) * mm, 200 * mm, height - (total_top + 22) * mm)
    draw_text(145, total_top + 33, euro(invoice['net_total']), 12, False, 'right')
    draw_text(168, total_top + 33, euro(invoice['vat_total']), 12, False, 'right')
    draw_text(193, total_top + 33, euro(invoice['gross_total']), 12, True, 'right')

    foot_y = 275
    draw_text(15, foot_y, 'Kontaktinformation', 12, True)
    draw_text(85, foot_y, 'Kontodaten', 12, True)
    draw_text(155, foot_y, 'Überweisungsbetreff', 12, True)
    yy = foot_y + 7
    for item in [company['owner_name'], company['street'], f"{company['postal_code']} {company['city']}".strip(), company['phone'], f"Steuernummer {company['tax_number']}" if company['tax_number'] else '']:
        if item:
            draw_text(15, yy, item, 10)
            yy += 6
    yy = foot_y + 12
    for item in [company['bank_name'], company['iban'], company['bic']]:
        if item:
            draw_text(110, yy, item, 12, False, 'center')
            yy += 8
    ref_tpl = company['invoice_payment_reference_template'] or '{invoice_number} {last_name}'
    reference = ref_tpl.replace('{invoice_number}', invoice['invoice_number'] or '').replace('{last_name}', (customer['last_name'] or customer['company_name'] or '').strip() if customer else '')
    draw_text(190, foot_y + 14, reference, 12, False, 'right')
    c.save()
    conn.execute('UPDATE invoices SET pdf_path = ?, updated_at = ? WHERE id = ?', (str(file_path.relative_to(BASE_DIR)), now_ts(), invoice_id))
    return str(file_path)
