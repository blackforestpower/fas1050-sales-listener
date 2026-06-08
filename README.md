# FAS 1050 PRO Vending Machine — Sales Listener

Passiver Lauscher für den FAS 1050 PRO Vending-Automaten.
**LiSPI (Listen Only, No Transmit)** — es werden KEINE Kommandos gesendet.

## Funktionsweise

Der Automat sendet auf TCP-Port 8888 kontinuierlich Statusdaten im Klartext (ASCII).
Der Listener verbindet sich einmalig und lauscht — der Automat pusht, der Client empfängt nur.

Es gibt **kein Login, kein Handshake, kein Polling**.

## Datenformat (ASCII, Zeilen mit `\r\n`)

| Befehl | Beispiel | Bedeutung |
|--------|----------|-----------|
| `selstate: ack <state>` | `enderog` = bereit, `erog` = Ausgabe läuft, `viewprice 250` = Preis wird gezeigt | Verkaufsstatus |
| `readerrors: ack <byte> <code> <count>` | `16 54 0` → kein aktiver Fehler | Fehlerstatus |
| `gettemperature: ack <t1> <t2>` | `78 -20` → Zone 1 = 7.8°C, Verdampfer -2.0°C | Kühltemperatur |
| `gettemperaturetwo 1: ack <t1> <t2>` | `73 -1` → Zone 2 = 7.3°C | Dual-Zone Temperatur |
| `readprice <rack> <slot>: ack <id> <preis_cent> ...` | `readprice 1 11: ack 101 600 10 10` → Slot 11 = 6.00€ | Preisabfrage |
| `readcredit: ack <einheiten> <anzahl>` | `0 5` → Guthaben / Zähler | Kredit/Verkaufszähler |
| `readclock: ack <tag> <mon> <jahr> <wtag> <std> <min> <sec>` | `7 6 25 7 14 15 58` → 07.06.2025 14:15:58 | Automaten-Uhr |

## Verkaufserkennung (v4)

Es gibt **zwei völlig unterschiedliche Zahlungsprozesse**, die unterschiedlich erkannt werden:

### 💳 KARTE

Der Kartenterminal autorisiert immer eine **49,00€ Reserve** (Kredit = 4900).

```
select → viewprice (optional) → readcredit: 4900 → select → erog → readcredit: 0 → take
```

- **4900 = Karte.** Punkt. Ist immer die Kartenterminal-Reserve, **nicht** der Preis.
- **Preis-Quelle:** Slot-Preis-Tabelle (aus dem initialen Burst)
- `viewprice` kann erscheinen (Display-Update), wird aber nicht für die Preisermittlung genutzt
- Der Kredit springt direkt von 4900 auf 0 — kein Kredit-Verlauf dazwischen

**Beispiel gestern Abend (22:25, Slot 69, 2.50€):**
```
select 0 69 → viewprice 250 → readcredit: 4900 → select 0 69 → erog → readcredit: 0 → take
```

### 💵 CASH

Der Kredit zeigt den tatsächlich eingeworfenen Geldbetrag.

```
select → viewprice → readcredit X (mehrere, steigend) → select → erog → readcredit Y (sinkt) → readcredit 0 → take
```

- **Preis-Quelle:** `viewprice` — dort steht der exakte Preis!
- **Kredit-Verlauf:** Einwurf (z.B. 200 → 250 → 500) → Abzug (250) → Rückgeld (0)
- Bei exakter Zahlung: Kredit von 250 direkt auf 0
- Bei größerem Schein: Kredit fällt erst auf Rückgeld-Betrag, dann auf 0

**Beispiel heute früh (07:26, Slot 64, 2.50€ mit 5€-Schein):**
```
readcredit: 500 (5€-Schein eingeworfen)
→ select 0 64 → erog
→ readcredit: 250 (2.50€ abgebucht)
→ readcredit: 0 (2.50€ Rückgeld)
→ take
```

**Beispiel heute früh (07:27, Slot 61, exakt 2.50€):**
```
select 0 61 → viewprice 250
→ readcredit: 200 (2€-Münze)
→ readcredit: 250 (0.50€ nachgeworfen)
→ select 0 61 → erog
→ readcredit: 0
→ take
```

### Zusammenfassung

| Merkmal | 💳 KARTE | 💵 CASH |
|---------|----------|---------|
| Kredit bei erog | **4900** (49.00€ Reserve) | variabel (eingeworfener Betrag) |
| Preis-Quelle | Slot-Preis-Tabelle | `viewprice` |
| viewprice nötig? | Nein (optional) | **Ja** (ist die Quelle!) |
| Kredit-Verlauf | 4900 → 0 (direkt) | Einwurf → Abzug → Rückgeld → 0 |

## Dateien

Alle Daten liegen im Ordner `data/`:

| Datei | Beschreibung |
|-------|-------------|
| `current_state.json` | Aktueller Zustand (wird ständig überschrieben) |
| `events.jsonl` | Relevante Ereignisse (Verkäufe, Zustandswechsel, Fehler) — eine JSON-Zeile pro Event. Enthält auch `payment_method`, `credit_before` |
| `raw_stream.log` | **Jede** Zeile vom Automaten mit Zeitstempel — rotiert bei >5MB oder >24h |
| `product_catalog.json` | Produktnamen pro Slot (optional, für schönere Telegram-Nachrichten) |

## Installation

```bash
# Service-Datei installieren
sudo cp fas1050-listener.service /etc/systemd/system/

# Service aktivieren und starten
sudo systemctl daemon-reload
sudo systemctl enable fas1050-listener.service
sudo systemctl start fas1050-listener.service

# Status prüfen
sudo systemctl status fas1050-listener.service

# Logs live ansehen
sudo journalctl -u fas1050-listener.service -f
```

## Telegram-Konfiguration

Optional: Telegram-Benachrichtigungen bei Verkäufen.

Umgebungsvariablen (oder Datei `.telegram_token` im Projektordner):

```
VAS1050_TELEGRAM_BOT_TOKEN=dein_bot_token
VAS1050_TELEGRAM_CHAT_ID=-5134447945
```

## Manueller Test

```bash
python3 fas1050_listener.py
# Oder: Rohdaten live verfolgen
tail -f data/raw_stream.log
# Oder via Daemon-Wrapper:
./fas1050_daemon.sh
```

## Technische Daten

| Eigenschaft | Wert |
|-------------|------|
| Automat | FAS 1050 PRO |
| IP | X.X.X.X |
| Port | TCP 8888 (Management-Interface) |
| Port 8889 | Seriennummer |
| Authentifizierung | Keine |
| Protokoll | Reines ASCII, zeilenbasiert |

> ⚠️ **Hinweis zur Erreichbarkeit:** Der Automat ist **nur im lokalen Netzwerk (LAN/WLAN)** erreichbar. Die IP muss in deiner eigenen Netzwerkkonfiguration ermittelt und in `fas1050_listener.py` als Konstante `AUTOMAT_HOST` eingetragen werden.

## Lizenz

Projekt von Camiro Bot — Kann bei GitHub veröffentlicht werden.
