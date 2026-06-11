#!/usr/bin/env python3
"""
Tageszusammenfassung der Automaten-Verkäufe (SQLite-Version).

Modi:
  A) Täglicher Report um 6:00 Uhr → Umsätze von 06:00 Vortag bis 05:59 heute
  B) Tagesumsatz auf Abruf → Umsätze von 00:00 heute bis jetzt (lokal Berlin)
  
Aufruf:
  python3 daily_sales_summary.py            → Modus A (gestern 06:00 – heute 06:00)
  python3 daily_sales_summary.py --today     → Modus B (heute 00:00 – jetzt)
"""

import os
import sys
import sqlite3
from datetime import datetime, timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
DB_FILE = os.path.join(DATA_DIR, "sales.db")

# Telegram
TELEGRAM_BOT_TOKEN = os.environ.get("VAS1050_TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("VAS1050_TELEGRAM_CHAT_ID", "")
if not TELEGRAM_BOT_TOKEN:
    token_file = os.path.join(SCRIPT_DIR, ".telegram_token")
    try:
        with open(token_file) as f:
            TELEGRAM_BOT_TOKEN = f.read().strip()
    except FileNotFoundError:
        pass


def telegram_send(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️  KEIN TELEGRAM_BOT_TOKEN oder CHAT_ID", file=sys.stderr)
        return
    try:
        import urllib.request, urllib.parse
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(url, data=data)
        resp = urllib.request.urlopen(req, timeout=15)
        if resp.status != 200:
            print(f"⚠️  Telegram-Fehler: {resp.status}", file=sys.stderr)
    except Exception as e:
        print(f"⚠️  Telegram-Fehler: {e}", file=sys.stderr)


def load_sales(start_local, end_local):
    """
    Lädt Verkäufe aus der SQLite-DB im angegebenen Zeitraum.
    start_local, end_local = datetime-Objekte in Berliner Lokalzeit.
    Die DB speichert Timestamps als ISO-Strings in Lokalzeit.
    """
    start_str = start_local.strftime("%Y-%m-%dT%H:%M:%S")
    end_str = end_local.strftime("%Y-%m-%dT%H:%M:%S")

    if not os.path.exists(DB_FILE):
        return []

    try:
        conn = sqlite3.connect(DB_FILE, timeout=5)
        cursor = conn.execute(
            "SELECT timestamp, slot, product, category, price_eur, payment FROM sales WHERE timestamp >= ? AND timestamp < ? ORDER BY timestamp",
            (start_str, end_str)
        )
        rows = cursor.fetchall()
        conn.close()

        sales = []
        for ts, slot, product, category, price_eur, payment in rows:
            sales.append({
                "time": ts,
                "slot": slot,
                "product": product or "?",
                "category": category or "?",
                "price_eur": price_eur,
                "payment": payment,
            })
        return sales
    except Exception as e:
        print(f"⚠️  DB-Fehler: {e}", file=sys.stderr)
        return []


def build_report(sales, start_local, end_local, title_prefix):
    """Erzeuge Report-String aus gefilterten Sales."""
    if not sales:
        msg = (
            f"📊 <b>{title_prefix}</b>\n"
            f"🗓 {start_local.strftime('%d.%m')} {start_local.strftime('%H:%M')} – {end_local.strftime('%d.%m')} {end_local.strftime('%H:%M')}\n\n"
            f"Keine Verkäufe in diesem Zeitraum."
        )
        return msg, 0, 0.0

    total_cent = int(sum(s["price_eur"] for s in sales) * 100 + 0.5)

    # Nach Kategorie gruppieren
    cat_data = {}
    slot_details = {}
    for s in sales:
        cat = s.get("category") or "?"
        slot = s.get("slot") or "?"
        product = s.get("product") or ""
        price_cent = int(s["price_eur"] * 100 + 0.5)

        if cat not in cat_data:
            cat_data[cat] = {"count": 0, "total_cent": 0}
        cat_data[cat]["count"] += 1
        cat_data[cat]["total_cent"] += price_cent

        key = (cat, slot)
        if key not in slot_details:
            slot_details[key] = {"count": 0, "total_cent": 0, "name": product if product != "?" else ""}
        slot_details[key]["count"] += 1
        slot_details[key]["total_cent"] += price_cent

    total_eur = total_cent / 100.0
    lines = [
        f"📊 <b>{title_prefix}</b>",
        f"🗓 {start_local.strftime('%d.%m')} {start_local.strftime('%H:%M')} – {end_local.strftime('%d.%m')} {end_local.strftime('%H:%M')}",
        "",
        f"💰 <b>Umsatz: {total_eur:.2f}€</b>",
        f"🛒 Verkäufe: {len(sales)}",
        "",
    ]

    cat_order = ["Getränke", "Snacks", "Vapes", "Zahnstocher", "Metzgerei", "Sonstiges"]
    for cat in cat_order:
        cd = cat_data.get(cat)
        if not cd:
            continue
        lines.append(f"<b>{cat}</b> ({cd['count']}x, {cd['total_cent']/100:.2f}€)")
        for (c, slot), info in sorted(slot_details.items(), key=lambda x: int(x[0][1]) if x[0][1].isdigit() else 999):
            if c != cat:
                continue
            name_str = f" — {info['name']}" if info['name'] else ""
            lines.append(f"  📍 Slot {slot}{name_str}: {info['count']}x ({info['total_cent']/100:.2f}€)")
        lines.append("")

    return "\n".join(lines).strip(), len(sales), total_eur


def mode_a():
    """Täglicher Report: gestern 06:00 – heute 06:00"""
    now_local = datetime.now()
    today6 = now_local.replace(hour=6, minute=0, second=0, microsecond=0)
    if now_local.hour < 6:
        today6 -= timedelta(days=1)
    start = today6 - timedelta(days=1)
    end = today6
    sales = load_sales(start, end)
    return build_report(sales, start, end, "Automaten-Zusammenfassung"), start, end


def mode_b():
    """Tagesumsatz: heute 00:00 – jetzt"""
    now_local = datetime.now()
    today0_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    sales = load_sales(today0_local, now_local)
    return build_report(sales, today0_local, now_local, "Heutiger Tagesumsatz"), today0_local, now_local


def main():
    mode = "A"
    if "--today" in sys.argv or "-t" in sys.argv:
        mode = "B"

    if mode == "A":
        (msg, count, total), start, end = mode_a()
        telegram_send(msg)
        print(f"✅ Report A ({start.strftime('%d.%m %H:%M')} – {end.strftime('%d.%m %H:%M')}): {count} Verkäufe, {total:.2f}€")
    elif mode == "B":
        (msg, count, total), start, end = mode_b()
        telegram_send(msg)
        print(f"✅ Report B ({start.strftime('%d.%m %H:%M')} – {end.strftime('%d.%m %H:%M')}): {count} Verkäufe, {total:.2f}€")


if __name__ == "__main__":
    main()
