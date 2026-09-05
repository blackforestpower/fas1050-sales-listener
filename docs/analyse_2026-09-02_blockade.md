# Analyse: Automat-Blockade 02.09.2026 (03:04–16:06 Uhr)

Auftrag von RCS Blackforest (03.09.2026): Blockade im Log finden, prüfen ob man
sie abfangen könnte, Backup erstellen, Fehler-Erkennung analysieren.

## Backup
- `backups/fas1050-sales-listener_20260903_195211.tar.gz` (9,7 MB, erstellt 03.09. 19:52)

## Befund im Log (`data/raw_stream.log`)

| Zeitpunkt | Ereignis |
|---|---|
| 03:03:35 | Verkaufsvorgang beginnt (`select 1 13`, ageprotection) |
| 03:04:12 | `select 0 51` → **Volvic 500ml**, Kartenzahlung 2,00 € |
| 03:04:31–37 | `erog lock` (Ausgabe läuft) |
| **03:04:38** | Zustand wechselt auf **`take`** (Entnahme) – Sale wird in DB geschrieben (id 3271) |
| 03:04:38 → **16:06:56** | **782 Minuten durchgehend `selstate: take`** (alle ~2 s bestätigt) |
| 16:06:50–55 | `take unlock` |
| **16:06:56** | zurück auf `enderog` (bereit) → Blockade vorbei |
| 16:11:25 | nächster Verkauf (Oreo mini) – Automat wieder nutzbar |

**Interpretation:** Der Automat blieb im Zustand `take` = „Ware zur Entnahme
bereit / Entnahmeklappe offen" hängen. Normal dauert `take` nur Sekunden
(Median 10 s, p90 22 s, Maximum 56 s). Offenbar wurde die Entnahme/ das
Schließen der Klappe nicht erkannt (Sensor/Klappe hängt oder Produkt klemmt),
dadurch blockierte der Automat 13 Stunden lang alle weiteren Verkäufe.
Die DB bestätigt: zwischen 03:04 (Volvic) und 16:11 (Oreo) **kein einziger Sale**.

## Warum der Listener das nicht gemeldet hat
1. **Watchdog greift nur bei Datenstau:** Der Listener verbindet neu, wenn
   >12 s *keine Daten* ankommen. Während der Blockade kamen aber kontinuierlich
   Daten (selstate alle ~2 s) → kein Reconnect, kein Alarm.
2. **Fehler-Erkennung (readerrors) blieb stumm:** Der Automat meldete an dem
   Tag nur `readerrors: ack 0 0 0` (17:33) – die Blockade wird **nicht** als
   Fehlercode gemeldet, also kein Fehler-Alert.
3. **Keine Stuck-State-Erkennung:** Es gibt keine Logik „selstate hängt > X
   Minuten in take/erog/busy → Alarm".

## Wiederkehrendes Problem (wichtig!)
Scan über beide Logdateien – Nicht-Idle-Phasen > 2 min:
- **30.08. 21:12:54 → 21:15:49 (3 min) `take`**
- **30.08. 21:55:42 → 23:28:36 (93 min) `take`**
- 02.09. 03:04:38 → 16:06:56 (782 min) `take`

→ Gleiche Blockade gab es schon am 30.08. zweimal (3 und 93 Minuten).
Ein Watchdog mit Schwelle von wenigen Minuten hätte alle drei Fälle gefangen.

## Umsetzung: Stuck-State-Watchdog ✅ (eingebaut am 03.09.2026)
In `fas1050_listener_v5.py` eingebaut (nur additive Funktion, Verkaufserkennung
`detect_sale`/DB unverändert – Tests grün):
- `stuck_watchdog(val)` wird bei **jeder** selstate-Zeile aufgerufen.
- **5 Minuten** ununterbrochen nicht auf `enderog` (bereit) → 1. Telegram-Warnung.
- Danach **stündliche Wiederholung**, solange die Blockade anhält.
- Rückkehr zu `enderog` → „✅ Automat wieder bereit“-Meldung.
- Reiner Beobachter: sendet nur Telegram, schreibt nichts in Sales/DB.
- Konfiguration: `STUCK_ALERT_DELAY_S = 300`, `STUCK_REPEAT_INTERVAL_S = 3600`.
- Service `vas1050-listener` neu gestartet (20:24 Uhr), Watchdog aktiv.

Datenbasis: Lange Nicht-`enderog`-Phasen > 30 min enthielten praktisch nie
Sales (0 in fast allen Fällen) → die Maschine war dort wirklich blockiert.
Watchdog-Schwelle 5 min schlägt nur bei echten Blockaden an (normale
take-Phasen dauern < 60 s).

Nicht umgesetzt (optional, größerer Eingriff): aktives Kommando an den
Automaten senden (Reset/Klappe schließen) – Listener ist read-only.

## Dateien
- Analyse: `docs/analyse_2026-09-02_blockade.md`
- Backup: `backups/fas1050-sales-listener_20260903_195211.tar.gz`

## Update 05.09.2026: ageprotection/viewprice sind KEINE Blockaden
- Vor-Ort-Check (ROSH): Bei `ageprotection` (Alterscheck) und `viewprice` (Preisanzeige)
  kann man normal kaufen – sie blockieren den Automaten NICHT.
- Watchdog daher nachgeschärft: Alarme nur noch für `take`/`busy`/`erog`
  (mechanische Hänger). `STUCK_WATCH_STATES` in fas1050_listener_v5.py.
- Die echten Blockaden (02.09., 30.08.) waren alle `take` → weiterhin abgedeckt.
