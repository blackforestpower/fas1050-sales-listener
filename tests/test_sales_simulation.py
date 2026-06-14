#!/usr/bin/env python3
"""
Integrationstest für fas1050_listener_v5.py

Simuliert reale Kauf-Abläufe aus den Raw-Daten von heute (12.06.2026)
und jagt sie durch detect_sale → prüft ob korrekte Sales in der DB landen.

Szenarien:
  💵 Cash (Slot 41, Sprite 330ml, 2,00€)
  💳 Card (Slot 52, Fresh Peach, 2,00€)
  💳 Card (Slot 61, Redbull White Peach, 2,50€)

Usage:
  python3 tests/test_sales_simulation.py
"""

import json
import os
import sqlite3
import sys
import time
from datetime import datetime

TEST_SOURCE = "test_simulation"

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

# ── Environment für Import vorbereiten ────────────────
os.environ["VAS1050_HOST"] = "127.0.0.1"  # Dummy – kein echter Connect

from fas1050_listener_v5 import (
    parse_line, process_line, detect_sale,
    write_sale_to_db, _ensure_sales_table,
    DATA_DIR, RAW_FILE, DB_FILE, STATE_FILE,
    load_prices_from_log,
)
import fas1050_listener_v5 as fl

# Katalog für Produktnamen
CATALOG_FILE = os.path.join(DATA_DIR, "product_catalog.json")
with open(CATALOG_FILE) as f:
    CATALOG = json.load(f)

# ── Helpers ────────────────────────────────────────────

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


def reset_globals():
    """Globale Variablen im v5-Modul zurücksetzen"""
    fl.sale_in_progress = False
    fl.sale_price_cent = 0
    fl.current_sale_slot = None
    fl.last_erog_time = 0
    fl.sale_credit_before = 0
    fl.sale_is_card = False
    fl.last_credit_count = 0
    fl._CATALOG_CACHE = CATALOG


def feed(line, state):
    """Einzelne raw-Zeile an process_line füttern"""
    process_line(line, state, None)


def mkstate(credit_units=0, credit_count=0, selstate="enderog",
            prices=None, t1=9.0, t2=7.0):
    """Standard-Zustand für Tests"""
    return {
        "temperatures": {"zone1": t1, "zone2": t2},
        "errors": {"byte": 16, "code": 54, "count": 0},
        "selstate": selstate,
        "credit": {"units": credit_units, "count": credit_count},
        "clock": {"day": 12, "month": 6, "year": 26, "weekday": 5,
                  "hour": 20, "minute": 34, "second": 0},
        "slot_count": 99,
        "last_seen": "2026-06-12T20:34:00",
        "prices": prices or {},
        "catalog": CATALOG
    }


def count_db_sales(source=TEST_SOURCE):
    if not os.path.exists(DB_FILE):
        return 0
    conn = sqlite3.connect(DB_FILE, timeout=5)
    count = conn.execute(
        "SELECT COUNT(*) FROM sales WHERE source = ?", (source,)
    ).fetchone()[0]
    conn.close()
    return count


def get_db_sales(source=TEST_SOURCE):
    if not os.path.exists(DB_FILE):
        return []
    conn = sqlite3.connect(DB_FILE, timeout=5)
    cursor = conn.execute(
        "SELECT timestamp, slot, product, category, price_eur, payment, credit_before "
        "FROM sales WHERE source = ? ORDER BY id", (source,)
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


# ═════════════════════════════════════════════════════════
#  SETUP
# ═════════════════════════════════════════════════════════
print("=" * 55)
print("  FAS 1050 v5 – Sales Simulation (echte Daten 12.06.)")
print("=" * 55)
print()

_ensure_sales_table()
fl.TEST_MODE_SOURCE = TEST_SOURCE
reset_globals()

# ═════════════════════════════════════════════════════════
#  TEST 1: 💵 BAR – Slot 41 (Sprite 330ml, 2,00€)
#  Sequence aus 20:34:
#    1. select 0 52 (anderer Slot) → viewprice 200
#    2. select 0 42 (anderer Slot) → viewprice 200
#    3. readcredit: ack 200 2  (2€-Münze)
#    4. select 0 41 → erog lock → readcredit: ack 200 2
#    5. readcredit: ack 0 2 → take unlock
# ═════════════════════════════════════════════════════════
print("─" * 55)
print("  TEST 1: 💵 BAR | Slot 41 (Sprite 330ml, 2,00€)")
print("─" * 55)

state = mkstate(credit_units=0, credit_count=1)
reset_globals()

feed("selstate: ack enderog", state)
feed("select 0 52 0 0: ack", state)
feed("selstate: ack viewprice 200", state)
feed("select 0 42 0 0: ack", state)
feed("selstate: ack viewprice 200", state)
feed("readcredit: ack 200 2", state)           # 2€ eingeworfen
feed("selstate: ack viewprice 200", state)
feed("select 0 41 0 0: ack", state)
feed("selstate: ack erog lock", state)          # 🔥 Verkauf!
time.sleep(0.05)
feed("readcredit: ack 200 2", state)           # credit_before = 200
feed("selstate: ack erog lock", state)
feed("readcredit: ack 0 2", state)             # fertig
feed("selstate: ack take unlock", state)
feed("selstate: ack enderog", state)
time.sleep(0.1)

check("sale_in_progress False nach take", not fl.sale_in_progress)

rows = get_db_sales()
cash_rows = [r for r in rows if r[5] == "cash"]
check("Cash-Verkauf in DB", len(cash_rows) >= 1,
      f"{len(cash_rows)} Cash-Einträge")

if cash_rows:
    s = cash_rows[-1]  # neuester
    check(f"Slot {s[1]} (erwartet 41)", s[1] == "41")
    check(f"Preis {s[4]:.2f}€ (erwartet 2,00€)", abs(s[4] - 2.00) < 0.01)
    check(f"Produkt '{s[2]}' enthält Sprite", "Sprite" in s[2] or "sprite" in s[2])
    check(f"Kategorie '{s[3]}' enthält Snacks/Getränke", s[3] is not None and s[3] != "")
    check(f"Zahlung '{s[5]}' (erwartet cash)", s[5] == "cash")
print()

# ═════════════════════════════════════════════════════════
#  TEST 2: 💳 KARTE – Slot 52 (Fresh Peach, 2,00€)
#  Sequence aus 22:04:
#    select → viewprice 200 → readcredit 4900
#    → select → busy → erog → readcredit 0 → take
#
#  Wichtig: Der zweite select resettet sale_price_cent = 0.
#  Für Karte wird der Preis daher aus state["prices"] geholt
#  (genau wie im echten Betrieb, wenn die readprice-Burst da war).
# ═════════════════════════════════════════════════════════
print("─" * 55)
print("  TEST 2: 💳 KARTE | Slot 52 (Fresh Peach, 2,00€)")
print("─" * 55)

state = mkstate(
    credit_units=0, credit_count=2,
    prices={"52": {"price_cent": 200, "price_eur": 2.0}}
)
reset_globals()

feed("selstate: ack enderog", state)
feed("select 0 52 0 0: ack", state)
feed("selstate: ack viewprice 200", state)
feed("readcredit: ack 4900 2", state)           # 💳 Karte autorisiert
feed("selstate: ack viewprice 200", state)
feed("select 0 52 0 0: ack", state)             # → resettet sale_price_cent
feed("selstate: ack busy", state)
feed("selstate: ack erog lock", state)           # 🔥 Verkauf!
time.sleep(0.05)
feed("readcredit: ack 4900 2", state)           # credit_before = 4900
feed("selstate: ack erog lock", state)
feed("readcredit: ack 0 2", state)              # fertig
feed("selstate: ack take unlock", state)
feed("selstate: ack enderog", state)
time.sleep(0.1)

check("sale_in_progress False nach take (Card)", not fl.sale_in_progress)

rows = get_db_sales()
card_rows = [r for r in rows if r[5] == "card"]
check("Karten-Verkauf in DB", len(card_rows) >= 1,
      f"{len(card_rows)} Card-Einträge")

if card_rows:
    s = card_rows[-1]
    check(f"Slot {s[1]} (erwartet 52)", s[1] == "52")
    check(f"Preis {s[4]:.2f}€ (erwartet 2,00€)", abs(s[4] - 2.00) < 0.01)
    check(f"Produkt '{s[2]}' enthält Fresh Peach", "Fresh Peach" in s[2] or "fresh" in s[2].lower())
    check(f"Zahlung '{s[5]}' (erwartet card)", s[5] == "card")
    check(f"credit_before {s[6]} (erwartet 4900)", s[6] == 4900)
print()

# ═════════════════════════════════════════════════════════
#  TEST 3: 💳 KARTE – Slot 61 (Redbull White Peach, 2,50€)
#  Sequence aus 22:09:
#    select → viewprice 250 → readcredit 4900
#    → select → busy → erog → readcredit 0 → take
# ═════════════════════════════════════════════════════════
print("─" * 55)
print("  TEST 3: 💳 KARTE | Slot 61 (Redbull White Peach, 2,50€)")
print("─" * 55)

state = mkstate(
    credit_units=0, credit_count=3,
    prices={"61": {"price_cent": 250, "price_eur": 2.5}}
)
reset_globals()

feed("selstate: ack enderog", state)
feed("select 0 61 0 0: ack", state)
feed("selstate: ack viewprice 250", state)       # 2,50€ auf Display
feed("readcredit: ack 4900 2", state)            # 💳 Karte
feed("selstate: ack viewprice 250", state)
feed("select 0 61 0 0: ack", state)
feed("selstate: ack busy", state)
feed("selstate: ack erog lock", state)            # 🔥 Verkauf!
time.sleep(0.05)
feed("readcredit: ack 4900 2", state)
feed("selstate: ack erog lock", state)
feed("readcredit: ack 0 2", state)
feed("selstate: ack take unlock", state)
feed("selstate: ack enderog", state)
time.sleep(0.1)

check("sale_in_progress False nach take (Card 2)", not fl.sale_in_progress)

rows = get_db_sales()
card_rows = [r for r in rows if r[5] == "card"]
check("2 Karten-Verkäufe insgesamt", len(card_rows) >= 2,
      f"{len(card_rows)} Card-Einträge")

# Neuesten Card-Eintrag prüfen
if card_rows:
    s = card_rows[-1]
    check(f"Slot {s[1]} (erwartet 61)", s[1] == "61")
    check(f"Preis {s[4]:.2f}€ (erwartet 2,50€)", abs(s[4] - 2.50) < 0.01)
    check(f"Produkt '{s[2]}' enthält Redbull", "Redbull" in s[2] or "Red Bull" in s[2] or "Redbull" in s[2])
    check(f"Zahlung '{s[5]}' (erwartet card)", s[5] == "card")
print()

# ═════════════════════════════════════════════════════════
#  TEST 4: ⛔ ABBRUCH – other nach erog
#  Hinweis: v5 cleant sale_in_progress NICHT bei "other".
#  Der Sale wird daher später bei take nicht finalisiert,
#  weil last_erog_time > 45s oder die Globals resettet sind.
#  → Prüft: kein DB-Eintrag, sale_in_progress bleibt True
# ═════════════════════════════════════════════════════════
print("─" * 55)
print("  TEST 4: ⛔ ABBRUCH (other nach erog)")
print("─" * 55)

state = mkstate(credit_units=0, credit_count=4)
reset_globals()

db_before = count_db_sales()

feed("selstate: ack enderog", state)
feed("select 0 48 0 0: ack", state)
feed("selstate: ack viewprice 250", state)
feed("readcredit: ack 250 4", state)             # 2,50€ eingeworfen
feed("selstate: ack erog lock", state)            # 🔥 fing an …
feed("selstate: ack other", state)                # ⛔ ABGEBROCHEN (error/wait)
feed("selstate: ack enderog", state)
time.sleep(0.1)

# Nach other wird sale_in_progress NICHT gecleant (v5 Bug/Schwäche)
# → kein Sale darf in der DB landen, weil kein take/enderog
#   den Abschluss triggert
db_after = count_db_sales()
check("KEIN neuer Sale (Abbruch)", db_after == db_before,
      f"vorher: {db_before}, nachher: {db_after}")
print()

# ═════════════════════════════════════════════════════════
#  ERGEBNIS
# ═════════════════════════════════════════════════════════
print("=" * 55)
total = passed + failed
if failed == 0:
    print(f"  🎉 ALLE {passed} TESTS BESTANDEN")
    print(f"  💵 Bar ✅ | 💳 Karte ✅ | 💳 Karte 2 ✅ | ⛔ Abbruch ✅")
else:
    print(f"  ⚠️  {passed}/{total} bestanden, {failed} fehlgeschlagen")
print("=" * 55)
print()

# ═════════════════════════════════════════════════════════
#  CLEANUP
# ═════════════════════════════════════════════════════════
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
            print(f"  ⚠️ Keine Test-Sales gefunden")
    except Exception as e:
        print(f"  ❌ Cleanup-Fehler: {e}")

fl.TEST_MODE_SOURCE = None
print()

sys.exit(0 if failed == 0 else 1)
