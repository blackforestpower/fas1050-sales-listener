#!/usr/bin/env python3
"""
Abfrage-Tool für die Sales-Datenbank.

Beispiele:
    python3 query_sales.py                          # letzte 10 Verkäufe
    python3 query_sales.py --help                    # Hilfe
    python3 query_sales.py --product "Paulaner Spezi"  # alle Verkäufe eines Produkts
    python3 query_sales.py --product "Paulaner Spezi" --month 6  # nur Juni
    python3 query_sales.py --category Getränke --month 6
    python3 query_sales.py --category Vapes --since 2026-06-01
    python3 query_sales.py --today                  # heute
    python3 query_sales.py --cash                   # nur Barverkäufe
    python3 query_sales.py --card                   # nur Kartenverkäufe
    python3 query_sales.py --stats                  # Monatsstatistik
    python3 query_sales.py --top 15                 # Top 15 Produkte
"""

import sqlite3
import os
import sys
from datetime import datetime, timedelta
from collections import Counter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(SCRIPT_DIR, "data", "sales.db")


def connect():
    if not os.path.exists(DB_FILE):
        print(f"❌ Datenbank nicht gefunden: {DB_FILE}")
        print("   Führe zuerst aus: python3 setup_sales_db.py --migrate")
        sys.exit(1)
    return sqlite3.connect(DB_FILE)


def fmt_eur(cent):
    return f"{cent/100:.2f}€"


def run_query(sql, params=(), limit=None):
    conn = connect()
    cursor = conn.cursor()
    if limit:
        sql += f" LIMIT {limit}"
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()
    return rows


def show_help():
    print(__doc__)
    sys.exit(0)


def cmd_last(n=10):
    rows = run_query(
        "SELECT timestamp, slot, product, category, price_eur, payment FROM sales ORDER BY timestamp DESC",
        limit=n
    )
    if not rows:
        print("❌ Keine Verkäufe gefunden.")
        return
    print(f"📋 Letzte {len(rows)} Verkäufe:\n")
    for ts, slot, prod, cat, price, paym in rows:
        pay_icon = "💳" if paym in ("card", "karte") else "💵"
        prod_str = f" — {prod}" if prod else ""
        print(f"  {ts}  Slot {slot}{prod_str}  ({cat})  {price:.2f}€ {pay_icon}")
    total = sum(r[4] for r in rows)
    print(f"\n💰 Summe: {total:.2f}€")


def cmd_product(name, month=None):
    if month:
        rows = run_query(
            "SELECT timestamp, slot, price_eur, payment FROM sales WHERE product = ? AND CAST(strftime('%m', substr(timestamp,1,10)) AS INTEGER) = ? ORDER BY timestamp",
            (name, month)
        )
    else:
        rows = run_query(
            "SELECT timestamp, slot, price_eur, payment FROM sales WHERE product = ? ORDER BY timestamp",
            (name,)
        )
    if not rows:
        print(f"❌ Keine Verkäufe von '{name}' gefunden.")
        return
    print(f"📊 Verkäufe von '{name}': {len(rows)}\n")
    for ts, slot, price, paym in rows:
        pay_icon = "💳" if paym in ("card", "karte") else "💵"
        print(f"  {ts}  Slot {slot}  {price:.2f}€ {pay_icon}")
    total = sum(r[2] for r in rows)
    print(f"\n💰 Gesamt: {total:.2f}€")
    print(f"🛒 Anzahl: {len(rows)}")


def cmd_category(cat, month=None):
    if month:
        rows = run_query(
            "SELECT timestamp, slot, product, price_eur, payment FROM sales WHERE category = ? AND CAST(strftime('%m', substr(timestamp,1,10)) AS INTEGER) = ? ORDER BY timestamp",
            (cat, month)
        )
    else:
        rows = run_query(
            "SELECT timestamp, slot, product, price_eur, payment FROM sales WHERE category = ? ORDER BY timestamp",
            (cat,)
        )
    if not rows:
        print(f"❌ Keine Verkäufe in Kategorie '{cat}' gefunden.")
        return
    print(f"📊 Verkäufe in '{cat}': {len(rows)}\n")
    for ts, slot, prod, price, paym in rows:
        pay_icon = "💳" if paym in ("card", "karte") else "💵"
        prod_str = f" — {prod}" if prod else ""
        print(f"  {ts}  Slot {slot}{prod_str}  {price:.2f}€ {pay_icon}")
    total = sum(r[3] for r in rows)
    print(f"\n💰 Gesamt: {total:.2f}€")
    print(f"🛒 Anzahl: {len(rows)}")


def cmd_today():
    today = datetime.now().strftime("%Y-%m-%d")
    rows = run_query(
        "SELECT timestamp, slot, product, category, price_eur, payment FROM sales WHERE substr(timestamp,1,10) = ? ORDER BY timestamp",
        (today,)
    )
    if not rows:
        print(f"📅 Heute ({today}): Keine Verkäufe.")
        return
    print(f"📅 Heute ({today}): {len(rows)} Verkäufe\n")
    for ts, slot, prod, cat, price, paym in rows:
        pay_icon = "💳" if paym in ("card", "karte") else "💵"
        prod_str = f" — {prod}" if prod else ""
        print(f"  {ts}  Slot {slot}{prod_str}  ({cat})  {price:.2f}€ {pay_icon}")
    total = sum(r[4] for r in rows)
    print(f"\n💰 Tagesumsatz: {total:.2f}€")


def cmd_stats(month=None):
    if month:
        rows = run_query(
            "SELECT category, product, price_eur, payment FROM sales WHERE CAST(strftime('%m', substr(timestamp,1,10)) AS INTEGER) = ?",
            (month,)
        )
        label = f"Monat {month}"
    else:
        rows = run_query("SELECT category, product, price_eur, payment FROM sales")
        label = "Gesamt"
    
    if not rows:
        print(f"❌ Keine Daten für {label}.")
        return
    
    total = sum(r[2] for r in rows)
    cash = sum(r[2] for r in rows if r[3] in ("cash",))
    card = sum(r[2] for r in rows if r[3] in ("card", "karte"))
    cash_count = sum(1 for r in rows if r[3] in ("cash",))
    card_count = sum(1 for r in rows if r[3] in ("card", "karte"))
    
    print(f"📊 Statistik ({label}):\n")
    print(f"🛒  Verkäufe gesamt: {len(rows)}")
    print(f"💰  Umsatz gesamt: {total:.2f}€")
    print(f"💵  Cash: {cash:.2f}€ ({cash_count}x)")
    print(f"💳  Karte: {card:.2f}€ ({card_count}x)")
    print()
    
    # Pro Kategorie
    cat_data = {}
    for cat, prod, price, paym in rows:
        if cat not in cat_data:
            cat_data[cat] = {"count": 0, "total": 0.0}
        cat_data[cat]["count"] += 1
        cat_data[cat]["total"] += price
    
    print("📂 Pro Kategorie:")
    for cat, data in sorted(cat_data.items(), key=lambda x: -x[1]["total"]):
        print(f"   {data['count']:3d}x  {data['total']:>6.2f}€  {cat}")
    
    print()
    # Top 10 Produkte
    products = Counter(r[1] or f"Slot {rows[i][3]}" for i, r in enumerate(rows))
    print("🏆 Top 10 Produkte:")
    for prod, count in products.most_common(10):
        print(f"   {count:3d}x  {prod}")


def cmd_top(n=10):
    rows = run_query("SELECT product, COUNT(*) as cnt, SUM(price_eur) as total FROM sales WHERE product != '' GROUP BY product ORDER BY cnt DESC")
    if not rows:
        print("❌ Keine Produktdaten.")
        return
    print(f"🏆 Top {min(n, len(rows))} Produkte:\n")
    for i, (prod, cnt, total) in enumerate(rows[:n], 1):
        print(f"  {i:2d}. {cnt:3d}x  {total:>6.2f}€  {prod}")


def cmd_cash():
    rows = run_query(
        "SELECT timestamp, slot, product, category, price_eur FROM sales WHERE payment IN ('cash') ORDER BY timestamp DESC"
    )
    if not rows:
        print("❌ Keine Barverkäufe.")
        return
    print(f"💵 Barverkäufe: {len(rows)}\n")
    for ts, slot, prod, cat, price in rows:
        prod_str = f" — {prod}" if prod else ""
        print(f"  {ts}  Slot {slot}{prod_str}  ({cat})  {price:.2f}€")
    total = sum(r[4] for r in rows)
    print(f"\n💰 Summe: {total:.2f}€")


def cmd_card():
    rows = run_query(
        "SELECT timestamp, slot, product, category, price_eur FROM sales WHERE payment IN ('card', 'karte') ORDER BY timestamp DESC"
    )
    if not rows:
        print("❌ Keine Kartenverkäufe.")
        return
    print(f"💳 Kartenverkäufe: {len(rows)}\n")
    for ts, slot, prod, cat, price in rows:
        prod_str = f" — {prod}" if prod else ""
        print(f"  {ts}  Slot {slot}{prod_str}  ({cat})  {price:.2f}€")
    total = sum(r[4] for r in rows)
    print(f"\n💰 Summe: {total:.2f}€")


if __name__ == "__main__":
    args = sys.argv[1:]
    
    if not args or "--help" in args or "-h" in args:
        show_help()
    
    # Flags parsen
    product = None
    category = None
    month = None
    since = None
    today = False
    cash = False
    card = False
    stats = False
    top = None
    
    i = 0
    while i < len(args):
        if args[i] == "--product" and i+1 < len(args):
            product = args[i+1]
            i += 2
        elif args[i] == "--category" and i+1 < len(args):
            category = args[i+1]
            i += 2
        elif args[i] == "--month" and i+1 < len(args):
            month = int(args[i+1])
            i += 2
        elif args[i] == "--since" and i+1 < len(args):
            since = args[i+1]
            i += 2
        elif args[i] == "--today":
            today = True
            i += 1
        elif args[i] == "--cash":
            cash = True
            i += 1
        elif args[i] == "--card":
            card = True
            i += 1
        elif args[i] == "--stats":
            stats = True
            i += 1
        elif args[i] == "--top" and i+1 < len(args):
            top = int(args[i+1])
            i += 2
        else:
            print(f"❌ Unbekanntes Argument: {args[i]}")
            sys.exit(1)
    
    if product:
        cmd_product(product, month)
    elif category:
        cmd_category(category, month)
    elif today:
        cmd_today()
    elif cash:
        cmd_cash()
    elif card:
        cmd_card()
    elif stats:
        cmd_stats(month)
    elif top:
        cmd_top(top)
    else:
        cmd_last()
