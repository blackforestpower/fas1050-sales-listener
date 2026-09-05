#!/usr/bin/env python3
"""Catalog Import Script - Liest CSV und schreibt in data/sales.db"""
import csv, sqlite3, re, os, sys

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
CSV_FILE = os.path.join(DATA_DIR, "catalog_export.csv")
DB_FILE = os.path.join(DATA_DIR, "sales.db")

COL_PROD = 14   # Produkt
COL_CAT = 15    # Produktgruppe
COL_SEL = 16    # Selection (z.B. NA/10)
COL_PRICE = 27  # Machine Price (VMC) in €

def parse_selection(sel_str):
    sel_str = sel_str.strip()
    m = re.match(r'NA/(\d+)$', sel_str)
    return m.group(1) if m else None

def parse_price(val):
    val = val.strip()
    if not val:
        return None
    try:
        return int(round(float(val) * 100))
    except ValueError:
        return None

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
        price_cent = parse_price(row[COL_PRICE]) if len(row) > COL_PRICE else 0
        if price_cent is None:
            price_cent = 0
        rows.append((slot, product, category, group_name, price_cent))

print(f"📋 CSV gelesen: {len(rows)} Produkte")

# --- DB schreiben ---
conn = sqlite3.connect(DB_FILE, timeout=10)
# Alten Katalog löschen
conn.execute("DELETE FROM catalog")
conn.execute("DELETE FROM sqlite_sequence WHERE name='catalog'")  # Reset für INSERT (catalog hat PK, kein autoincrement)
# Alle einfügen
conn.executemany(
    "INSERT OR REPLACE INTO catalog (slot, name, category, group_name, price_cent) VALUES (?, ?, ?, ?, ?)",
    rows
)
conn.commit()
count = conn.execute("SELECT COUNT(*) FROM catalog").fetchone()[0]
conn.close()

print(f"✅ Katalog aktualisiert: {count} Einträge in DB")

# Änderungen zeigen
from collections import Counter
cats = Counter(r[2] for r in rows)
print(f"\n📊 Kategorien:")
for cat, cnt in sorted(cats.items()):
    print(f"   {cat:30s} → {cnt:3d} Slots")
