#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║              fas1050_katalog_import.py                        ║
║                                                              ║
║   Importiert Produktkatalog aus VaS-Inventar-CSV + Live-Daten ║
║                                                              ║
║   QUELLEN:                                                    ║
║     1. InventoryStatus*.csv (vom Automaten-Betreiber)         ║
║        → Produktname + Produktgruppe + Slot-Nummer            ║
║     2. current_state.json (Live-Daten vom Listener)           ║
║        → Preise aus readprice-Befehlen (zuverlässiger)        ║
║     3. Altes product_catalog.json (Fallback)                  ║
║        → Slots, die nicht in CSV auftauchen, bleiben erhalten ║
║                                                              ║
║   OUTPUT: product_catalog.json (überschreibt altes)           ║
║           + Backup unter product_catalog.json.bak.<timestamp>  ║
╚══════════════════════════════════════════════════════════════╝
"""

import csv
import json
import os
import sys
import glob
from datetime import datetime
from pathlib import Path

# ── Pfade ───────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
LISTENER_DIR = SCRIPT_DIR.parent  # fas1050-sales-listener/
DATA_DIR = LISTENER_DIR / "data"

CSV_DIR = LISTENER_DIR / "import"  # CSV-Import-Ordner im Projekt
CATALOG_FILE = DATA_DIR / "product_catalog.json"
STATE_FILE = DATA_DIR / "current_state.json"

# ── Hilfsfunktionen ─────────────────────────────────────────

def find_latest_csv():
    """
    Findet die aktuellste InventoryStatus*.csv.
    Priorität: 1) Projekt-Import-Ordner  2) media/inbound (Chat-Uploads)
    """
    search_dirs = [CSV_DIR, Path("/root/.openclaw/media/inbound")]
    best = None
    for d in search_dirs:
        pattern = str(d / "InventoryStatus*.csv")
        files = glob.glob(pattern)
        if files:
            latest = max(files, key=os.path.getmtime)
            if best is None or os.path.getmtime(latest) > os.path.getmtime(best):
                best = latest
    return best


def load_csv(csv_path):
    """
    Liest die VaS-Inventar-CSV.
    Versucht UTF-8 zuerst, Fallback latin1 (VaS-typisch).
    Liefert dict: slot_num -> {name, group, price_csv}
    """
    encodings = ["utf-8", "latin1", "iso-8859-1", "cp1252"]
    products = {}
    for enc in encodings:
        try:
            with open(csv_path, encoding=enc) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            if len(rows) > 0:
                print(f"   Encoding: {enc}")
                break
        except (UnicodeDecodeError, UnicodeError):
            continue

    for row in rows:
        sel = row.get("Selection", "").strip()
        if not sel.startswith("NA/"):
            continue
        try:
            slot = int(sel.split("/")[1])
        except (IndexError, ValueError):
            continue

        name = (row.get("Produkt", "") or "").strip()
        group = (row.get("Produktgruppe", "") or "").strip()
        price_raw = (row.get("Machine Price (VMC)", "") or "0").strip().replace(",", ".")
        try:
            price_csv = float(price_raw)
        except ValueError:
            price_csv = 0.0

        products[slot] = {
            "name": name,
            "group": group,
            "price_csv": price_csv,
        }
    return products


def load_current_state(state_file):
    """
    Liest current_state.json.
    Liefert dict: slot_num_str -> {price_cent, price_eur}
    """
    try:
        with open(state_file) as f:
            data = json.load(f)
        return data.get("prices", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def load_old_catalog(catalog_file):
    """Liest altes product_catalog.json. Liefert dict oder None."""
    try:
        with open(catalog_file) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def build_catalog(csv_products, live_prices, old_catalog):
    """
    Merged CSV + Live-Preise + alten Katalog.
    Priorität:
      - Name + Gruppe: aus CSV
      - Preis: aus current_state (live) > CSV > alter Katalog
    """
    catalog = {}

    # ── Zuerst: Slots aus CSV ──────────────────────────────
    for slot, info in csv_products.items():
        slot_key = str(slot)
        live_price = live_prices.get(slot_key, {})

        # Preis: Live hat Vorrang
        price_cent = live_price.get("price_cent")
        if price_cent is not None:
            price_eur = live_price.get("price_eur", round(price_cent / 100, 2))
        else:
            # Fallback: CSV-Preis in Cent
            price_cent = int(round(info["price_csv"] * 100))
            price_eur = info["price_csv"]

        catalog[slot_key] = {
            "name": info["name"],
            "group": info["group"],
            "price": price_eur,
        }

    # ── Dann: Slots aus altem Katalog, die NICHT in CSV sind ──
    if old_catalog:
        for slot_key, old_info in old_catalog.items():
            if slot_key not in catalog:
                # Preis ggf. aus Live-Daten überschreiben
                live_price = live_prices.get(slot_key, {})
                price_cent = live_price.get("price_cent")
                if price_cent is not None:
                    price_eur = live_price.get("price_eur", round(price_cent / 100, 2))
                else:
                    price_eur = old_info.get("price", 0)

                catalog[slot_key] = {
                    "name": old_info.get("name", "?"),
                    "group": old_info.get("group", "?"),
                    "price": price_eur,
                }

    return catalog


def save_catalog(catalog, catalog_file):
    """Schreibt Katalog + erstellt Backup."""
    # Backup
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = catalog_file.with_name(f"product_catalog.json.bak.{timestamp}")
    if catalog_file.exists():
        catalog_file.rename(backup_file)
        print(f"📦 Backup: {backup_file}")

    with open(catalog_file, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)
    print(f"✅ Katalog geschrieben: {catalog_file}")
    print(f"   → {len(catalog)} Einträge")


def report_changes(csv_products, old_catalog, new_catalog):
    """Zeigt eine Übersicht der Änderungen."""
    if not old_catalog:
        print("\n📋 Neuer Katalog (kein alter vorhanden)")
        return

    print("\n📋 Änderungsübersicht:")
    changed_names = 0
    changed_prices = 0
    new_slots = 0
    removed_slots = 0

    for slot_key, new_info in sorted(new_catalog.items(), key=lambda x: int(x[0])):
        old_info = old_catalog.get(slot_key)
        if not old_info:
            new_slots += 1
            print(f"  🆕 Slot {slot_key}: {new_info['name']} ({new_info['price']:.2f}€)")
            continue

        old_name = old_info.get("name", "")
        old_price = old_info.get("price", 0)
        name_changed = old_name.strip().lower().replace(" ", "") != new_info["name"].strip().lower().replace(" ", "")
        price_changed = abs(old_price - new_info["price"]) > 0.005

        if name_changed and price_changed:
            changed_names += 1
            changed_prices += 1
            print(f"  🔄 Slot {slot_key}: {old_name} ({old_price:.2f}€) → {new_info['name']} ({new_info['price']:.2f}€)")
        elif name_changed:
            changed_names += 1
            print(f"  🏷  Slot {slot_key}: {old_name} → {new_info['name']}")
        elif price_changed:
            changed_prices += 1
            print(f"  💰 Slot {slot_key}: {new_info['name']}: {old_price:.2f}€ → {new_info['price']:.2f}€")

    for slot_key in old_catalog:
        if slot_key not in new_catalog:
            removed_slots += 1
            name = old_catalog[slot_key].get("name", "?")
            print(f"  🗑  Slot {slot_key}: {name} (nur noch im Backup)")

    print(f"\n📊 Statistik:")
    print(f"   {new_slots} neue Slots")
    print(f"   {changed_names} umbenannte Slots")
    print(f"   {changed_prices} Preisänderungen")
    if removed_slots:
        print(f"   {removed_slots} entfernte Slots (nur Backup)")


# ── Hauptfunktion ───────────────────────────────────────────

def main():
    csv_path = None
    if len(sys.argv) > 1:
        csv_path = sys.argv[1]
        if not os.path.isfile(csv_path):
            print(f"❌ Datei nicht gefunden: {csv_path}")
            sys.exit(1)
        print(f"📄 CSV: {csv_path}")
    else:
        csv_path = find_latest_csv()
        if not csv_path:
            print("❌ Keine InventoryStatus*.csv gefunden.")
            print("   Usage: python3 fas1050_katalog_import.py [pfad/zur/InventoryStatus.csv]")
            sys.exit(1)
        print(f"📄 Automatisch gefunden: {csv_path}")

    # CSV laden
    csv_products = load_csv(csv_path)
    print(f"   → {len(csv_products)} Produkte aus CSV")

    if not csv_products:
        print("❌ Keine Produkte aus CSV extrahiert. Abbruch.")
        sys.exit(1)

    # Live-Preise laden
    live_prices = load_current_state(STATE_FILE)
    print(f"💶 Live-Preise geladen: {len(live_prices)} Slots")

    # Alten Katalog laden
    old_catalog = load_old_catalog(CATALOG_FILE)
    if old_catalog:
        print(f"📚 Alter Katalog: {len(old_catalog)} Einträge")
    else:
        print("📚 Kein alter Katalog vorhanden (Neuanlage)")

    # Mergen
    new_catalog = build_catalog(csv_products, live_prices, old_catalog)

    # Report + speichern
    report_changes(csv_products, old_catalog, new_catalog)
    save_catalog(new_catalog, CATALOG_FILE)

    print("\n✨ Fertig!")


if __name__ == "__main__":
    main()
