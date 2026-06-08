# VaS 1050 Vending Machine — Passive Listener

Passiver Lauscher für den VaS 1050 Vending-Automaten von FAS International.  
**LiSPI (Listen Only, No Transmit)** — es werden KEINE Kommandos gesendet.

## Funktionsweise

Der VaS 1050 sendet auf TCP-Port 8888 kontinuierlich Statusdaten im Klartext (ASCII).  
Der Listener verbindet sich einmalig und lauscht — der Automat pusht, der Client empfängt nur.

Es gibt **kein Login, kein Handshake, kein Polling**.

## Datenformat (ASCII, Zeilen mit `\r\n`)

| Befehl | Beispiel | Bedeutung |
|--------|----------|-----------|
| `selstate: ack <state>` | `enderog` = bereit, `erog` = Ausgabe läuft, `busy` = beschäftigt, `viewprice` = Preis wird gezeigt | Verkaufsstatus |
| `readerrors: ack <byte> <code> <count>` | `16 54 0` → kein aktiver Fehler | Fehlerstatus |
| `gettemperature: ack <t1> <t2>` | `78 -20` → Zone 1 = 7.8°C, Verdampfer -2.0°C | Kühltemperatur |
| `gettemperaturetwo 1: ack <t1> <t2>` | `73 -1` → Zone 2 = 7.3°C | Dual-Zone Temperatur |
| `readprice <rack> <slot>: ack <id> <preis_cent> ...` | `readprice 1 11: ack 101 600 10 10` → Slot 11 = 6.00€ | Preisabfrage |
| `readcredit: ack <einheiten> <anzahl>` | `0 5` → Guthaben / Zähler | Kredit/Verkaufszähler |
| `readclock: ack <tag> <mon> <jahr> <wtag> <std> <min> <sec>` | `7 6 25 7 14 15 58` → 07.06.2025 14:15:58 | Automaten-Uhr |

## Verkaufserkennung (v3)

Ein Verkauf wird erkannt an der Sequenz:  
`select → viewprice → busy → erog → (Preis/Kredit) → take/enderog`

- **Preis-Quelle:** `viewprice` (primär) oder Slot-Preis aus dem initialen Preis-Burst (Fallback bei Kartenzahlung)
- **Kein Kredit-Differenz-Fallback** — zu unzuverlässig (Kartenzahlung setzt Kredit auf XX.00€)
- Bei erkanntem Verkauf: Telegram-Benachrichtigung mit Preis, Zahlungsart (Cash/Karte), Slot und Temperaturen

## Dateien

Alle Daten liegen im Ordner `data/`:

| Datei | Beschreibung |
|-------|-------------|
| `current_state.json` | Aktueller Zustand (wird ständig überschrieben) |
| `events.jsonl` | Relevante Ereignisse (Verkäufe, Zustandswechsel, Fehler) — eine JSON-Zeile pro Event |
| `raw_stream.log` | **Jede** Zeile vom Automaten mit Zeitstempel (für Debugging) — rotiert bei >5MB oder >24h |
| `product_catalog.json` | Produktnamen pro Slot (optional, für schönere Telegram-Nachrichten) |

## Installation

```bash
# Service-Datei installieren
sudo cp vas1050-listener.service /etc/systemd/system/

# Service aktivieren und starten
sudo systemctl daemon-reload
sudo systemctl enable vas1050-listener.service
sudo systemctl start vas1050-listener.service

# Status prüfen
sudo systemctl status vas1050-listener.service

# Logs live ansehen
sudo journalctl -u vas1050-listener.service -f
```

## Telegram-Konfiguration

Optional: Telegram-Benachrichtigungen bei Verkäufen.

Umgebungsvariablen (oder Datei `.telegram_token` im Projektordner):

```
VAS1050_TELEGRAM_BOT_TOKEN=dein_bot_token
VAS1050_TELEGRAM_CHAT_ID=-1234567890
```

## Manueller Test

```bash
python3 vas1050_listener.py
# Oder: Rohdaten live verfolgen
tail -f data/raw_stream.log
```

## Technische Daten

| Eigenschaft | Wert |
|-------------|------|
| Automat | VaS 1050 PRO (FAS International) |
| IP | 192.168.200.146 |
| Port | TCP 8888 (Management-Interface) |
| Port 8889 | Seriennummer (202425042) |
| Authentifizierung | Keine |
| Protokoll | Reines ASCII, zeilenbasiert |

## Lizenz

Projekt von Camiro Bot — Kann bei GitHub veröffentlicht werden.
