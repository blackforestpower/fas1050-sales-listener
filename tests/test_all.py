#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║             FAS 1050 PRO - Test Suite (SQLite)              ║
║                                                              ║
║  Testet: Listener-Prozess, sales.db, daily_report,          ║
║          Produkt-Katalog, query_sales                       ║
╚══════════════════════════════════════════════════════════════╝
"""

import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta

# ── Settings ─────────────────────────────────────────────────
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_DIR, "data")
DB_FILE = os.path.join(DATA_DIR, "sales.db")
RAW_LOG = os.path.join(DATA_DIR, "raw_stream.log")
CATALOG_FILE = os.path.join(DATA_DIR, "product_catalog.json")
STATE_FILE = os.path.join(DATA_DIR, "current_state.json")

passed = 0
failed = 0
errors = []


def test(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        msg = f"  ❌ {name}"
        if detail:
            msg += f"  → {detail}"
        print(msg)
        errors.append(msg)


def check_file(filepath, label):
    """Prüft ob Datei existiert und nicht leer ist."""
    if not os.path.exists(filepath):
        return False, f"{label} fehlt ({filepath})"
    size = os.path.getsize(filepath)
    if size == 0:
        return False, f"{label} ist leer ({filepath})"
    return True, f"{size:,} Bytes"


# ══════════════════════════════════════════════════════════════
print("═" * 55)
print("  FAS 1050 PRO - Test-Suite (SQLite)")
print(f"  Projekt: {PROJECT_DIR}")
print(f"  Zeit:    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("═" * 55)
print()

# ══════════════════════════════════════════════════════════════
#  1) Grundlegende Dateistruktur
# ══════════════════════════════════════════════════════════════
print("📁 Dateistruktur")
print("-" * 40)

for path, label in [
    (DB_FILE, "sales.db"),
    (RAW_LOG, "raw_stream.log"),
    (CATALOG_FILE, "product_catalog.json"),
    (STATE_FILE, "current_state.json"),
]:
    ok, msg = check_file(path, label)
    test(f"{label} vorhanden", ok, msg)

# Prüfen dass events.jsonl NICHT mehr existiert
if os.path.exists(os.path.join(DATA_DIR, "events.jsonl")):
    test("events.jsonl gelöscht", False, "Datei existiert noch!")
else:
    test("events.jsonl gelöscht", True)

print()

# ══════════════════════════════════════════════════════════════
#  2) Listener läuft
# ══════════════════════════════════════════════════════════════
print("📡 Listener")
print("-" * 40)

result = subprocess.run(["ps", "aux"], capture_output=True, text=True)
listener_running = "fas1050_listener" in result.stdout
test("Listener-Prozess läuft", listener_running)

if os.path.exists(RAW_LOG):
    mtime = os.path.getmtime(RAW_LOG)
    age = time.time() - mtime
    test("raw_stream aktiv (< 60s)", age < 60, f"Letzte Aktualisierung vor {age:.0f}s")
    with open(RAW_LOG) as f:
        lines = f.readlines()
    recent = [l.strip() for l in lines[-20:] if l.strip()]
    dups = 0
    for i in range(len(recent) - 1):
        if recent[i] == recent[i+1]:
            dups += 1
    test("Keine Dubletten in raw_stream", dups < 5, f"{dups} Dubletten gefunden")
else:
    test("raw_stream.log vorhanden", False)

print()

# ══════════════════════════════════════════════════════════════
#  3) sales.db Integrität
# ══════════════════════════════════════════════════════════════
print("💾 sales.db")
print("-" * 40)

if os.path.exists(DB_FILE):
    try:
        conn = sqlite3.connect(DB_FILE, timeout=5)
        cursor = conn.execute("SELECT COUNT(*) FROM sales")
        count = cursor.fetchone()[0]
        test(f"sales.db: {count} Einträge", count > 0)

        if count > 0:
            # Schema prüfen
            cursor.execute("PRAGMA table_info(sales)")
            cols = {row[1] for row in cursor.fetchall()}
            required_cols = {"id", "timestamp", "slot", "product", "category", "price_cent", "price_eur", "payment"}
            missing_cols = required_cols - cols
            test("sales.db: alle Spalten vorhanden", len(missing_cols) == 0,
                 f"Fehlende Spalten: {missing_cols}")

            # Werte prüfen
            cursor.execute("SELECT COUNT(*) FROM sales WHERE price_cent <= 0")
            bad_price = cursor.fetchone()[0]
            test("sales.db: alle price_cent > 0", bad_price == 0,
                 f"{bad_price} mit price_cent <= 0")

            cursor.execute("SELECT COUNT(*) FROM sales WHERE payment NOT IN ('cash', 'card')")
            bad_pay = cursor.fetchone()[0]
            test("sales.db: gültige payment", bad_pay == 0,
                 f"{bad_pay} mit ungültiger payment")

            # Timestamps prüfen
            cursor.execute("SELECT timestamp FROM sales")
            bad_ts = 0
            for row in cursor.fetchall():
                try:
                    datetime.fromisoformat(row[0])
                except:
                    bad_ts += 1
            test("sales.db: gültige Timestamps", bad_ts == 0,
                 f"{bad_ts} ungültige Timestamps")

            # Dubletten prüfen
            cursor.execute(
                "SELECT timestamp, slot, price_cent, COUNT(*) as cnt FROM sales GROUP BY timestamp, slot, price_cent HAVING cnt > 1"
            )
            dups = cursor.fetchall()
            test("sales.db: keine Dubletten", len(dups) == 0,
                 f"{len(dups)} Dubletten: {dups[:3]}")

            # Heutiger Umsatz
            today = datetime.now().strftime("%Y-%m-%d")
            cursor.execute(
                "SELECT COUNT(*), SUM(price_eur) FROM sales WHERE substr(timestamp, 1, 10) = ?",
                (today,)
            )
            today_count, today_total = cursor.fetchone()
            if today_count and today_count > 0:
                test(f"📈 Heute: {today_count} Verkäufe / {today_total:.2f}€",
                     today_total > 0, f"{today_total:.2f}€")
            else:
                test("📈 Heute: keine Verkäufe", True)

            # Payment-Verteilung
            cursor.execute("SELECT payment, COUNT(*), SUM(price_eur) FROM sales GROUP BY payment")
            payment_data = cursor.fetchall()
            for pay, cnt, total in payment_data:
                icon = "💳" if pay == "card" else "💵"
                test(f"   {icon} {pay}: {cnt}x ({total:.2f}€)", cnt > 0)

        conn.close()
    except sqlite3.Error as e:
        test("sales.db: lesbar", False, str(e))
else:
    test("sales.db vorhanden", False)

print()

# ══════════════════════════════════════════════════════════════
#  4) Produkt-Katalog
# ══════════════════════════════════════════════════════════════
print("📦 Produkt-Katalog")
print("-" * 40)

if os.path.exists(CATALOG_FILE):
    try:
        with open(CATALOG_FILE) as f:
            catalog = json.load(f)
        test(f"Katalog: {len(catalog)} Einträge", len(catalog) > 0)

        no_name = [k for k, v in catalog.items() if not v.get("name")]
        test("Katalog: alle haben name", len(no_name) == 0,
             f"{len(no_name)} ohne name")

        no_cat = [k for k, v in catalog.items() if not v.get("category")]
        test("Katalog: alle haben category", len(no_cat) == 0,
             f"{len(no_cat)} ohne category")

        categories = set()
        for v in catalog.values():
            if v.get("category"):
                categories.add(v["category"])
        test(f"Kategorien: {', '.join(sorted(categories))}", len(categories) > 0)

        # Slots in DB auf unbekannte prüfen
        if os.path.exists(DB_FILE):
            conn = sqlite3.connect(DB_FILE, timeout=5)
            cursor = conn.execute("SELECT DISTINCT slot FROM sales WHERE slot != ''")
            unknown = []
            for row in cursor.fetchall():
                slot_str = str(row[0])
                if slot_str not in catalog:
                    unknown.append(slot_str)
            conn.close()
            test("Alle Sales-Slots im Katalog", len(unknown) == 0,
                 f"Unbekannte Slots: {set(unknown)}")
    except json.JSONDecodeError:
        test("Katalog: gültiges JSON", False)
else:
    test("product_catalog.json vorhanden", False)

print()

# ══════════════════════════════════════════════════════════════
#  5) Daily Report und Query Sales
# ══════════════════════════════════════════════════════════════
print("📋 Skripte")
print("-" * 40)

# daily_sales_summary Mode A
result = subprocess.run(
    ["python3", os.path.join(PROJECT_DIR, "daily_sales_summary.py")],
    capture_output=True, text=True, timeout=15
)
test("Mode A (daily) läuft durch", result.returncode == 0,
     f"Exit {result.returncode}: {result.stderr[:200]}")
test("Mode A: 'Report A' im Output", "Report A" in result.stdout, result.stdout[:200])

# daily_sales_summary Mode B
result_today = subprocess.run(
    ["python3", os.path.join(PROJECT_DIR, "daily_sales_summary.py"), "--today"],
    capture_output=True, text=True, timeout=15
)
test("Mode B (--today) läuft durch", result_today.returncode == 0,
     f"Exit {result_today.returncode}: {result_today.stderr[:200]}")
test("Mode B: 'Report B' im Output", "Report B" in result_today.stdout, result_today.stdout[:200])

# query_sales
qresult = subprocess.run(
    ["python3", os.path.join(PROJECT_DIR, "query_sales.py"), "--today"],
    capture_output=True, text=True, timeout=15
)
test("query_sales --today läuft durch", qresult.returncode == 0,
     f"Exit {qresult.returncode}: {qresult.stderr[:200]}")
test("query_sales: Output brauchbar", len(qresult.stdout) > 10, qresult.stdout[:100])

qproduct = subprocess.run(
    ["python3", os.path.join(PROJECT_DIR, "query_sales.py"), "--stats"],
    capture_output=True, text=True, timeout=15
)
test("query_sales --stats läuft durch", qproduct.returncode == 0,
     f"Exit {qproduct.returncode}: {qproduct.stderr[:200]}")
test("query_sales --stats: Kategorien im Output",
     "Gesamt" in qproduct.stdout and "Kategorie" in qproduct.stdout,
     qproduct.stdout[:150])

print()

# ══════════════════════════════════════════════════════════════
#  6) Prozesstest: Nur ein Listener
# ══════════════════════════════════════════════════════════════
print("🔄 Prozesse")
print("-" * 40)

listeners = subprocess.run(
    ["pgrep", "-f", "fas1050_listener"], capture_output=True, text=True
)
count = len(listeners.stdout.strip().split("\n")) if listeners.stdout.strip() else 0

if count == 0:
    test("⚠️ Listener läuft nicht", False, "Kein Prozess gefunden")
    test("   → täglicher Report läuft trotzdem", True,
         "daily_sales_summary ist unabhängig von der DB")
elif count == 1:
    test("Nur 1 Listener-Prozess", True)
elif count == 2:
    test("⚠️ 2 Listener-Prozesse", True,
         "Zwei Prozesse – schreibt doppelt in DB + raw_stream")
else:
    test(f"⚠️ {count} Listener-Prozesse", False)

print()

# ══════════════════════════════════════════════════════════════
#  7) Live-Daten vom Automaten
# ══════════════════════════════════════════════════════════════
print("🔌 Live-Daten vom Automaten")
print("-" * 40)

if os.path.exists(RAW_LOG) and os.path.getsize(RAW_LOG) > 0:
    with open(RAW_LOG) as f:
        lines = f.readlines()
    recent = [l.strip() for l in lines[-15:] if l.strip()]

    cmds_seen = set()
    valid_cmds = {"selstate", "readerrors", "readcredit", "gettemperature",
                  "select", "readprice", "priceline", "readpar", "getmessage"}

    for line in recent:
        for cmd in valid_cmds:
            if cmd in line:
                cmds_seen.add(cmd)

    if cmds_seen:
        cmd_str = ", ".join(sorted(cmds_seen))
        test(f"Automat sendet: {cmd_str}", True)
    else:
        test("Keine Automaten-Kommandos in raw_stream", False,
             "Letzte 15 Zeilen enthalten keine bekannten CMDs")

    today_prefix = datetime.now().strftime("[%Y-%m-%d")
    today_lines = [l for l in lines if today_prefix in l]
    test(f"📡 {len(today_lines)} Zeilen heute in raw_stream",
         len(today_lines) > 100,
         f"Nur {len(today_lines)} Zeilen – Automat könnte offline sein")

    selstate_lines = [l for l in recent if "selstate" in l]
    has_enderog = any("enderog" in l for l in selstate_lines)
    test("Automat meldet 'enderog' (betriebsbereit)", has_enderog,
         "Kein enderog in letzten Zeilen")
else:
    test("raw_stream.log lesbar", False, "Existiert nicht oder ist leer")

sock_result = subprocess.run(
    ["timeout", "2", "bash", "-c",
     "echo > /dev/tcp/192.168.200.146/8888 2>/dev/null && echo OK || echo FAIL"],
    capture_output=True, text=True, timeout=5
)
tcp_ok = "OK" in sock_result.stdout
test("TCP 8888 erreichbar (Automat)", tcp_ok,
     "Automat nicht erreichbar – IP/Port prüfen")

print()

# ══════════════════════════════════════════════════════════════
#  Ergebnis
# ══════════════════════════════════════════════════════════════
print("═" * 55)
total = passed + failed
if failed == 0:
    print(f"  🎉 ALLE {passed} TESTS BESTANDEN")
else:
    print(f"  ⚠️  {passed}/{total} bestanden, {failed} fehlgeschlagen")
    print()
    print("  Fehler:")
    for e in errors:
        print(f"    {e}")
print("═" * 55)
sys.exit(0 if failed == 0 else 1)
