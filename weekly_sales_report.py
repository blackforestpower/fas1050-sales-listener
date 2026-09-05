#!/usr/bin/env python3
"""
Taeglicher Sales-Graph mit 7-Tage-Rueckblick
==============================================
• Chart-Bild + Text-Caption in EINER Telegram-Nachricht
• Prozentuale Veraenderung zur Vorwoche
• Kein LLM

Usage:
    python3 weekly_sales_report.py
"""

import sqlite3
import os
import sys
import json
from datetime import datetime, timedelta
from urllib.request import Request, urlopen
from collections import defaultdict
import io

import dotenv
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

# ── Config ─────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(SCRIPT_DIR, "data", "sales.db")

dotenv.load_dotenv(os.path.join(SCRIPT_DIR, ".env"))
TELEGRAM_BOT_TOKEN = os.environ.get("VAS1050_TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("VAS1050_TELEGRAM_CHAT_ID", "-1003979461717")


def fmt_eur_de(amount):
    """1234.56 -> '1.234,56EUR'"""
    return f"{amount:,.2f}EUR".replace(",", "X").replace(".", ",").replace("X", ".")


def get_sales(start, end):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.execute(
        "SELECT timestamp, product, price_eur "
        "FROM sales WHERE timestamp >= ? AND timestamp < ? "
        "ORDER BY timestamp",
        (start, end)
    )
    rows = cur.fetchall()
    conn.close()
    return rows


# ── Daten abrufen ──────────────────────────────────────────
def analyze_period(start_str, end_str):
    rows = get_sales(start_str, end_str)
    if not rows:
        return None

    daily_totals = defaultdict(float)
    product_counts = defaultdict(lambda: {"count": 0, "total": 0.0})

    for ts, product, price_eur in rows:
        day = ts[:10]
        daily_totals[day] += price_eur
        prod_name = product or "(unbekannt)"
        product_counts[prod_name]["count"] += 1
        product_counts[prod_name]["total"] += price_eur

    total_eur = sum(daily_totals.values())
    num_tx = len(rows)
    avg_daily = total_eur / max(len(daily_totals), 1)

    top3 = sorted(product_counts.items(), key=lambda x: -x[1]["count"])[:3]

    return {
        "daily_totals": dict(daily_totals),
        "total_eur": total_eur,
        "num_tx": num_tx,
        "avg_daily": avg_daily,
        "top3": top3,
    }


# ── Monatsumsatz abrufen ───────────────────────────────────
def get_month_sales():
    """
    Holt Umsatz des aktuellen Monats (1. bis heute) und
    Umsatz des kompletten Vormonats.
    Gibt (this_month_eur, prev_month_eur, this_month_name, prev_month_name) zurueck.
    """
    heute = datetime.now()
    month_start = heute.replace(day=1, hour=0, minute=0, second=0).strftime("%Y-%m-%dT%H:%M:%S")
    tomorrow = (heute + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")

    # Vormonat komplett: 1. bis 1. des aktuellen Monats (exklusive)
    if heute.month == 1:
        prev_start = heute.replace(year=heute.year - 1, month=12, day=1, hour=0, minute=0, second=0)
    else:
        prev_start = heute.replace(month=heute.month - 1, day=1, hour=0, minute=0, second=0)
    prev_end = heute.replace(day=1, hour=0, minute=0, second=0)

    # Monatsnamen auf Deutsch
    monate_de = ["", "Januar", "Februar", "Maerz", "April", "Mai", "Juni",
                 "Juli", "August", "September", "Oktober", "November", "Dezember"]
    this_month_name = monate_de[heute.month]
    prev_month_name = monate_de[prev_start.month]

    conn = sqlite3.connect(DB_FILE)
    cur = conn.execute(
        "SELECT SUM(price_eur) FROM sales WHERE timestamp >= ? AND timestamp < ?",
        (month_start, tomorrow)
    )
    this_month = cur.fetchone()[0] or 0.0
    cur = conn.execute(
        "SELECT SUM(price_eur) FROM sales WHERE timestamp >= ? AND timestamp < ?",
        (prev_start.strftime("%Y-%m-%dT%H:%M:%S"), prev_end.strftime("%Y-%m-%dT%H:%M:%S"))
    )
    prev_month = cur.fetchone()[0] or 0.0
    conn.close()

    return this_month, prev_month, this_month_name, prev_month_name
    cur = conn.execute(
        "SELECT SUM(price_eur) FROM sales WHERE timestamp >= ? AND timestamp < ?",
        (prev_start, prev_end_str)
    )
    prev_month = cur.fetchone()[0] or 0.0
    conn.close()

    return this_month, prev_month


# ── Chart (nur Balken, hochkant) ───────────────────────────
def generate_chart(daily_totals):
    if not daily_totals:
        return None

    dates = sorted(daily_totals.keys())
    values = [daily_totals[d] for d in dates]
    date_objs = [datetime.strptime(d, "%Y-%m-%d") for d in dates]
    weekday_names = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    labels = [f"{weekday_names[d.weekday()]}\n{d.day:02d}.{d.month:02d}." for d in date_objs]

    fig, ax = plt.subplots(figsize=(6.5, 5.5))
    fig.patch.set_facecolor('#f0f4f8')
    ax.set_facecolor('#f0f4f8')

    max_val = max(values) if values else 1

    # Farbverlauf Blau
    colors = []
    for v in values:
        intensity = v / max_val
        r = 0.15 + 0.25 * intensity
        g = 0.35 + 0.35 * intensity
        b = 0.65 + 0.35 * intensity
        colors.append((r, g, b))

    bars = ax.bar(labels, values, color=colors, edgecolor='#2b6cb0',
                  linewidth=1.0, width=0.55, alpha=0.9)

    for bar, val in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2,
                bar.get_height() + max_val * 0.01,
                f'{val:.0f}EUR', ha='center', va='bottom',
                fontweight='bold', fontsize=10, color='#1a365d')

    ax.set_ylabel('Umsatz in EUR', fontweight='bold', fontsize=10, color='#2d3748')
    ax.set_title('Taeglicher Umsatz - Letzte 7 Tage',
                 fontweight='bold', fontsize=13, color='#1a202c', pad=12)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'{x:.0f}EUR'))
    ax.yaxis.grid(True, linestyle='--', alpha=0.3, color='#a0aec0')
    ax.set_axisbelow(True)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color('#cbd5e0')
    ax.spines['bottom'].set_color('#cbd5e0')
    ax.tick_params(colors='#4a5568', labelsize=9)

    plt.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight',
                facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close(fig)
    buf.seek(0)
    return buf


# ── Caption bauen ──────────────────────────────────────────
def build_caption(this_week, start_date, end_date, prev_total, month_total=0, this_month_name="", prev_month_name="", prev_month_total=0):
    tw = this_week["total_eur"]
    daily = this_week["daily_totals"]

    lines = []
    lines.append(f"\U0001f4ca <b>Wochenrückblick</b>")
    lines.append("")

    # Gestern = Kalendertag now - 1
    gestern_tag = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    vorgestern_tag = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    gestern_val = daily.get(gestern_tag, 0)
    vorgestern_val = daily.get(vorgestern_tag, 0)

    if gestern_val > 0:
        g_dt = datetime.strptime(gestern_tag, "%Y-%m-%d")
        g_show = f"{g_dt.day:02d}.{g_dt.month:02d}."

        if vorgestern_val > 0:
            pct = ((gestern_val - vorgestern_val) / vorgestern_val) * 100
            sign = "+" if pct >= 0 else ""
            arrow = "\U0001f7e2" if pct >= 0 else "\U0001f534"
            lines.append(f"\U0001f4c5 <b>Gestern ({g_show}):</b> {fmt_eur_de(gestern_val)}  {arrow} {sign}{pct:.1f}% zum Vortag")
        else:
            lines.append(f"\U0001f4c5 <b>Gestern ({g_show}):</b> {fmt_eur_de(gestern_val)}")
        lines.append("")

    lines.append(f"\U0001f4b0 <b>Umsatz 7 Tage:</b> {fmt_eur_de(tw)}")
    lines.append(f"\U0001f6d2 <b>Transaktionen:</b> {this_week['num_tx']}")
    lines.append(f"\U0001f4c8 <b>Ø pro Tag:</b> {fmt_eur_de(this_week['avg_daily'])}")

    if prev_total is not None and prev_total > 0:
        pct = ((tw - prev_total) / prev_total) * 100
        sign = "+" if pct >= 0 else ""
        arrow = "\U0001f7e2" if pct >= 0 else "\U0001f534"
        lines.append(f"{arrow} <b>zur Vorwoche:</b> {sign}{pct:.1f}%")
    lines.append("")

    lines.append("\U0001f3c6 <b>Top 3 Produkte:</b>")
    medals = ["\U0001f947", "\U0001f948", "\U0001f949"]
    for i, (prod, data) in enumerate(this_week["top3"]):
        lines.append(f"{medals[i]} {prod} - {data['count']}x ({fmt_eur_de(data['total'])})")
    lines.append("")

    # Monatsumsatz
    lines.append(f"\U0001f4c5 <b>Monatsumsatz:</b> {fmt_eur_de(month_total)}")
    lines.append(f"   {prev_month_name}: {fmt_eur_de(prev_month_total)}")

    return "\n".join(lines)


# ── Telegram senden: Bild + Caption ────────────────────────
def telegram_send_photo(image_bytes, caption):
    if not TELEGRAM_BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN nicht gesetzt", file=sys.stderr)
        return False

    boundary = "----FormBoundary9MA4YWxkTrZu0gW"

    body = b""
    # chat_id
    body += f"--{boundary}\r\n".encode()
    body += b'Content-Disposition: form-data; name="chat_id"\r\n'
    body += b"\r\n"
    body += TELEGRAM_CHAT_ID.encode()
    body += b"\r\n"

    # photo
    body += f"--{boundary}\r\n".encode()
    body += b'Content-Disposition: form-data; name="photo"; filename="chart.png"\r\n'
    body += b"Content-Type: image/png\r\n"
    body += b"\r\n"
    body += image_bytes
    body += b"\r\n"

    # caption
    if caption:
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="caption"\r\n'
        body += b"\r\n"
        body += caption.encode("utf-8")
        body += b"\r\n"

    # parse_mode
    body += f"--{boundary}\r\n".encode()
    body += b'Content-Disposition: form-data; name="parse_mode"\r\n'
    body += b"\r\n"
    body += b"HTML"
    body += b"\r\n"

    body += f"--{boundary}--\r\n".encode()

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        req = Request(url, data=body)
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        urlopen(req, timeout=30)
        return True
    except Exception as e:
        if hasattr(e, 'read'):
            detail = e.read().decode()
            print(f"Telegram-Fehler: {e} -- {detail}", file=sys.stderr)
        else:
            print(f"Telegram-Fehler: {e}", file=sys.stderr)
        return False


# ── MAIN ────────────────────────────────────────────────────
def main():
    heute = datetime.now()
    end_str = heute.strftime("%Y-%m-%d") + "T06:00:00"
    start_current = (heute - timedelta(days=7)).strftime("%Y-%m-%d") + "T06:00:00"
    start_prev = (heute - timedelta(days=14)).strftime("%Y-%m-%d") + "T06:00:00"

    this_week = analyze_period(start_current, end_str)
    prev_week = analyze_period(start_prev, start_current)

    if this_week is None:
        print("Keine Verkaufsdaten.", file=sys.stderr)
        return

    # Anzeige: Endtag = gestern (letzter vollständiger Kalendertag)
    gestern = heute - timedelta(days=1)
    start_date = start_current[:10]
    end_date = gestern.strftime("%Y-%m-%d")
    prev_total = prev_week["total_eur"] if prev_week else None

    # Monatsumsatz
    month_total, prev_month_total, this_month_name, prev_month_name = get_month_sales()

    # Chart
    chart_buf = generate_chart(this_week["daily_totals"])
    if not chart_buf:
        print("Chart-Generierung fehlgeschlagen.", file=sys.stderr)
        return

    # Caption
    caption = build_caption(this_week, start_date, end_date, prev_total, month_total, this_month_name, prev_month_name, prev_month_total)

    # Senden: Bild + Caption in EINER Nachricht
    ok = telegram_send_photo(chart_buf.read(), caption)

    if ok:
        print(f"Gesendet: {fmt_eur_de(this_week['total_eur'])} Umsatz, {this_week['num_tx']} Transaktionen")
    else:
        print("Fehler beim Senden", file=sys.stderr)


if __name__ == "__main__":
    main()
