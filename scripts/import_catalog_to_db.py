#!/usr/bin/env python3
"""Catalog Import Script - Liest CSV und schreibt in data/sales.db

WICHTIG (18.09.2026): Aus der CSV werden NUR Slot + Produktname + Produktgruppe
übernommen. PREISE WERDEN NIE AUS DER CSV GEBILDET – der Preis pro Slot kommt
ausschließlich vom Listener (Live-Daten aus data/current_state.json bzw. dem
bereits vorhandenen DB-Wert, den der Listener pflegt).
"""
import csv, sqlite3, re, os, sys, json

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
CSV_FILE = os.path.join(DATA_DIR, "catalog_export.csv")
DB_FILE = os.path.join(DATA_DIR, "sales.db")
STATE_FILE = os.path.join(DATA_DIR, "current_state.json")

COL_PROD = 14   # Produkt
COL_CAT = 15    # Produktgruppe
COL_SEL = 16    # Selection (z.B. NA/10)
# Spalte 27 ("Machine Price (VMC)") wird bewusst NICHT verwendet – Preise kommen vom Listener!

def parse_selection(sel_str):
    sel_str = sel_str.strip()
    m = re.match(r'NA/(\d+)$', sel_str)
    return m.group(1) if m else None

def load_listener_prices():
    """Live-Preise des Listeners (data/current_state.json) – einzige Preisquelle."""
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
        return {str(k): v.get("price_cent")
                for k, v in (data.get("prices") or {}).items()
                if v.get("price_cent") is not None}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def group_from_category(cat):
    """Aus Kategorie die group ableiten (z.B. 'Vapes 19%' -> 'Vapes')"""
    return cat.split(" ")[0].strip() if cat else ""

# --- CSV parsen ---
rows = []
with open(CSV_FILE, "r", encoding="utf-8-sig") as f:
    reader = csv.reader(f)
    header = next(reader)
    for row in reader:
        if len(row) <= COL_SEL:
            continue
        sel = row[COL_SEL].strip()
        if not sel:
            continue
        slot = parse_selection(sel)
        if not slot:
            continue
        product = row[COL_PROD].strip() if len(row) > COL_PROD else ""
        category = row[COL_CAT].strip() if len(row) > COL_CAT else ""
        group_name = group_from_category(category)
        rows.append([slot, product, category, group_name])   # Preis bewusst NICHT aus CSV

print(f"📋 CSV gelesen: {len(rows)} Produkte")

# --- DB schreiben (Preise: nur Listener) ---
conn = sqlite3.connect(DB_FILE, timeout=10)
listener_prices = load_listener_prices()
db_prices = dict(conn.execute("SELECT slot, price_cent FROM catalog").fetchall())

from_listener = from_db = no_price = 0
final_rows = []
for slot, product, category, group_name in rows:
    if slot in listener_prices:
        price_cent = listener_prices[slot]
        from_listener += 1
    elif slot in db_prices:
        price_cent = db_prices[slot]      # bisherigen (vom Listener gepflegten) Wert behalten
        from_db += 1
    else:
        price_cent = 0                    # Listener trägt den Preis nach
        no_price += 1
    final_rows.append((slot, product, category, group_name, price_cent))

# Alten Katalog löschen
conn.execute("DELETE FROM catalog")
conn.execute("DELETE FROM sqlite_sequence WHERE name='catalog'")  # Reset für INSERT (catalog hat PK, kein autoincrement)
# Alle einfügen
conn.executemany(
    "INSERT OR REPLACE INTO catalog (slot, name, category, group_name, price_cent) VALUES (?, ?, ?, ?, ?)",
    final_rows
)
conn.commit()
count = conn.execute("SELECT COUNT(*) FROM catalog").fetchone()[0]
conn.close()

print(f"💶 Preise (nur vom Listener): {from_listener}x live übernommen, {from_db}x aus DB behalten, {no_price}x noch ohne Preis")
print(f"✅ Katalog aktualisiert: {count} Einträge in DB")

# Änderungen zeigen
from collections import Counter
cats = Counter(r[2] for r in rows)
print(f"\n📊 Kategorien:")
for cat, cnt in sorted(cats.items()):
    print(f"   {cat:30s} → {cnt:3d} Slots")
