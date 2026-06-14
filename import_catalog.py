#!/usr/bin/env python3
"""
Importiert den Produkt-Katalog aus einem Cantaloupe DEX/CSV-Export
und erstellt die product_catalog.json für den FAS 1050 Listener.

Die CSV ist komma-getrennt (siehe Header-Zeile).
"NA/" vor der Selection wird entfernt → direkt die Slot-Nummer als Key.

Usage:
  python3 import_catalog.py [datei.csv]
  Standard: data/catalog_export.csv
"""

import csv
import json
import os
import sys
import re

# Spalten-Index (0-based)
COL_PROD = 14   # Produkt
COL_CAT = 15    # Produktgruppe
COL_SEL = 16    # Selection (z.B. NA/10)
COL_PRICE = 27  # Machine Price (VMC) in € → wird zu Cent
COL_STOCK = 21  # Verfügbarer Bestand
COL_PAR = 20    # Par

# Projekt-Root
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_DIR, "data")


def parse_selection(sel_str):
    """'NA/10' → '10', 'NA/168' → '168'. Einfach NA/ entfernen."""
    sel_str = sel_str.strip()
    m = re.match(r'NA/(\d+)$', sel_str)
    if not m:
        return None
    return m.group(1)


def parse_price(val):
    """'2.5' → 250 Cent, '5' → 500 Cent, '' → None"""
    val = val.strip()
    if not val:
        return None
    try:
        return int(round(float(val) * 100))
    except ValueError:
        return None


def main():
    csv_file = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DATA_DIR, "catalog_export.csv")

    if not os.path.exists(csv_file):
        print(f"❌ Datei nicht gefunden: {csv_file}")
        sys.exit(1)

    catalog = {}
    errors = []

    with open(csv_file, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader)
        print(f"📋 Spalten ({len(header)}):")
        for i, h in enumerate(header):
            print(f"   [{i:2d}] {h}")

        for row_num, row in enumerate(reader, start=2):
            if len(row) <= COL_SEL:
                continue

            sel = row[COL_SEL].strip()
            if not sel:
                continue

            slot_key = parse_selection(sel)
            if not slot_key:
                errors.append(f"Zeile {row_num}: ungültige Selection '{sel}'")
                continue

            product = row[COL_PROD].strip() if len(row) > COL_PROD else ""
            category = row[COL_CAT].strip() if len(row) > COL_CAT else ""
            price_cent = parse_price(row[COL_PRICE]) if len(row) > COL_PRICE else None

            try:
                stock = int(row[COL_STOCK]) if len(row) > COL_STOCK and row[COL_STOCK].strip() else 0
            except ValueError:
                stock = 0
            try:
                par = int(row[COL_PAR]) if len(row) > COL_PAR and row[COL_PAR].strip() else 0
            except ValueError:
                par = 0

            entry = {"name": product, "category": category}
            if price_cent:
                entry["price_cent"] = price_cent
            entry["stock"] = stock
            entry["par"] = par

            catalog[slot_key] = entry

    # Ausgabe
    out_path = os.path.join(DATA_DIR, "product_catalog.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"\n✅ Katalog gespeichert: {out_path}")
    print(f"   Einträge: {len(catalog)}")
    print(f"   Fehler:   {len(errors)}")

    # Kategorien-Übersicht
    cats = {}
    for k, v in catalog.items():
        cat = v.get("category", "Unbekannt")
        cats.setdefault(cat, []).append(k)
    print(f"\n📊 Kategorien:")
    for cat, slots in sorted(cats.items()):
        stock_total = sum(catalog[s].get("stock", 0) for s in slots)
        print(f"   {cat:30s} → {len(slots):3d} Slots ({stock_total:3d} Stück verfügbar)")

    if errors:
        print(f"\n⚠️  Fehler:")
        for e in errors:
            print(f"   {e}")


if __name__ == "__main__":
    main()
