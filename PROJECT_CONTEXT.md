# Projektkontext

## Produkt
Interne Betriebs-App für einen Hufschmied-Betrieb mit Fokus auf:
- Kunden- und Pferdeverwaltung
- Tageslieferscheine
- Rechnungsentwürfe, Freigabe und PDF-Erzeugung
- Zahlungseingänge und Zuordnung
- Arbeitszeiterfassung
- Firmen- und E-Mail-Einstellungen

## Arbeitsprinzip für die Weiterentwicklung
- kleine, sichere Branches
- zuerst testen, dann committen
- keine echten Kundendaten im Repo
- bestehende Templates und UI vorerst stabil halten
- Geschäftsdaten nur über die App oder gezielte Imports hinzufügen

## Aktueller Strukturstand
Diese Version ist **agent-ready**: Konfiguration, DB, Services und Routes sind getrennt. Dadurch lassen sich einzelne Bereiche künftig deutlich sicherer ändern.
