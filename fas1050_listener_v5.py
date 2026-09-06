#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║             VaS 1050 Vending Machine Listener                ║
║                                                              ║
║  LiSPI (listen only, no transmit) — PASSIVER LAUSCHER        ║
║  Verbindet sich zum VaS 1050 Automaten und zeichnet alle     ║
║  Daten auf, die der Automat von sich aus sendet.             ║
║                                                              ║
║  KEINE Kommandos werden gesendet — nur mithören!             ║
╚══════════════════════════════════════════════════════════════╝

PROTOKOLL-BESCHREIBUNG:
========================
Der VaS 1050 (FAS International) sendet auf TCP-Port 8888
kontinuierlich Statusdaten im Klartext (ASCII). Die Verbindung
ist unidirektional — der Automat pusht, der Client lauscht nur.

DATENFORMAT:
------------
Jede Zeile hat das Format:
    <befehl>: ack <wert1> <wert2> ...

Bekannte Befehle:
─────────────────
    selstate: ack <zustand>
        → Verkaufsstatus: "enderog" = bereit, "erog" = Ausgabe läuft
    
    readerrors: ack <byte> <code> <count>
        → Fehlerstatus: Byte, Fehlercode, Anzahl
    
    gettemperature: ack <temp1> <temp2>
        → Kühltemperatur in Zehntelgrad (78 = 7.8°C, -20 = -2.0°C)
    
    gettemperaturetwo 1: ack <temp1> <temp2>
        → Dual-Zonen Temperatur
    
    readprice <rack> <slot>: ack <id> <preis_cent> 10 10
        → Preis eines Slots (preis_cent / 100 = Euro)
          z.B. "readprice 1 11: ack 101 600 10 10" = Rack1/Slot11 = 6.00€
    
    readcredit: ack <einheiten> <anzahl>
        → Guthaben/Wertmarken (evtl. Verkaufszähler)
    
    readclock: ack <tag> <mon> <jahr> <wtag> <std> <min> <sec>
        → Interne Uhr des Automaten

AUSGABEDATEIEN:
───────────────
    current_state.json   → Aktueller Zustand (wird überschrieben)
    sales.db             → SQLite-Datenbank mit allen Verkäufen
    raw_stream.log        → Rohdaten (jede Zeile mit Zeitstempel)

INSTALLATION (systemd):
───────────────────────
    sudo cp vas1050-listener.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable vas1050-listener.service
    sudo systemctl start vas1050-listener.service
"""

import socket
import json
import time
import os
import sys
import sqlite3
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import URLError
from dotenv import load_dotenv

# ─── .env laden ───────────────────────────────────────────────
load_dotenv()

# ─── KONFIGURATION ────────────────────────────────────────────
HOST = os.environ.get("VAS1050_HOST")
if not HOST:
    print("❌ VAS1050_HOST nicht gesetzt — .env prüfen!")
    sys.exit(1)

try:
    PORT = int(os.environ.get("VAS1050_PORT", "8888"))
except ValueError:
    print("❌ VAS1050_PORT ungültig — muss eine Zahl sein")
    sys.exit(1)

try:
    RECONNECT_DELAY = int(os.environ.get("VAS1050_RECONNECT_DELAY", "5"))
except ValueError:
    print("❌ VAS1050_RECONNECT_DELAY ungültig — muss eine Zahl sein")
    sys.exit(1)

# ─── TELEGRAM ────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("VAS1050_TELEGRAM_BOT_TOKEN", "")
TELEGRAM_TRADING_GROUP = os.environ.get("VAS1050_TELEGRAM_CHAT_ID", "-5134447945")
TELEGRAM_ENABLED = True

# Fallback: Falls Environment-Variablen nicht gesetzt,
# Token aus externer Datei laden (damit nicht im Code sichtbar)
if not TELEGRAM_BOT_TOKEN:
    token_file = os.path.join(os.path.dirname(__file__), ".telegram_token")
    try:
        with open(token_file) as f:
            TELEGRAM_BOT_TOKEN = f.read().strip()
    except FileNotFoundError:
        pass  # Telegram deaktiviert – wird später geloggt
# ──────────────────────────────────────────────────────────────

# Test-Modus (wird von tests/test_sales_simulation.py gesetzt)
TEST_MODE_SOURCE = None  # None = normal, "test_simulation" = Test-Einträge

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
STATE_FILE = os.path.join(DATA_DIR, "current_state.json")
RAW_FILE = os.path.join(DATA_DIR, "raw_stream.log")
CATALOG_FILE = os.path.join(DATA_DIR, "product_catalog.json")
# ──────────────────────────────────────────────────────────────

os.makedirs(DATA_DIR, exist_ok=True)

LOCK_FILE = os.path.join(DATA_DIR, "listener.lock")


def check_lock():
    """PID-Lockfile: verhindert doppelten Listener-Start."""
    if os.path.exists(LOCK_FILE):
        with open(LOCK_FILE) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log(f"⚠️  Listener läuft bereits (PID {old_pid}) – beende mich")
            sys.exit(0)
        except (OSError, ProcessLookupError):
            log(f"🗑  Altes Lockfile (PID {old_pid}) – Prozess tot, überschreibe")
    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    log(f"🔒 Lockfile {LOCK_FILE} (PID {os.getpid()})")


def cleanup_lock():
    """Lockfile beim Beenden aufräumen."""
    if os.path.exists(LOCK_FILE):
        with open(LOCK_FILE) as f:
            pid_in_file = int(f.read().strip())
        if pid_in_file == os.getpid():
            os.remove(LOCK_FILE)
            log("🔓 Lockfile entfernt")


def rotate_log_if_needed():
    """raw_stream.log rotieren wenn >24h alt oder >5MB"""
    try:
        size = os.path.getsize(RAW_FILE)
        age = time.time() - os.path.getmtime(RAW_FILE)
        MAX_SIZE = 5 * 1024 * 1024  # 5 MB
        MAX_AGE = 24 * 3600  # 24h
        if size > MAX_SIZE or age > MAX_AGE:
            ts_rot = datetime.now().strftime("%Y%m%d_%H%M%S")
            rotated = f"{RAW_FILE}.{ts_rot}"
            os.rename(RAW_FILE, rotated)
            log(f"🔄 Log rotiert → {os.path.basename(rotated)} ({size//1024}KB, {age//3600:.0f}h alt)")
            # Alte Rotationen (>7 Tage) löschen
            from pathlib import Path
            for f in Path(DATA_DIR).glob("raw_stream.log.*"):
                if time.time() - f.stat().st_mtime > 7 * 86400:
                    f.unlink()
                    log(f"🧹 Altes Log gelöscht: {f.name}")
    except FileNotFoundError:
        pass  # Neu, kein rotate nötig


# load_prices_from_log() entfernt – Preise werden jetzt aus current_state.json geladen


# ─── SQLite Sales-DB ──────────────────────────────────────────
DB_FILE = os.path.join(DATA_DIR, "sales.db")


def _ensure_sales_table():
    """Stellt sicher, dass die sales-Tabelle existiert (einmalig beim Start)"""
    try:
        conn = sqlite3.connect(DB_FILE, timeout=5)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sales (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT NOT NULL,
                slot        TEXT,
                product     TEXT,
                category    TEXT,
                price_cent  INTEGER NOT NULL,
                price_eur   REAL NOT NULL,
                payment     TEXT NOT NULL,
                credit_before INTEGER,
                source      TEXT DEFAULT 'listener'
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        log(f"⚠️  DB-Fehler (init): {e}")


def _ensure_catalog_table():
    """Stellt sicher, dass die catalog-Tabelle existiert (einmalig beim Start)"""
    try:
        conn = sqlite3.connect(DB_FILE, timeout=5)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS catalog (
                slot        TEXT PRIMARY KEY,
                name        TEXT NOT NULL DEFAULT '',
                category    TEXT DEFAULT '',
                group_name  TEXT DEFAULT '',
                price_cent  INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        log(f"⚠️  DB-Fehler (catalog init): {e}")


def _migrate_catalog_from_json():
    """Migriert Daten aus product_catalog.json in die catalog-Tabelle (einmalig)"""
    try:
        conn = sqlite3.connect(DB_FILE, timeout=5)
        count = conn.execute("SELECT COUNT(*) FROM catalog").fetchone()[0]
        if count > 0:
            conn.close()
            log(f"📋 Katalog-DB bereits gefüllt: {count} Einträge")
            return

        if not os.path.exists(CATALOG_FILE):
            conn.close()
            log("⚠️  Kein product_catalog.json für Migration")
            return

        with open(CATALOG_FILE) as f:
            catalog = json.load(f)

        inserted = 0
        for slot_key, entry in catalog.items():
            name = entry.get("name", "")
            category = entry.get("group", entry.get("category", ""))
            group_name = entry.get("group", "")
            price = entry.get("price", 0)
            price_cent = int(round(price * 100))

            conn.execute(
                "INSERT OR REPLACE INTO catalog (slot, name, category, group_name, price_cent) VALUES (?, ?, ?, ?, ?)",
                (slot_key, name, category, group_name, price_cent)
            )
            inserted += 1

        conn.commit()
        conn.close()
        log(f"📋 Katalog aus JSON migriert: {inserted} Einträge")
    except Exception as e:
        log(f"⚠️  DB-Fehler (catalog migration): {e}")


def _get_catalog_entry(slot):
    """Holt einen Katalog-Eintrag (name, category) aus der DB"""
    try:
        if not slot:
            return None
        conn = sqlite3.connect(DB_FILE, timeout=5)
        row = conn.execute(
            "SELECT name, category, group_name, price_cent FROM catalog WHERE slot = ?",
            (slot,)
        ).fetchone()
        conn.close()
        if row:
            return {
                "name": row[0] or "",
                "category": row[1] or "",
                "group_name": row[2] or "",
                "price_cent": row[3]
            }
        return None
    except Exception as e:
        log(f"⚠️  DB-Fehler (get_catalog): {e}")
        return None


def write_sale_to_db(ts, slot, price_cent, price_eur, payment, credit_before):
    """Schreibt einen Sale in die SQLite-DB (Fehler tolerant)"""
    try:
        product = ""
        category = ""
        if slot:
            cat = _get_catalog_entry(slot)
            if cat:
                product = cat.get("name", "")
                category = cat.get("category", "")
        
        source = TEST_MODE_SOURCE if TEST_MODE_SOURCE else "listener"
        
        conn = sqlite3.connect(DB_FILE, timeout=5)
        conn.execute(
            "INSERT INTO sales (timestamp, slot, product, category, price_cent, price_eur, payment, credit_before, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, slot, product, category, price_cent, price_eur, payment, credit_before, source)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log(f"⚠️  DB-Fehler (write_sale): {e}")

# ──────────────────────────────────────────────────────────────

# ═══════════════════════════════════════════════════════════════
#  HELFER
# ═══════════════════════════════════════════════════════════════

def ts():
    """Aktueller Zeitstempel als lesbarer String"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    """Loggt eine Zeile mit Zeitstempel auf stdout (geht in systemd journal)"""
    print(f"[{ts()}] {msg}", flush=True)


# ═══════════════════════════════════════════════════════════════
#  PARSER
# ═══════════════════════════════════════════════════════════════

def parse_line(line):
    """
    Parst eine einzelne Zeile vom Automaten.
    
    Erkennbare Formate:
    - "Hello :-) ..." → Begrüßung
    - "<cmd>: ack <val1> <val2> ..." → Datum
    
    Returns: dict mit type, command, values und spezifischen Feldern
    """
    # Begrüßung (kommt einmalig bei Verbindungsaufbau)
    if "ack " not in line and "Hello" in line:
        return {"type": "greeting", "raw": line}

    # Normale Datenzeile
    if ": ack" in line:
        idx = line.index(": ack")
        cmd = line[:idx].strip()
        rest = line[idx + 5:].strip()
        values = rest.strip().split()
        result = {"type": "data", "command": cmd, "values": values, "raw": line}

        # ── Temperatur ──────────────────────────────────────
        # gettemperature: ack <t1> <t2>   → Zone 1 = t1/10 °C
        # gettemperaturetwo 1: ack <t1> <t2> → Zone 2 = t1/10 °C
        # Nur der ERSTE Wert ist die Temperatur, der zweite ist Dummy/Wert
        if "temperaturetwo" in cmd and len(values) >= 1:
            result["temp_c"] = int(values[0]) / 10.0  # Zehntelgrad → Grad
            result["zone"] = "Zone 2"
        elif "temperature" in cmd and len(values) >= 1:
            result["temp_c"] = int(values[0]) / 10.0
            result["zone"] = "Zone 1"

        # ── Preis pro Slot ──────────────────────────────────
        elif "readprice" in cmd:
            # Befehl: "readprice 1 11" → Rack 1, Slot 11
            parts = cmd.split()
            if len(parts) >= 3:
                result["rack"] = int(parts[1])
                result["slot"] = int(parts[2])
            # Werte: "ack 101 600 10 10" → SlotID=101, Preis=600¢ = 6,00€
            if len(values) >= 2:
                result["slot_id"] = int(values[0])
                result["price_cent"] = int(values[1])
                result["price_eur"] = int(values[1]) / 100.0

        # ── Fehler ──────────────────────────────────────────
        elif "readerrors" in cmd and len(values) >= 3:
            result["error_byte"] = int(values[0])
            result["error_code"] = int(values[1])
            result["error_count"] = int(values[2])

        # ── Verkaufsstatus ─────────────────────────────────
        elif "selstate" in cmd:
            result["state"] = values[0] if values else "unknown"
            # viewprice: Preis steht in values[1] (z.B. "viewprice 250")
            if "viewprice" in result["state"] and len(values) >= 2:
                try:
                    result["viewprice_cent"] = int(values[1])
                except ValueError:
                    pass

        # ── Guthaben / Verkaufszähler ──────────────────────
        elif "readcredit" in cmd and len(values) >= 2:
            result["credit_units"] = int(values[0])
            result["credit_count"] = int(values[1])

        # ── Automaten-Uhr ──────────────────────────────────
        elif "readclock" in cmd and len(values) >= 7:
            result["datetime"] = {
                "day": int(values[0]),
                "month": int(values[1]),
                "year": int(values[2]),
                "weekday": int(values[3]),
                "hour": int(values[4]),
                "minute": int(values[5]),
                "second": int(values[6]),
            }

        return result

    # Unbekanntes Format
    return {"type": "unknown", "raw": line}


# ═══════════════════════════════════════════════════════════════
#  ZUSTANDSMANAGEMENT
# ═══════════════════════════════════════════════════════════════

def process_line(line, state, last_state):
    """
    Verarbeitet eine eingehende Zeile:
    1. Loggt sie ins raw_stream.log
    2. Parst sie
    3. Aktualisiert den Zustand bei Änderung
    4. Speichert State + Event bei Änderung
    """
    # Rohdaten-Log (jede Zeile)
    with open(RAW_FILE, "a") as f:
        f.write(f"[{ts()}] {line}\n")

    # Parsen
    parsed = parse_line(line)
    if parsed["type"] == "greeting":
        state["greeting"] = line
        log(f"👋 {line}")
        return

    if parsed["type"] != "data":
        return

    cmd = parsed.get("command", "")

    # Prüfen ob sich was geändert hat
    changed = False

    # ── Preise ──────────────────────────────────────────────
    if "readprice" in cmd:
        slot_num = str(parsed['rack'] * 100 + parsed['slot'])
        val = {
            "slot_id": parsed["slot_id"],
            "price_cent": parsed["price_cent"],
            "price_eur": parsed["price_eur"]
        }
        new_price = val['price_eur']
        old_prices = state.get("prices", {})
        if old_prices.get(slot_num) != val:
            old_prices[slot_num] = val
            state["prices"] = old_prices
            changed = True
            log(f"🏷  Slot {slot_num}: {new_price:.2f}€")
            # Preis auch in den Katalog übernehmen (Automat = Ground Truth)
            new_price_cent = int(round(new_price * 100))
            try:
                conn = sqlite3.connect(DB_FILE, timeout=5)
                conn.execute(
                    "UPDATE catalog SET price_cent = ?, name = COALESCE(NULLIF(name, ''), name), category = COALESCE(NULLIF(category, ''), category) WHERE slot = ? AND price_cent != ?",
                    (new_price_cent, slot_num, new_price_cent)
                )
                if conn.total_changes > 0:
                    log(f"📋  Katalog aktualisiert: Slot {slot_num} = {new_price:.2f}€")
                else:
                    # Slot existiert noch nicht – neu anlegen (unbekannter Slot)
                    conn.execute(
                        "INSERT OR IGNORE INTO catalog (slot, name, category, group_name, price_cent) VALUES (?, '', '', '', ?)",
                        (slot_num, new_price_cent)
                    )
                    if conn.total_changes > 0:
                        log(f"📋  Neuer Katalog-Eintrag: Slot {slot_num} = {new_price:.2f}€")
                conn.commit()
                conn.close()
            except Exception as e:
                log(f"⚠️  DB-Fehler (price update): {e}")

    # ── Temperaturen ───────────────────────────────────────
    # gettemperature: Zone 1 (Spirale), gettemperaturetwo: Zone 2 (Türen/Getränke)
    # Nur erster Wert = Temperatur, zweiter = Dummy
    elif "temperaturetwo" in cmd or "temperature" in cmd:
        zone = "zone2" if "two" in cmd else "zone1"
        temp_c = parsed.get("temp_c")
        old_temps = state.get("temperatures", {})
        if old_temps.get(zone) != temp_c:
            old_temps[zone] = temp_c
            state["temperatures"] = old_temps
            changed = True
            zone_label = "Zone 2" if zone == "zone2" else "Zone 1"
            log(f"🌡  {zone_label}: {temp_c}°C")

    # ── Fehler ──────────────────────────────────────────────
    elif "readerrors" in cmd:
        err = {
            "byte": parsed["error_byte"],
            "code": parsed["error_code"],
            "count": parsed["error_count"]
        }
        if state.get("errors") != err:
            old_err = state.get("errors")
            state["errors"] = err
            changed = True
            log(f"⚠️  Fehler: Byte={err['byte']} Code={err['code']} "
                f"Count={err['count']}")
            # Fehler-Wechsel (aktiv/behoben) → Telegram-Alert
            detect_error_change(state, old_err, err)


    # ── Verkaufsstatus ─────────────────────────────────────
    elif "selstate" in cmd:
        val = parsed["state"]
        if state.get("selstate") != val:
            old_val = state.get("selstate", "???")
            state["selstate"] = val
            changed = True
            log(f"📋  Status: {old_val} → {val}")
        # Stuck-State-Watchdog: bei JEDER selstate-Zeile prüfen
        # (reiner Beobachter – kein Eingriff in die Verkaufserkennung)
        stuck_watchdog(val)

    # ── Guthaben / Verkaufszähler ──────────────────────────
    elif "readcredit" in cmd:
        val = {"units": parsed["credit_units"], "count": parsed["credit_count"]}
        if state.get("credit") != val:
            old = state.get("credit", {})
            state["credit"] = val
            changed = True
            diff = (val.get("count", 0) or 0) - (old.get("count", 0) or 0)
            if diff > 0 and val.get("units", 0) or 0 > 0:
                log(f"💰  Kredit geladen: +{val['units']} Cent")
            elif val.get("count") is not None:
                log(f"   (Count init: {val['count']})")

    # ── Uhrzeit ────────────────────────────────────────────
    elif "readclock" in cmd:
        c = parsed["datetime"]
        state["clock"] = c
        changed = True
        log(f"🕒  Automaten-Zeit: {c['day']:02d}.{c['month']:02d}."
            f"{c['year']} {c['hour']:02d}:{c['minute']:02d}:"
            f"{c['second']:02d}")

    # Bei Änderung speichern
    if changed:
        state["last_seen"] = datetime.now().isoformat()
        save_state(state)

    # Verkaufserkennung + Telegram (bei jeder readcredit-Zeile)
    detect_sale(line, parsed, state)
    
    # Reboot-Erkennung (readpar/writeclock nach Datenlücke)
    detect_reboot(cmd, parsed, state)


def save_state(state):
    """Aktuellen Zustand als JSON speichern (immer überschreiben)"""
    # catalog aus state rausfiltern – kommt immer frisch aus product_catalog.json
    save = {k: v for k, v in state.items() if k != "catalog"}
    with open(STATE_FILE, "w") as f:
        json.dump(save, f, indent=2, ensure_ascii=False)

# ═══════════════════════════════════════════════════════════════
#  SALES DETECTION + TELEGRAM
# ═══════════════════════════════════════════════════════════════
#
# LOGIK (v4 - 08.06.2026):
# ─────────────────────────
# Es gibt zwei völlig unterschiedliche Zahlungsprozesse:
#
# 💳 KARTE (Kredit = 4900 = 49,00€ Reserve)
#   select → viewprice → readcredit 4900 → select → erog → readcredit 0 → take
#   - 4900 = Kartenterminal-Reserve, NICHT der Preis!
#   - Preis: PRIO 1 viewprice (kommt immer) → PRIO 2 Preiscache → PRIO 3 Kredit-Drop (wenn != 4900)
#   - viewprice wird NICHT mehr von select resettet → bleibt erhalten
#
# 💵 CASH (Kredit = eingeworfener Geldbetrag)
#   select → viewprice → readcredit X (mehrere, steigend) → select → erog
#   → readcredit Y (sinkt) → readcredit 0 → take
#   - Preis kommt AUSSCHLIESSLICH aus viewprice!
#   - Kredit-Verlauf: Einwurf (z.B. 500) → Abzug (250) → Rückgeld (0)
#   - Der Kredit-Vorher (credit_before) ist der eingeworfene Betrag
#
# Slot aus select <rack> <slot>  →  Anzeige: <rack><slot> (Bsp: 0:35 = 035 = 35)
# ═══════════════════════════════════════════════════════════════

last_credit_count = 0
sale_in_progress = False
sale_price_cent = 0       # Preis aus viewprice
current_sale_slot = None
last_erog_time = 0        # epoch seconds

# Für die Verkaufserkennung
sale_credit_before = 0    # Kredit-Wert bei erog
sale_is_card = False      # Wurde 4900 (Karte) gesehen?
sale_credit_price = None  # Preis aus erstem Kredit-Drop während erog (Cash)

# ── Reboot-Erkennung ─────────────────────────────────────────
last_data_ts = 0          # Zeitstempel der letzten Datenzeile
reboot_notified = False   # Ob wir für aktuellen Boot bereits benachrichtigt haben
readpar_count = 0          # Zähler für readpar-Burst während Boot
reboot_cooldown_ts = 0    # Cooldown: frühester nächster Reboot-Alarm


def detect_reboot(cmd, parsed, state):
    """
    Erkennt einen Automat-Neustart anhand der Boot-Sequenz:
    Nach einem Stromausfall sendet der Automat einen Burst von
    readpar-Befehlen + writeclock + commandsversion.
    Diese Befehle kommen im Normalbetrieb NIE vor.
    """
    global last_data_ts, reboot_notified, readpar_count, reboot_cooldown_ts
    
    now = time.time()
    
    # readpar = klares Boot-Signal (kommt nur beim Neustart)
    if cmd.startswith("readpar ") or cmd.startswith("writeclock"):
        gap = now - last_data_ts if last_data_ts > 0 else 999
        
        # Mindestens 60s seit letzter Datenzeile = Reboot
        if gap > 60 and not reboot_notified and now > reboot_cooldown_ts:
            reboot_notified = True
            
            # Temperatur vor Reboot merken falls verfügbar
            temps = state.get("temperatures", {})
            z1 = temps.get("zone1", "?")
            z2 = temps.get("zone2", "?")
            
            gap_min = int(gap // 60)
            gap_sec = int(gap % 60)
            
            msg = (
                f"⚡ <b>Automat-Neustart erkannt!</b>\n"
                f"⏱ Ca. {gap_min}:{gap_sec:02d} Min offline\n"
                f"🔄 Boot um {datetime.now().strftime('%H:%M:%S')} Uhr\n"
                f"🌡 Zone 1: {z1}°C | Zone 2: {z2}°C"
            )
            telegram_send(msg)
            log(f"⚡ REBOOT: {gap_min}m{gap_sec}s offline – Telegram gesendet")
    
    last_data_ts = now  # Immer aktualisieren
    
    # Nach 30s ohne readpar zurücksetzen (nächster Reboot erkennbar)
    if not cmd.startswith("readpar ") and reboot_notified:
        readpar_count = 0


def telegram_send(text):
    """Sendet eine Nachricht via Bot-API (konfigurierter Chat)"""
    if not TELEGRAM_ENABLED:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = json.dumps({
            "chat_id": TELEGRAM_TRADING_GROUP,
            "text": text,
            "parse_mode": "HTML",
            "disable_notification": False
        }).encode()
        req = Request(url, data=payload, headers={"Content-Type": "application/json"})
        urlopen(req, timeout=10)
    except Exception as e:
        log(f"⚠️  Telegram-Fehler: {e}")


# ── Fehler-Erkennung + Telegram-Alert ────────────────────────
# Bekannte Fehlercodes (Byte/Code) – wird bei Bedarf erweitert
ERROR_DESCRIPTIONS = {
    (41, 54): "Blockade in der Ausgabe (Produkt klemmt?)",
    (16, 1): "Unbekannter Fehler (16/1)",
    (32, 0): "Unbekannter Fehler (32/0)",
}

error_alert_timestamps = {}  # key "byte/code" -> letzter Alert-Zeitstempel
ERROR_ALERT_COOLDOWN = 600   # gleicher Fehler max. alle 10 Min melden


def detect_error_change(state, old_err, err):
    """
    Meldet Fehler-Wechsel nach Telegram:
    - kein Fehler → Fehler:   "⚠️ Automat meldet Fehler"
    - Fehler → kein Fehler:   "✅ Fehler behoben"
    - Erster Wert nach Start: wird nur gemerkt, NICHT gemeldet
      (verhindert Fehlalarme für alte, längst bekannte Fehler)
    """
    if old_err is None:
        return  # Baseline nach Listener-Start – nicht alerteen

    old_active = old_err.get("byte") != 0 or old_err.get("code") != 0
    new_active = err.get("byte") != 0 or err.get("code") != 0

    if old_active == new_active:
        return  # kein aktiv/inaktiv-Wechsel

    now = time.time()
    now_str = datetime.now().strftime("%H:%M")
    temps = state.get("temperatures", {})
    z1 = temps.get("zone1", "?")
    z2 = temps.get("zone2", "?")

    if new_active:
        # Cooldown nur für AKTIV-Meldungen (Spam-Schutz bei Dauerfehler)
        key = f"{err['byte']}/{err['code']}"
        if now - error_alert_timestamps.get(key, 0) < ERROR_ALERT_COOLDOWN:
            return
        error_alert_timestamps[key] = now

        desc = ERROR_DESCRIPTIONS.get((err["byte"], err["code"]), "Unbekannter Fehler")
        msg = (
            f"⚠️ <b>Automat meldet Fehler!</b>\n"
            f"🔧 Code: {err['byte']}/{err['code']} ({desc})\n"
            f"🕒 {now_str} Uhr\n"
            f"🌡 Zone 1: {z1}°C | Zone 2: {z2}°C"
        )
        telegram_send(msg)
        log(f"⚠️ Fehler-Alert gesendet: {err['byte']}/{err['code']}")
    else:
        # „Behoben“ wird IMMER gemeldet – ist ein einmaliges Signal pro Fehler-Phase
        desc = ERROR_DESCRIPTIONS.get((old_err["byte"], old_err["code"]), "Unbekannter Fehler")
        msg = (
            f"✅ <b>Fehler behoben!</b>\n"
            f"🔧 Code: {old_err['byte']}/{old_err['code']} ({desc})\n"
            f"🕒 {now_str} Uhr\n"
            f"🌡 Zone 1: {z1}°C | Zone 2: {z2}°C"
        )
        telegram_send(msg)
        log(f"✅ Fehler-behoben-Alert gesendet: {old_err['byte']}/{old_err['code']}")


# ═══════════════════════════════════════════════════════════════
#  STUCK-STATE-WATCHDOG (Blockade-Erkennung)
# ═══════════════════════════════════════════════════════════════
#  Erkennt hängende MECHANISCHE Zustände (z.B. dauerhaft "take"):
#  Wenn selstate länger als STUCK_ALERT_DELAY_S ununterbrochen in einem
#  Blockade-Zustand hängt → Telegram-Warnung, danach stündliche
#  Wiederholung, bis der Automat wieder auf "enderog" ist ("behoben").
#
#  WICHTIG (seit 05.09.2026): Nur take/busy/erog sind echte Blockade-
#  Zustände! viewprice (Preisanzeige) und ageprotection (Alterscheck)
#  blockieren den Automaten NICHT – Kunden können dort normal kaufen
#  (bestätigt vor Ort). Diese Zustände lösen daher KEINE Alarme aus.
#  Reiner Beobachter – fasst Verkaufserkennung/DB nicht an.
STUCK_ALERT_DELAY_S = 300        # 5 Minuten im Blockade-Zustand → 1. Warnung
STUCK_REPEAT_INTERVAL_S = 3600   # weitere Warnungen stündlich
# Nur diese Zustände blockieren den Verkauf wirklich (Mechanik hängt):
STUCK_WATCH_STATES = {"take", "busy", "erog"}

stuck_phase_start = 0.0    # epoch: Beginn der Blockade-Episode (für Dauer-Meldung)
stuck_since_ts = 0.0       # epoch: Beginn des aktuellen ununterbrochenen Zustands
stuck_state_label = None   # aktueller Blockade-Zustand (Timer-Logik)
stuck_alert_label = None   # Zustand, für den der letzte Alarm galt ("behoben"-Text)
stuck_alert_sent = False   # Warnung in dieser Episode schon gesendet?
stuck_last_alert_ts = 0.0  # epoch der letzten Warnung


def stuck_watchdog(val):
    """
    Wird bei JEDER selstate-Zeile aufgerufen (auch ohne Zustandswechsel).
    val = erster Wert aus selstate (z.B. "take", "busy", "enderog").
    """
    global stuck_phase_start, stuck_since_ts, stuck_state_label
    global stuck_alert_label, stuck_alert_sent, stuck_last_alert_ts
    now = time.time()

    if val == "enderog":
        # Automat wieder bereit – blockierte Episode beenden
        if stuck_alert_sent:
            dur_min = int((now - stuck_phase_start) // 60) if stuck_phase_start else 0
            alert_label = stuck_alert_label or stuck_state_label or "?"
            msg = (
                f"✅ <b>Automat wieder bereit</b>\n"
                f"Zustand „{alert_label}“ war ca. {dur_min} Min blockiert"
            )
            telegram_send(msg)
            log(f"✅ Stuck-State beendet ({alert_label}, ~{dur_min} min)")
        stuck_phase_start = 0.0
        stuck_since_ts = 0.0
        stuck_state_label = None
        stuck_alert_label = None
        stuck_alert_sent = False
        stuck_last_alert_ts = 0.0
        return

    # Nur mechanische Blockade-Zustände überwachen. viewprice/ageprotection
    # (und andere Anzeige-/Wartezustände) blockieren NICHT → Timer UND
    # Zustands-Label zurücksetzen (Unterbrechung der Blockade-Phase),
    # damit der nächste Blockade-Zustand frisch startet. Keine
    # "behoben"-Meldung hier – die kommt erst bei enderog.
    if val not in STUCK_WATCH_STATES:
        stuck_since_ts = 0.0
        stuck_state_label = None
        return

    if stuck_state_label != val or stuck_since_ts == 0.0:
        # Neuer Blockade-Zustand ODER Timer wurde durch einen Zwischenzustand
        # (z.B. viewprice) zurückgesetzt → Timer frisch starten.
        # Episoden-Beginn nur einmal setzen (für die "behoben"-Dauer).
        if stuck_phase_start == 0.0:
            stuck_phase_start = now
        stuck_since_ts = now
        stuck_state_label = val
        return

    # Gleicher Blockade-Zustand hält ununterbrochen an
    dur_s = now - stuck_since_ts
    if dur_s < STUCK_ALERT_DELAY_S:
        return

    if not stuck_alert_sent:
        # Erste Warnung nach 5 Minuten
        stuck_alert_sent = True
        stuck_alert_label = val
        stuck_last_alert_ts = now
        _send_stuck_alert(val, dur_s, repeat=False)
    elif now - stuck_last_alert_ts >= STUCK_REPEAT_INTERVAL_S:
        # Stündliche Wiederholung, solange blockiert
        stuck_last_alert_ts = now
        _send_stuck_alert(val, dur_s, repeat=True)


def _send_stuck_alert(val, dur_s, repeat):
    """Telegram-Warnung für eine anhaltende Blockade (1. Alarm/Wiederholung)."""
    dur_min = int(dur_s // 60)
    dur_txt = f"{dur_min // 60} h {dur_min % 60} min" if dur_min >= 60 else f"{dur_min} min"
    prefix = ("🔁 <b>Automat weiterhin blockiert</b>" if repeat
              else "⚠️ <b>Automat blockiert?</b>")
    msg = (
        f"{prefix}\n"
        f"🚦 Zustand: {val}\n"
        f"⏱ Bereits ~{dur_txt} nicht auf „bereit“\n"
        f"🕒 {datetime.now().strftime('%H:%M')} Uhr\n"
        f"🔧 Automat/Entnahmefach prüfen!"
    )
    telegram_send(msg)
    log(f"{'🔁' if repeat else '⚠️'} Stuck-Alert: {val} seit ~{dur_txt}")


def _get_today_total(db_path, today_prefix, current_amount_eur):
    """Summiert heutige Verkäufe inkl. des aktuellen."""
    total = float(current_amount_eur)
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        cur = conn.cursor()
        cur.execute(
            "SELECT COALESCE(SUM(price_eur), 0) FROM sales WHERE timestamp LIKE ?",
            (today_prefix + "%",)
        )
        total += float(cur.fetchone()[0])
        conn.close()
    except Exception:
        pass
    return round(total, 2)


def _finalize_sale(state, now, current_sale_slot, price, payment_type, credit_before):
    """
    Führt einen Verkauf final aus: Telegram + DB-Write.
    
    Args:
        state: Aktueller Automaten-Status (für Katalog + Temperaturen)
        now: datetime-Objekt
        current_sale_slot: Slot-Nummer als String (z.B. "52", "167")
        price: Preis in Cent
        payment_type: "card" oder "cash"
        credit_before: Kredit vor dem Verkauf
    """
    slot_str = f"Slot {current_sale_slot}" if current_sale_slot else "unbekannt"
    amount_eur = price / 100.0
    
    temps = state.get("temperatures", {})
    z1 = temps.get("zone1", "?")
    z2 = temps.get("zone2", "?")
    
    pay_icon = "💳 Karte" if payment_type == "card" else "💵 Cash"
    
    product_name = None
    if current_sale_slot:
        cat_entry = _get_catalog_entry(str(current_sale_slot))
        if cat_entry:
            product_name = cat_entry.get("name")
    
    # Tagesumsatz (inkl. dieses Verkaufs)
    today_prefix = now.strftime("%Y-%m-%dT")
    daily_total = _get_today_total(DB_FILE, today_prefix, amount_eur)
    
    msg = (
        f"🛒 <b>Verkauf ({now.strftime('%H:%M')} Uhr)</b>\n"
        f"💰 {amount_eur:.2f}€ {pay_icon}\n"
        f"📍 {slot_str}"
    )
    if product_name:
        msg += f" — {product_name}\n"
    else:
        msg += "\n"
    msg += f"📊 Tagesumsatz: {daily_total:.2f}€\n"
    msg += f"🌡  Zone 1: {z1}°C | Zone 2: {z2}°C"
    telegram_send(msg)
    log(f"📨 Telegram: {amount_eur:.2f}€ {slot_str}")
    
    write_sale_to_db(
        ts=now.isoformat(),
        slot=current_sale_slot,
        price_cent=price,
        price_eur=amount_eur,
        payment=payment_type,
        credit_before=credit_before
    )
    
    # Preis-Mirror: tatsächlich gezahlten Preis in den Catalog schreiben
    if current_sale_slot:
        try:
            conn = sqlite3.connect(DB_FILE, timeout=5)
            conn.execute(
                "UPDATE catalog SET price_cent = ? WHERE slot = ? AND price_cent != ?",
                (price, current_sale_slot, price)
            )
            if conn.total_changes > 0:
                log(f"📋  Catalog-Preis gespiegelt: Slot {current_sale_slot} = {amount_eur:.2f}€")
            conn.commit()
            conn.close()
        except Exception as e:
            log(f"⚠️  DB-Fehler (catalog price mirror): {e}")


def detect_sale(line, parsed, state):
    """
    Erkennt VERKÄUFE anhand der Protokoll-Sequenz.
    
    STRUKTURELLE TRENNUNG:
    💳 KARTE (credit_before == 4900):
       - Signal: Kredit 4900 (Kartenterminal-Reserve)
       - Preis:  PRIO 1 viewprice → PRIO 2 Preiscache → PRIO 3 Kredit-Drop (wenn != 4900)
       - Abschluss: take (bei Card mit take) oder enderog
       - Alter:   `select 1 <slot>` = Altersprüfung via Karte
    
    💵 CASH (credit_before < 4900):
       - Signal: Kredit < 4900 (eingeworfener Betrag)
       - Preis:  Kredit-Drop (Differenz während erog)
       - Abschluss: take (Ware wird entnommen)
    """
    global last_credit_count, sale_in_progress, sale_price_cent
    global current_sale_slot, last_erog_time
    global sale_credit_before, sale_is_card, sale_credit_price

    now = datetime.now()

    cmd = parsed.get("command", "")

    # ── Slot-Erkennung: select <rack> <slot> ... ────────
    if cmd.startswith("select "):
        parts = cmd.split()
        if len(parts) >= 3:
            rack = int(parts[1])
            slot_num = int(parts[2])
            current_sale_slot = str(rack * 100 + slot_num)
            # viewprice NICHT zurücksetzen – kann vor select kommen

    # ── viewprice: Preis merken (für Cash & Karte) ──
    if "selstate" in cmd:
        new_state = parsed.get("state", "")

        if new_state.startswith("viewprice"):
            # viewprice-Preis kommt jetzt korrekt aus dem Parser
            vp = parsed.get("viewprice_cent", 0)
            if vp and 1 <= vp <= 10000:
                sale_price_cent = vp

    # ── readcredit 4900 = Karte erkannt ───────────────
    #    Bei Cash: ersten Kredit-Drop als Preis merken
    if "readcredit" in cmd:
        cu = parsed.get("credit_units", 0)
        if cu == 4900 and not sale_in_progress:
            sale_is_card = True
            log(f"   💳 Kartenzahlung erkannt (Kredit 4900)")
        # Bei laufendem Verkauf: Kredit-Drop tracken (für Cash & Karte)
        if sale_in_progress:
            if sale_credit_price is None and cu < sale_credit_before and cu >= 0:
                diff = sale_credit_before - cu
                if 1 <= diff <= 10000:
                    sale_credit_price = diff
                    icon = "💳" if sale_is_card else "💵"
                    log(f"   {icon} Kredit-Drop: {sale_credit_before}→{cu} = {diff} Cent")

    # ── selstate Continue für erog/take ─────────────────
    if "selstate" in cmd:
        new_state = parsed.get("state", "")

        # 🔥 Echter Verkauf: erog = Ausgabe läuft!
        if new_state.startswith("erog"):
            if time.time() - last_erog_time > 3:
                last_erog_time = time.time()
                if not sale_in_progress:
                    sale_in_progress = True
                    
                    # Kredit-Vorher: LIVE aus aktuellen Automaten-Daten
                    credit = state.get("credit", {})
                    sale_credit_before = credit.get("units", 0) or 0
                    sale_is_card = (sale_credit_before == 4900)
                    
                    log(f"🛒  VERKAUF ({current_sale_slot or '?'}) | "
                        f"viewprice={sale_price_cent or '?'} Cent | "
                        f"credit_before={sale_credit_before} | "
                        f"{'💳 Karte' if sale_is_card else '💵 Cash'}")

        # ── 💳💵 SALE ABSCHLIESSEN (enderog) ─────────────
        # BEIDE Zahlungsarten schliessen bei enderog ab:
        #   💳 Karte:  credit_before=4900 → Preis aus Slot-Tabelle
        #   💵 Cash:   credit_before<4900 → Preis aus Kredit-Differenz
        # Ausnahme: manche Slots haben take vor enderog (z.B. Slot 52 ohne Altersprüfung)
        if new_state.startswith("enderog"):
            if sale_in_progress:
                if time.time() - last_erog_time < 45:
                    price = None
                    
                    if sale_is_card:
                        # 💳 KARTE: Preis live aus Stream holen (kein Log-Burst nötig)
                        slot_idx = str(current_sale_slot) if current_sale_slot else None
                        price = None
                        
                        # PRIO 1: Preiscache (Slot-Tabelle) — zuverlässig, nicht von hängendem viewprice beeinflusst
                        if slot_idx:
                            prices = state.get("prices", {})
                            slot_price = prices.get(slot_idx, {}).get("price_cent")
                            if slot_price and 1 <= slot_price <= 10000:
                                price = slot_price
                                log(f"   💳 Kartenzahlung {price} Cent (Preiscache Slot {slot_idx})")
                        
                        # PRIO 2: viewprice (Fallback, wenn Slot nicht in Preistabelle)
                        if price is None and sale_price_cent and 1 <= sale_price_cent <= 10000:
                            price = sale_price_cent
                            log(f"   💳 Kartenzahlung {price} Cent (viewprice, Slot {slot_idx})")
                        
                        # PRIO 3: Kredit-Differenz (wenn beides fehlschlägt)
                        if price is None and sale_credit_price:
                            # 4900 = volle Kartengutschrift kann nie der Preis sein
                            if sale_credit_price == 4900:
                                log(f"   ⚠️  Kredit-Drop = 4900 (volle Reserve, kein Preis)")
                            else:
                                price = sale_credit_price
                                log(f"   💳 Kartenzahlung {price} Cent (Kredit-Drop, Slot {slot_idx})")
                        
                        if price:
                            _finalize_sale(state, now, current_sale_slot, price, "card", sale_credit_before)
                        else:
                            log(f"   ⚠️  Karte aber kein Preis für Slot {slot_idx} (viewprice/Preiscache/Kredit-Drop alle leer)")
                            log(f"⏭  Kartenzahlung ohne Preis - ignoriert")
                    else:
                        # 💵 CASH: Preis aus Kredit-Drop (während erog getrackt)
                        if sale_credit_price and 1 <= sale_credit_price <= 10000:
                            price = sale_credit_price
                            log(f"   💵 Barzahlung {price} Cent (Kredit-Drop)")
                            _finalize_sale(state, now, current_sale_slot, price, "cash", sale_credit_before)
                        else:
                            log(f"   ⚠️  Kein Kredit-Drop erfasst (bp={sale_credit_before})")
                            log(f"⏭  Barzahlung ohne Preis - ignoriert")
                else:
                    log(f"⏭  enderog ohne erog in 45s - ignoriert")
                
                # IMMER aufräumen – sonst klemmt sale_in_progress
                sale_in_progress = False
                sale_price_cent = 0
                sale_credit_before = 0
                sale_is_card = False
                sale_credit_price = None
                current_sale_slot = None

        # ── FALLBACK: Sale abschliessen bei take ───────────
        # (nur für Slots OHNE Altersprüfung, die take vor enderog haben)
        if new_state.startswith("take"):
            if sale_in_progress:
                if time.time() - last_erog_time < 45:
                    price = None
                    
                    if sale_is_card:
                        slot_idx = str(current_sale_slot) if current_sale_slot else None
                        price = None
                        
                        # PRIO 0: Preiscache (zuverlässigster Weg, siehe enderog-Handler)
                        if slot_idx:
                            prices = state.get("prices", {})
                            slot_price = prices.get(slot_idx, {}).get("price_cent")
                            if slot_price and 1 <= slot_price <= 10000:
                                price = slot_price
                                log(f"   💳 Kartenzahlung {price} Cent (Preiscache, {slot_idx}, take)")
                        
                        # PRIO 1: viewprice
                        if price is None and sale_price_cent and 1 <= sale_price_cent <= 10000:
                            price = sale_price_cent
                            log(f"   💳 Kartenzahlung {price} Cent (viewprice, {slot_idx}, take)")
                        
                        # PRIO 2: Kredit-Differenz
                        if price is None and sale_credit_price:
                            if sale_is_card and sale_credit_price == 4900:
                                log(f"   ⚠️  Kredit-Drop = 4900 (volle Reserve, kein Preis)")
                            else:
                                price = sale_credit_price
                                log(f"   💳 Kartenzahlung {price} Cent (Kredit-Drop, {slot_idx}, take)")
                        
                        if price:
                            _finalize_sale(state, now, current_sale_slot, price, "card", sale_credit_before)
                        else:
                            log(f"   ⚠️  Karte kein Preis für {slot_idx} (take)")
                    else:
                        # 💵 CASH: Preis aus Kredit-Drop (während erog getrackt)
                        if sale_credit_price and 1 <= sale_credit_price <= 10000:
                            price = sale_credit_price
                            log(f"   💵 Barzahlung {price} Cent (Kredit-Drop, take)")
                        else:
                            # Fallback: Kredit-Differenz bei enderog
                            credit = state.get("credit", {})
                            sale_credit_after = credit.get("units", 0) or 0
                            diff = sale_credit_before - sale_credit_after
                            if 1 <= diff <= 10000:
                                price = diff
                                log(f"   ⚠️  Cash-Fallback: Kredit-Diff {diff} (take)")
                        if price:
                            _finalize_sale(state, now, current_sale_slot, price, "cash", sale_credit_before)
                        else:
                            log(f"⏭  Barzahlung ohne Preis - ignoriert (take)")
                else:
                    log(f"⏭  take ohne erog in 45s - ignoriert")
                
                sale_in_progress = False
                sale_price_cent = 0
                sale_credit_before = 0
                sale_is_card = False
                sale_credit_price = None
                current_sale_slot = None

        # ── Automat in "other" – Sale abgebrochen ───────────
        if new_state.startswith("other"):
            if sale_in_progress:
                log(f"   ⚠️  Automat in 'other' – Sale abgebrochen (Slot {current_sale_slot})")
                sale_in_progress = False
                sale_price_cent = 0
                sale_credit_before = 0
                sale_is_card = False
                sale_credit_price = None
                current_sale_slot = None

    # ── Kredit nur fürs Log und Fallback-Preis ──────────
    if cmd == "readcredit":
        new_count = parsed.get("credit_count", 0) or 0
        if new_count != last_credit_count and new_count > 0:
            last_credit_count = new_count
            log(f"💰  Kredit geladen: {parsed.get('credit_units', 0)} Cent (Verkäufe: {last_credit_count})")

    # Temperatur-Logging alle 11 Sekunden unterdrücken wir
    # indem wir nur relevante States loggen
    # -> erledigt im process_line


# ═══════════════════════════════════════════════════════════════
#  HAUPTLOOP
# ═══════════════════════════════════════════════════════════════

def listen_forever():
    """
    Endlose Hauptschleife (v2 - non-blocking loop).
    - Verbindet zu HOST:PORT
    - Liest alle eingehenden Daten (non-blocking mit Polling)
    - Verarbeitet sie (parser + state)
    - Bei Verbindungsabbruch: warten und neu verbinden
    """
    state = {}  # Aktueller Zustand (wächst mit jedem neuen Datentyp)
    # Log-Rotation: raw_stream.log alle 24h oder >5MB rotieren
    rotate_log_if_needed()
    # Preise aus current_state.json laden (letzter Live-Stand)
    # Der Live-Listener speichert readprice-Befehle dort via save_state()
    state["prices"] = {}
    try:
        with open(STATE_FILE) as f:
            saved_state = json.load(f)
            saved_prices = saved_state.get("prices", {})
            if saved_prices:
                state["prices"] = saved_prices
                log(f"🏷  Preise aus current_state geladen: {len(saved_prices)} Slots")
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    
    # SQLite-DB initialisieren (Tabellen anlegen falls nicht vorhanden)
    _ensure_sales_table()
    _ensure_catalog_table()
    _migrate_catalog_from_json()

    # Reboot-Detection zurücksetzen (nach Neustart des Listeners)
    global last_data_ts, reboot_notified, readpar_count
    last_data_ts = time.time()
    reboot_notified = False
    readpar_count = 0

    while True:
        # ── Verbinden ───────────────────────────────────────
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.0)   # 1s recv-Timeout für regelmäßige Polls
            s.connect((HOST, PORT))
            log(f"✅ Verbunden mit {HOST}:{PORT}")
        except Exception as e:
            log(f"❌ Verbindung fehlgeschlagen: {e}")
            time.sleep(RECONNECT_DELAY)
            continue

        # ── Initialen Daten-Burst empfangen ────────────────
        # Mehrere recv-Aufrufe, bis 3s Pause oder Ende
        initial_data = b""
        s.settimeout(1.0)
        for _ in range(6):  # max 6 x 1s = 6s warten
            try:
                chunk = s.recv(8192)
                if not chunk:
                    break
                initial_data += chunk
            except socket.timeout:
                break
        if initial_data:
            text = initial_data.decode("latin1", errors="replace")
            for line in text.split("\r\n"):
                line = line.strip()
                if line:
                    process_line(line, state, None)
            log(f"📦 Initial-Burst: {len(initial_data)} Bytes, "
                f"{len(state.get('prices', {}))} Preise geladen")

        # ── Polling-Loop mit 1s recv-Timeout ─────────────
        s.settimeout(1.0)
        buffer = b""
        idle_cycles = 0
        while True:
            try:
                chunk = s.recv(8192)
                if not chunk:
                    log("⚠️ Verbindung vom Automaten geschlossen")
                    break

                idle_cycles = 0  # Daten empfangen

                # Pufferverwaltung für fragmentierte TCP-Pakete
                buffer += chunk
                text = buffer.decode("latin1", errors="replace")

                # Nach Zeilenumbrüchen splitten
                while "\r\n" in text:
                    line, text = text.split("\r\n", 1)
                    line = line.strip()
                    if line:
                        process_line(line, state, None)

                buffer = text.encode("latin1", errors="replace")

            except socket.timeout:
                # Normal: kein Data in diesem 1s-Fenster
                idle_cycles += 1
                # Alle 600 Zyklen (~10 min) einen Heartbeat-Log
                # (damit wir sehen dass die Verbindung noch lebt)
                if idle_cycles % 600 == 0:
                    idle_mins = idle_cycles // 60
                    log(f"💓 Heartbeat ({idle_mins} min ohne Daten)")
                # Watchdog: nach 5 Minuten ohne Daten → Reconnect
                # (verhindert Zombie-Verbindungen, wenn der Automat neustartet
                #  oder die Leitung tot ist, der Socket aber offen bleibt)
                if idle_cycles >= 12:  # 12 * 1s = 12 Sekunden
                    idle_secs = idle_cycles
                    log(f"🔄 Watchdog: {idle_secs}s ohne Daten – Reconnect")
                    break
                continue
            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                log(f"⚠️ Verbindungsfehler: {e}")
                break
            except Exception as e:
                log(f"❌ Unerwarteter Fehler: {e}")
                break

        s.close()
        log(f"🔌 Neustart in {RECONNECT_DELAY}s...")
        time.sleep(RECONNECT_DELAY)


# ═══════════════════════════════════════════════════════════════
#  EINSTIEGSPUNKT
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import signal
    import atexit
    signal.signal(signal.SIGTERM, lambda *a: (cleanup_lock(), sys.exit(0)))
    signal.signal(signal.SIGINT, lambda *a: (cleanup_lock(), sys.exit(0)))
    atexit.register(cleanup_lock)
    check_lock()
    log("🚀 VaS 1050 Live-Listener gestartet")
    log(f"📁 Automat: {HOST}:{PORT}")
    log(f"📁 State:   {STATE_FILE}")
    log(f"📝 Raw:     {RAW_FILE}")
    log(f"💾 DB:      {DB_FILE}")
    log("=" * 50)
    log("📡 Warte auf Daten vom Automaten...")
    listen_forever()
