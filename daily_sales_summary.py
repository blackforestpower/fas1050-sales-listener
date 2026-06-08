#!/usr/bin/env python3
"""
Tageszusammenfassung der Automaten-Verkäufe.

Modi:
  A) Täglicher Report um 6:00 Uhr → Umsätze von 06:00 Vortag bis 05:59 heute
  B) Tagesumsatz auf Abruf → Umsätze von 00:00 heute bis jetzt (lokal Berlin)
  
Aufruf:
  python3 daily_sales_summary.py            → Modus A (gestern 06:00 – heute 06:00)
  python3 daily_sales_summary.py --today     → Modus B (heute 00:00 – jetzt)
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone, date

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
EVENTS_FILE = os.path.join(DATA_DIR, "events.jsonl")
CATALOG_FILE = os.path.join(DATA_DIR, "product_catalog.json")

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


def load_catalog():
    try:
        with open(CATALOG_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def load_sales():
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
                if ev.get("type") == "sale":
                    data = ev.get("data", {})
                    sales.append({
                        "time": ev.get("time", ""),
                        "slot": data.get("slot"),
                        "price_cent": data.get("price_cent"),
                        "price_eur": data.get("price_eur"),
                    })
    except FileNotFoundError:
        pass
    return sales


def get_utc_offset():
    import time
    return 2 if time.localtime().tm_isdst else 1


def build_report(sales, catalog, start_local, end_local, title_prefix):
    """Erzeuge Report-String aus gefilterten Sales.
    start_local, end_local = datetime-Objekte in Berliner Lokalzeit
    Events in events.jsonl sind in lokaler Berliner Zeit (CEST).
    """
    start_str = start_local.strftime("%Y-%m-%dT%H:%M:%S")
    end_str = end_local.strftime("%Y-%m-%dT%H:%M:%S")

    filtered = [s for s in sales if start_str <= s.get("time", "") < end_str]
    if not filtered:
        msg = (
            f"📊 <b>{title_prefix}</b>\n"
            f"🗓 {start_local.strftime('%d.%m')} {start_local.strftime('%H:%M')} – {end_local.strftime('%d.%m')} {end_local.strftime('%H:%M')}\n\n"
            f"Keine Verkäufe in diesem Zeitraum."
        )
        return msg, 0, 0.0

    total_cent = 0
    slot_sales = {}
    for s in filtered:
        slot = s.get("slot", "?")
        price = s.get("price_cent") or 0
        total_cent += price
        if slot not in slot_sales:
            cat = catalog.get(str(slot), {}).get("category", "?")
            name = catalog.get(str(slot), {}).get("name", "")
            slot_sales[slot] = {"count": 0, "total_cent": 0, "name": name, "cat": cat}
        slot_sales[slot]["count"] += 1
        slot_sales[slot]["total_cent"] += price

    total_eur = total_cent / 100.0
    lines = [
        f"📊 <b>{title_prefix}</b>",
        f"🗓 {start_local.strftime('%d.%m')} {start_local.strftime('%H:%M')} – {end_local.strftime('%d.%m')} {end_local.strftime('%H:%M')}",
        "",
        f"💰 <b>Umsatz: {total_eur:.2f}€</b>",
        f"🛒 Verkäufe: {len(filtered)}",
        "",
    ]

    cat_order = ["Getränke", "Snacks", "Vapes", "Zahnstocher", "Metzgerei", "Sonstiges"]
    cat_data = {}
    for slot, info in slot_sales.items():
        c = info["cat"]
        if c not in cat_data:
            cat_data[c] = {"count": 0, "total_cent": 0, "items": []}
        cat_data[c]["count"] += info["count"]
        cat_data[c]["total_cent"] += info["total_cent"]
        cat_data[c]["items"].append((slot, info))

    for cat in cat_order:
        cd = cat_data.get(cat)
        if not cd:
            continue
        lines.append(f"<b>{cat}</b> ({cd['count']}x, {cd['total_cent']/100:.2f}€)")
        for slot, info in sorted(cd["items"], key=lambda x: int(x[0]) if x[0].isdigit() else 999):
            name_str = f" — {info['name']}" if info["name"] else ""
            lines.append(f"  📍 Slot {slot}{name_str}: {info['count']}x ({info['total_cent']/100:.2f}€)")
        lines.append("")

    return "\n".join(lines).strip(), len(filtered), total_eur


def mode_a():
    """Täglicher Report: gestern 06:00 – heute 06:00"""
    now_local = datetime.now()
    today6 = now_local.replace(hour=6, minute=0, second=0, microsecond=0)
    if now_local.hour < 6:
        today6 -= timedelta(days=1)
    start = today6 - timedelta(days=1)
    end = today6
    return build_report(sales, catalog, start, end, "Automaten-Zusammenfassung"), start, end


def mode_b():
    """Tagesumsatz: heute 00:00 – jetzt (Berliner Zeit)
    Wichtig: Events in events.jsonl sind UTC! Wir wandeln lokal->UTC um.
    """
    now_local = datetime.now()
    today0_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return build_report(sales, catalog, today0_local, now_local, "Heutiger Tagesumsatz"), today0_local, now_local


# Globale Ladevorgänge (damit modes darauf zugreifen können)
catalog = load_catalog()
sales = load_sales()


def main():
    mode = "A"  # default = täglicher Report
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
