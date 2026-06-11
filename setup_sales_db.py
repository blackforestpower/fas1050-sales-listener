#!/usr/bin/env python3
"""
Setup/Migration: Erzeugt sales.db und migriert alle historischen Sales aus events.jsonl.
    
Aufruf:
    python3 setup_sales_db.py            # nur anzeigen
    python3 setup_sales_db.py --migrate   # DB anlegen + migrieren
"""

import json
import os
import sqlite3
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
EVENTS_FILE = os.path.join(DATA_DIR, "events.jsonl")
DB_FILE = os.path.join(DATA_DIR, "sales.db")
CATALOG_FILE = os.path.join(DATA_DIR, "product_catalog.json")

# Katalog einlesen (für Produktname + Kategorie)
catalog = {}
try:
    with open(CATALOG_FILE) as f:
        catalog = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    pass

SCHEMA = """
CREATE TABLE IF NOT EXISTS sales (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,       -- ISO-8601 (z.B. 2026-06-10T20:12:34)
    slot        TEXT,                -- Slot-Nummer (z.B. "46")
    product     TEXT,                -- Produktname aus Katalog
    category    TEXT,                -- Kategorie aus Katalog
    price_cent  INTEGER NOT NULL,    -- Preis in Cent
    price_eur   REAL NOT NULL,       -- Preis in Euro
    payment     TEXT NOT NULL,       -- "cash" oder "card"
    credit_before INTEGER,           -- Kredit vor dem Sale
    source      TEXT DEFAULT 'events.jsonl'  -- Herkunft
);
CREATE INDEX IF NOT EXISTS idx_sales_timestamp ON sales(timestamp);
CREATE INDEX IF NOT EXISTS idx_sales_product ON sales(product);
CREATE INDEX IF NOT EXISTS idx_sales_category ON sales(category);
CREATE INDEX IF NOT EXISTS idx_sales_payment ON sales(payment);
"""


def extract_sales_from_events():
    """Liest alle Sale-Events aus events.jsonl"""
    sales = []
    try:
        with open(EVENTS_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") != "sale":
                    continue
                data = ev.get("data", {})
                ts = ev.get("time") or ev.get("timestamp", "")
                slot = data.get("slot", "")
                price_cent = data.get("price_cent", 0) or 0
                price_eur = data.get("price_eur", 0) or 0
                payment = data.get("payment_method", "")
                credit_before = data.get("credit_before")
                
                # Produktname + Kategorie aus Katalog
                product = ""
                category = ""
                if slot and slot in catalog:
                    product = catalog[slot].get("name", "")
                    category = catalog[slot].get("category", "")
                
                sales.append({
                    "timestamp": ts,
                    "slot": slot,
                    "product": product,
                    "category": category,
                    "price_cent": int(price_cent),
                    "price_eur": float(price_eur),
                    "payment": payment,
                    "credit_before": credit_before,
                })
    except FileNotFoundError:
        pass
    
    return sales


def get_existing_count(cursor):
    cursor.execute("SELECT COUNT(*) FROM sales")
    return cursor.fetchone()[0]


def migrate():
    # Prüfen ob DB schon existiert
    db_exists = os.path.exists(DB_FILE)
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Tabelle anlegen
    cursor.executescript(SCHEMA)
    conn.commit()
    
    existing = get_existing_count(cursor) if db_exists else 0
    sales = extract_sales_from_events()
    
    if not sales and existing == 0:
        print("❌ Keine Sales in events.jsonl gefunden. DB ist leer.")
        conn.close()
        return
    
    if existing > 0:
        print(f"📊 DB hat bereits {existing} Einträge, {len(sales)} in events.jsonl")
        # Nur neue Einträge hinzufügen (anhand timestamp + slot + price)
        cursor.execute("SELECT timestamp, slot, price_cent FROM sales")
        existing_set = set(cursor.fetchall())
        new_sales = [s for s in sales if (s["timestamp"], s["slot"], s["price_cent"]) not in existing_set]
        if not new_sales:
            print("✅ Keine neuen Sales – DB ist bereits aktuell.")
            conn.close()
            return
        sales = new_sales
    
    # Einfügen
    inserted = 0
    for s in sales:
        cursor.execute("""
            INSERT INTO sales (timestamp, slot, product, category, price_cent, price_eur, payment, credit_before, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            s["timestamp"],
            s["slot"],
            s["product"],
            s["category"],
            s["price_cent"],
            s["price_eur"],
            s["payment"],
            s["credit_before"],
            "events.jsonl"
        ))
        inserted += 1
    
    conn.commit()
    conn.close()
    
    total = existing + inserted
    print(f"✅ Migration erfolgreich: {inserted} neue Sales importiert")
    print(f"📊 Gesamt in DB: {total} Einträge")


def preview():
    sales = extract_sales_from_events()
    if not sales:
        print("❌ Keine Sales in events.jsonl gefunden.")
        return
    
    payments = {}
    for s in sales:
        p = s["payment"]
        payments[p] = payments.get(p, 0) + 1
    
    total_eur = sum(s["price_eur"] for s in sales)
    
    print(f"📊 Gefundene Sales in events.jsonl: {len(sales)}")
    print(f"💰 Gesamtumsatz (events.jsonl): {total_eur:.2f}€")
    print(f"💳 Zahlungsmethoden: {payments}")
    print()
    
    # Top 10 Produkte
    from collections import Counter
    products = Counter(s["product"] or f"Slot {s['slot']}" for s in sales)
    print("🏆 Top 10 Produkte:")
    for prod, count in products.most_common(10):
        print(f"   {count:3d}x {prod}")
    
    print()
    if os.path.exists(DB_FILE):
        conn = sqlite3.connect(DB_FILE)
        db_count = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
        print(f"📁 sales.db existiert bereits: {db_count} Einträge")
        conn.close()
    else:
        print("📁 sales.db existiert noch nicht")


if __name__ == "__main__":
    if "--migrate" in sys.argv:
        migrate()
    else:
        preview()
