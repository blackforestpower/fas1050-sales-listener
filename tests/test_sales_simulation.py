#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║  FAS 1050 PRO - Integrationstest mit Simulation            ║
║                                                              ║
║  Simuliert MDB-Daten und jagt sie durch die komplette      ║
║  detect_sale-Logik des Listeners – prüft ob korrekte       ║
║  Sales erkannt werden und in der SQLite-DB landen.         ║
║                                                              ║
║  Nutzung:                                                    ║
║    python3 tests/test_sales_simulation.py                   ║
╚══════════════════════════════════════════════════════════════╝
"""

import json
import os
import sqlite3
import sys
import time
from datetime import datetime

TEST_SOURCE = "test_simulation"  # Markierung für Test-Sales in der DB

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from fas1050_listener_v4 import parse_line, process_line, detect_sale
from fas1050_listener_v4 import (
    DATA_DIR, RAW_FILE, DB_FILE, CATALOG_FILE, STATE_FILE
)
from fas1050_listener_v4 import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_TRADING_GROUP, TELEGRAM_ENABLED
)

# Katalog laden
with open(CATALOG_FILE) as f:
    CATALOG = json.load(f)


# ── Helper ────────────────────────────────────────────────
def feed_line(line, state, last_state, log=False):
    """Füttert eine raw-Zeile an den Listener."""
    process_line(line, state, last_state)


def count_db_sales():
    if not os.path.exists(DB_FILE):
        return 0
    conn = sqlite3.connect(DB_FILE, timeout=5)
    count = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
    conn.close()
    return count


def db_sales_since(ts_start):
    if not os.path.exists(DB_FILE):
        return []
    conn = sqlite3.connect(DB_FILE, timeout=5)
    cursor = conn.execute(
        "SELECT timestamp, slot, product, price_eur, payment FROM sales WHERE timestamp >= ? ORDER BY timestamp",
        (ts_start,)
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


import fas1050_listener_v4 as fl

passed = 0
failed = 0

def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        msg = f"  ❌ {name}"
        if detail:
            msg += f"  → {detail}"
        print(msg)


# ── DB-Status vor Tests ──────────────────────────────────
# Test-Modus aktivieren: alle Sales in der DB bekommen source='test_simulation'
fl.TEST_MODE_SOURCE = TEST_SOURCE

db_before = count_db_sales()
ts_before = datetime.now().isoformat()

# ══════════════════════════════════════════════════════════════
#  TEST 1: BAR-VERKAUF (CASH)
# ══════════════════════════════════════════════════════════════
print("═" * 55)
print("  TEST 1: 💵 BAR-VERKAUF (Slot 64, Redbull Kokos, 2.50€)")
print("═" * 55)

test_state = {
    "temperatures": {"zone1": 9.0, "zone2": 7.0},
    "errors": {"byte": 16, "code": 54, "count": 0},
    "selstate": "enderog",
    "credit": {"units": 0, "count": 2},
    "clock": {"day": 11, "month": 6, "year": 26, "weekday": 4, "hour": 7, "minute": 30, "second": 0},
    "slot_count": 99,
    "last_seen": "2026-06-11T07:30:00",
    "prices": {},
    "catalog": CATALOG
}
test_last_state = {}

# Katalog auch direkt im fl-Modul setzen
fl._CATALOG_CACHE = CATALOG

fl.sale_in_progress = False
fl.sale_price_cent = 0
fl.current_sale_slot = None
fl.last_erog_time = 0
fl.sale_credit_before = 0
fl.sale_is_card = False
fl.last_credit_count = 0

feed_line("selstate: ack enderog", test_state, test_last_state)
feed_line("select 0 64 0 0: ack", test_state, test_last_state)
feed_line("readcredit: ack 0 2", test_state, test_last_state)
feed_line("selstate: ack viewprice 250", test_state, test_last_state)
feed_line("readcredit: ack 500 2", test_state, test_last_state)  # 5€ eingeworfen
feed_line("readcredit: ack 250 2", test_state, test_last_state)  # 2.50€ abgezogen
feed_line("selstate: ack erog lock", test_state, test_last_state)  # Ausgabe
feed_line("readcredit: ack 0 2", test_state, test_last_state)      # fertig
feed_line("selstate: ack take unlock", test_state, test_last_state)  # entnehmen
time.sleep(0.1)

check("sale_in_progress False nach take", not fl.sale_in_progress)

# Prüfen ob Sale in der DB gelandet ist
db_after = count_db_sales()
check("Neuer Eintrag in sales.db (Cash)", db_after > db_before,
      f"vorher: {db_before}, nachher: {db_after}")

new_sales = db_sales_since(ts_before)
cash_sales = [s for s in new_sales if s[4] == "cash"]
check("Cash-Verkauf als 'cash' gespeichert", len(cash_sales) > 0)

if cash_sales:
    s_ts, s_slot, s_prod, s_price, s_pay = cash_sales[-1]
    check(f"Slot {s_slot} (erwartet 64)", s_slot in ("64", "064"))
    check(f"Preis {s_price:.2f}€ (erwartet 2.50€)", abs(s_price - 2.50) < 0.01)
    check(f"Produktname '{s_prod}' (erwartet Redbull...)", "Redbull" in s_prod or "Kokos" in s_prod)

print()

# ══════════════════════════════════════════════════════════════
#  TEST 2: KARTEN-VERKAUF (CARD)
# ══════════════════════════════════════════════════════════════
print("═" * 55)
print("  TEST 2: 💳 KARTEN-VERKAUF (Slot 51, Volvic 500ml, 2.00€)")
print("═" * 55)

test_state2 = {
    "temperatures": {"zone1": 9.0, "zone2": 7.0},
    "errors": {"byte": 16, "code": 54, "count": 0},
    "selstate": "enderog",
    "credit": {"units": 0, "count": 3},
    "clock": {"day": 11, "month": 6, "year": 26, "weekday": 4, "hour": 11, "minute": 50, "second": 0},
    "slot_count": 99,
    "last_seen": "2026-06-11T11:50:00",
    "prices": {"51": {"slot_id": 51, "price_cent": 200, "price_eur": 2.0}},
    "catalog": CATALOG
}
test_last_state2 = {}

fl.sale_in_progress = False
fl.sale_price_cent = 0
fl.current_sale_slot = None
fl.last_erog_time = 0
fl.sale_credit_before = 0
fl.sale_is_card = False
fl.last_credit_count = 2

feed_line("selstate: ack enderog", test_state2, test_last_state2)
feed_line("select 0 51 0 0: ack", test_state2, test_last_state2)
feed_line("readcredit: ack 4900 3", test_state2, test_last_state2)
feed_line("selstate: ack viewprice 200", test_state2, test_last_state2)
feed_line("readcredit: ack 4900 3", test_state2, test_last_state2)
feed_line("select 0 51 0 0: ack", test_state2, test_last_state2)
feed_line("selstate: ack erog lock", test_state2, test_last_state2)
feed_line("readcredit: ack 0 3", test_state2, test_last_state2)
feed_line("selstate: ack take unlock", test_state2, test_last_state2)
time.sleep(0.1)

check("sale_in_progress False nach take (Card)", not fl.sale_in_progress)

new_sales2 = db_sales_since(ts_before)
card_sales = [s for s in new_sales2 if s[4] == "card"]
check("Karten-Verkauf als 'card' gespeichert", len(card_sales) > 0)

if card_sales:
    s_ts, s_slot, s_prod, s_price, s_pay = card_sales[-1]
    check(f"Slot {s_slot} (erwartet 51)", s_slot in ("51", "051"))
    check(f"Preis {s_price:.2f}€ (erwartet 2.00€)", abs(s_price - 2.00) < 0.01)
    check(f"Produktname '{s_prod}' (erwartet Volvic)", "Volvic" in s_prod)

print()

# ══════════════════════════════════════════════════════════════
#  TEST 3: ABBRUCH (other nach erog) – DARF KEIN SALE WERDEN
# ══════════════════════════════════════════════════════════════
print("═" * 55)
print("  TEST 3: ⛔ ABBRUCH (other nach erog)")
print("  Soll: KEIN Sale generiert werden")
print("═" * 55)

test_state3 = {
    "temperatures": {"zone1": 9.0, "zone2": 7.0},
    "errors": {"byte": 16, "code": 54, "count": 0},
    "selstate": "enderog",
    "credit": {"units": 0, "count": 4},
    "clock": {"day": 11, "month": 6, "year": 26, "weekday": 4, "hour": 9, "minute": 14, "second": 0},
    "slot_count": 99,
    "last_seen": "2026-06-11T09:14:00",
    "prices": {"48": {"slot_id": 48, "price_cent": 250, "price_eur": 2.5}},
    "catalog": CATALOG
}
test_last_state3 = {}

fl.sale_in_progress = False
fl.sale_price_cent = 0
fl.current_sale_slot = None
fl.last_erog_time = 0
fl.sale_credit_before = 0
fl.sale_is_card = False
fl.last_credit_count = 3

db_before_cancel = count_db_sales()

feed_line("selstate: ack enderog", test_state3, test_last_state3)
feed_line("select 1 48 0 0: ack", test_state3, test_last_state3)
feed_line("readcredit: ack 250 4", test_state3, test_last_state3)
feed_line("select 0 48 0 0: ack", test_state3, test_last_state3)
feed_line("selstate: ack erog lock", test_state3, test_last_state3)
feed_line("selstate: ack other", test_state3, test_last_state3)      # FEHLER!
feed_line("selstate: ack take unlock", test_state3, test_last_state3)
time.sleep(0.1)

check("sale_in_progress False nach other", not fl.sale_in_progress)
db_after_cancel = count_db_sales()
check("KEIN neuer Eintrag in sales.db (Abbruch)", db_after_cancel == db_before_cancel,
      f"vorher: {db_before_cancel}, nachher: {db_after_cancel}")

print()

# ══════════════════════════════════════════════════════════════
#  CLEANUP: Test-Sales aus DB entfernen
# ══════════════════════════════════════════════════════════════
print("🧹 Cleanup:")
if os.path.exists(DB_FILE):
    try:
        conn = sqlite3.connect(DB_FILE, timeout=5)
        conn.execute(f"DELETE FROM sales WHERE source = '{TEST_SOURCE}'")
        conn.commit()
        removed = conn.total_changes
        conn.close()
        if removed > 0:
            print(f"  ✅ {removed} Test-Sales aus DB entfernt")
        else:
            print(f"  ⚠️ Keine Test-Sales zum Löschen gefunden")
    except Exception as e:
        print(f"  ❌ Cleanup-Fehler: {e}")

print()

# ══════════════════════════════════════════════════════════════
#  ERGEBNIS
# ══════════════════════════════════════════════════════════════
print("═" * 55)
total = passed + failed
if failed == 0:
    print(f"  🎉 ALLE {passed} TESTS BESTANDEN")
    print(f"  💵 Barverkauf ✅ | 💳 Kartenzahlung ✅ | ⛔ Abbruch ✅")
else:
    print(f"  ⚠️  {passed}/{total} bestanden, {failed} fehlgeschlagen")
print("═" * 55)

# Test-Modus deaktivieren
fl.TEST_MODE_SOURCE = None

sys.exit(0 if failed == 0 else 1)
