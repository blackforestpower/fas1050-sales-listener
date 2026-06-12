# FAS 1050 PRO - Sales Detection Logic
## Stand: 12.06.2026 — Implementiert in v5

## Konfiguration
Seit v5 wird die Konfiguration ausschließlich über `.env` gesteuert (siehe `.env.example`).
`VAS1050_HOST` ist **zwingend erforderlich** — ohne Wert startet das Skript nicht.

### Zwei Zahlungsprozesse

#### 💳 KARTE (Kredit immer 4900)
Die 49.00€ Reserve ist die Kartenauthorisierung, **nicht** der Preis.
```
select → viewprice (optional) → readcredit 4900 → select → erog → readcredit 0 → take
```
- **Preis-Quelle:** Slot-Preis-Tabelle (nicht viewprice, nicht Kredit)
- **Kein Kredit-Verlauf** — geht direkt von 4900 auf 0
- `viewprice` kann erscheinen (Display-Update), muss aber nicht

#### 💵 CASH (Kredit = eingeworfener Betrag)
Der Kredit zeigt den tatsächlich eingeworfenen Geldbetrag.
```
select → viewprice → readcredit X (mehrere, steigend) → select → erog → readcredit Y (sinkt) → readcredit 0 → take
```
- **Preis-Quelle:** `viewprice` (der exakte Preis steht da!)
- **Kredit-Verlauf:** Einwurf (z.B. 200→250→500) → Abzug (z.B. 250) → Rückgeld (0)
- **Beispiel heute 07:26:** 5€-Schein (Credit 500) → 2.50€ Abzug (Credit 250) → 2.50€ Rückgeld (Credit 0)
- **Beispiel heute 07:27:** Exakt 2.50€ eingeworfen (Credit 200→250) → 2.50€ Abbuchung (Credit 0)

### Kredit-Erklärung
- **4900 = 49.00€** → Kartenterminal-Reserve
- **500 = 5.00€** → Bargeld (5€-Schein) oder Kartenauthorisierung mit genauem Betrag
- **250 = 2.50€** → Bargeld nach Einwurf oder Restguthaben
- **200 = 2.00€** → Bargeld (2€-Münze eingeworfen)
- **0** → kein Guthaben / Transaktion abgeschlossen
