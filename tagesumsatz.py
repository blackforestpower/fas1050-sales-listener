#!/usr/bin/env python3
"""
📅 Tagesumsatz – Unified Report Generator
==========================================
Einheitliches Format für beide Auswertungs-Modi.
Die build_report()-Funktion produziert identischen Output,
lediglich die Timestamps unterscheiden sich je nach Modus.

Usage:
  python3 tagesumsatz.py                  → Modus B: heute 00:00 – jetzt
  python3 tagesumsatz.py --gestern / -g   → Modus A: gestern 06:00 – heute 06:00 (Morning Report)
  python3 tagesumsatz.py --datum YYYY-MM-DD → beliebiger Tag 00:00–23:59
"""

import sqlite3
import os
import sys
from datetime import datetime, timedelta

# ── DB ──────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(SCRIPT_DIR, "data", "sales.db")


def fmt_eur(cent):
    """Cent → Euro-String"""
    return f"{cent/100:.2f}€"


def get_sales(start, end):
    """
    Holt alle Verkäufe im Zeitraum [start, end).
    start, end: ISO-Strings 'YYYY-MM-DDTHH:MM:SS'
    """
    conn = sqlite3.connect(DB_FILE)
    cur = conn.execute(
        "SELECT timestamp, slot, product, category, price_eur, payment "
        "FROM sales WHERE timestamp >= ? AND timestamp < ? "
        "ORDER BY timestamp",
        (start, end)
    )
    rows = cur.fetchall()
    conn.close()
    return rows


# ── Kategorie-Definitionen ──────────────────────────────────
# Anzeige-Reihenfolge (vereinfachte Namen ohne MwSt-Suffix)
CAT_ORDER = [
    "Getränke",
    "Spirituosen",
    "Snacks",
    "Metzgerei",
    "Sonstiges",
]


def short_cat(full_cat):
    """Vereinfachte Anzeige: 'Getränke 19%' → 'Getränke'"""
    if not full_cat:
        return "Sonstiges"
    # MwSt-Suffix entfernen: "Getränke 19%" → "Getränke"
    parts = full_cat.rsplit(" ", 1)
    if len(parts) == 2 and parts[1].endswith("%"):
        return parts[0]
    return full_cat


def build_report(start, end, title):
    """
    ─────────────────────────────────────────────────────────
    EINZIGE Formatierungs-Funktion.
    Produziert IMMER das gleiche Output-Layout:
      • Titel + Zeitraum
      • Einzeltransaktionen, gruppiert nach Kategorie
      • Summenzeilen (Cash / Karte / Total)
    ─────────────────────────────────────────────────────────
    """
    rows = get_sales(start, end)

    if not rows:
        return (
            f"📅 {title}\n"
            f"   Zeitraum: {start[:10]} {start[11:16]} – {end[:10]} {end[11:16]}\n"
            f"   Keine Verkäufe in diesem Zeitraum."
        )

    # ── Daten gruppieren ──
    cat_groups = {}  # short_cat → [(row, ...)]
    total_cent = 0
    card_cent = 0
    cash_cent = 0
    card_count = 0
    cash_count = 0

    for ts, slot, product, full_cat, price, payment in rows:
        cent = int(price * 100 + 0.5)
        total_cent += cent

        if payment in ("card", "karte"):
            card_cent += cent
            card_count += 1
        else:
            cash_cent += cent
            cash_count += 1

        cat_name = short_cat(full_cat)
        if cat_name not in cat_groups:
            cat_groups[cat_name] = []
        cat_groups[cat_name].append({
            "time": ts[11:16],
            "slot": slot,
            "product": product or "?",
            "price": price,
            "payment": payment,
        })

    # ── Output bauen ──
    lines = [
        f"📅 {title}",
        f"   Zeitraum: {start[:10]} {start[11:16]} – {end[:10]} {end[11:16]}",
        "",
    ]

    # Bekannte Kategorien in Reihenfolge
    for cat in CAT_ORDER:
        if cat not in cat_groups:
            continue
        lines.append(f"  {cat}:")
        for s in cat_groups[cat]:
            icon = "💳" if s["payment"] in ("card", "karte") else "💵"
            lines.append(f"    {s['time']}  Slot {s['slot']:>4s}  {icon}  {s['price']:.2f}€  {s['product']}")
        lines.append("")

    # Unbekannte Kategorien am Ende
    for cat in sorted(cat_groups):
        if cat in CAT_ORDER:
            continue
        lines.append(f"  {cat}:")
        for s in cat_groups[cat]:
            icon = "💳" if s["payment"] in ("card", "karte") else "💵"
            lines.append(f"    {s['time']}  Slot {s['slot']:>4s}  {icon}  {s['price']:.2f}€  {s['product']}")
        lines.append("")

    # Summenzeilen
    lines.append(f"  {'─' * 50}")
    lines.append(f"  💵 Cash:   {cash_count:2d}x  {fmt_eur(cash_cent):>8s}")
    lines.append(f"  💳 Karte:  {card_count:2d}x  {fmt_eur(card_cent):>8s}")
    lines.append(f"  {'─' * 50}")
    lines.append(f"  💰 Total:  {card_count + cash_count:2d}x  {fmt_eur(total_cent):>8s}")

    return "\n".join(lines)


# ── MAIN ────────────────────────────────────────────────────
def main():
    heute = datetime.now().strftime("%Y-%m-%d")

    if "--gestern" in sys.argv or "-g" in sys.argv:
        # Modus A: Morning Report – gestern 06:00 bis heute 06:00
        gestern = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        start = f"{gestern}T06:00:00"
        end = f"{heute}T06:00:00"
        print(build_report(start, end, f"Automaten-Zusammenfassung {gestern}"))

    elif "--datum" in sys.argv:
        idx = sys.argv.index("--datum")
        if idx + 1 < len(sys.argv):
            datum = sys.argv[idx + 1]
            morgen = (datetime.strptime(datum, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            print(build_report(f"{datum}T00:00:00", f"{morgen}T00:00:00", f"Tagesumsatz {datum}"))

    else:
        # Modus B: Tagesumsatz – heute 00:00 bis jetzt
        jetzt = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        print(build_report(f"{heute}T00:00:00", jetzt, f"Tagesumsatz {heute}"))


if __name__ == "__main__":
    main()
