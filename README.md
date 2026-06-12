# FAS 1050 PRO Vending Machine — Sales Listener (v5)

Passiver Lauscher für den FAS 1050 PRO Vending-Automaten.
**LiSPI (Listen Only, No Transmit)** — es werden KEINE Kommandos gesendet.

## Funktionsweise

Der Automat sendet auf TCP-Port 8888 kontinuierlich Statusdaten im Klartext (ASCII).
Der Listener verbindet sich einmalig und lauscht — der Automat pusht, der Client empfängt nur.

Es gibt **kein Login, kein Handshake, kein Polling**.

## Quickstart

```bash
# 1. Konfiguration anlegen (IP & Token eintragen!)
cp .env.example .env
nano .env

# 2. Service installieren & starten
sudo cp fas1050-listener.service /etc/systemd/system/vas1050-listener.service
sudo systemctl daemon-reload
sudo systemctl enable vas1050-listener
sudo systemctl start vas1050-listener

# 3. Logs prüfen
sudo journalctl -u vas1050-listener -f
```

> ⚠️ **Hinweis:** `VAS1050_HOST` ist **zwingend erforderlich** — ohne `.env` startet das Skript nicht!
  `.env` wird nicht versioniert (in `.gitignore`).

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

## Verkaufserkennung (v5)

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
| `sales.db` | SQLite-Datenbank mit allen Verkäufen (inkl. Produktname, Zahlungsart, Temperatur) |
| `raw_stream.log` | **Jede** Zeile vom Automaten mit Zeitstempel — rotiert bei >5MB oder >24h |
| `product_catalog.json` | Produktnamen pro Slot (optional, für schönere Telegram-Nachrichten) |

## Konfiguration

Alle Einstellungen erfolgen ausschließlich via **`.env` Datei** (siehe `.env.example`).

➡️ `.env` liegt im Projektordner, ist in `.gitignore` und wird **nicht versioniert**. Das Skript lädt sie via `python-dotenv` und **bricht ab**, wenn `VAS1050_HOST` fehlt.

| Variable | Pflicht | Default | Beschreibung |
|----------|---------|---------|-------------|
| `VAS1050_HOST` | ✅ Ja | — | IP-Adresse des Automaten |
| `VAS1050_PORT` | ❌ Nein | `8888` | TCP-Port |
| `VAS1050_RECONNECT_DELAY` | ❌ Nein | `5` | Sekunden bis Wiederverbindung |
| `VAS1050_TELEGRAM_BOT_TOKEN` | ❌ Nein | — | Bot-Token (für Telegram-Benachrichtigungen) |
| `VAS1050_TELEGRAM_CHAT_ID` | ❌ Nein | — | Ziel-Chat-ID |

```bash
cp .env.example .env
nano .env
```

## Manueller Test

```bash
python3 fas1050_listener_v5.py
# Oder: Rohdaten live verfolgen
tail -f data/raw_stream.log
```

## Installation (systemd)

```bash
# Service-Datei installieren
sudo cp fas1050-listener.service /etc/systemd/system/vas1050-listener.service

# Ausführbar machen (falls nicht schon)
chmod +x fas1050_listener_v5.py

# Service aktivieren und starten
sudo systemctl daemon-reload
sudo systemctl enable --now vas1050-listener

# Status prüfen
sudo systemctl status vas1050-listener

# Logs live ansehen
sudo journalctl -u vas1050-listener -f
```

## Manueller Test

```bash
python3 fas1050_listener.py
# Oder: Rohdaten live verfolgen
tail -f data/raw_stream.log
```

## Technische Daten

| Eigenschaft | Wert |
|-------------|------|
| Automat | FAS 1050 PRO |
| IP | via `VAS1050_HOST` in `.env` |
| Port | TCP 8888 (Management-Interface) |
| Port 8889 | Seriennummer (nicht genutzt) |
| Authentifizierung | Keine |
| Protokoll | Reines ASCII, zeilenbasiert über TCP |
| Max. Clients | 2 gleichzeitig |

> ⚠️ **Hinweis zur Erreichbarkeit:** Der Automat ist **nur im lokalen Netzwerk (LAN/WLAN)** erreichbar.

## SQLite-Datenbank (`data/sales.db`)

```sql
CREATE TABLE IF NOT EXISTS sales (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    slot            TEXT,
    product         TEXT,
    category        TEXT,
    price_cent      INTEGER NOT NULL,
    price_eur       REAL NOT NULL,
    payment         TEXT NOT NULL,    -- "card" oder "cash"
    credit_before   INTEGER,
    source          TEXT DEFAULT 'listener'
);
```

## Lizenz

Projekt für GitHub veröffentlicht.
