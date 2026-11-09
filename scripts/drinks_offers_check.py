#!/usr/bin/env python3
"""Getränke-Angebotscheck über marktguru.de (Brand-Seiten).

Quelle: https://www.marktguru.de/b/<brand> (serverseitiges HTML, kein JS nötig)
Ablauf: Config lesen → Brand-Seiten holen → Angebote parsen → auf unsere
Packungsgröße filtern → Zielpreis vergleichen → kurze Liste mit Links ausgeben.

Grund: kaufDA hat die Produktseiten (/Angebote/...) abgeschafft (404).
marktguru liefert Preis, Händler, Gültigkeit UND Packungsgröße im Text.
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
CONFIG = os.path.join(PROJECT_DIR, "data", "drinks_offers_config.json")

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
BASE = "https://www.marktguru.de/b/"


def fetch(slug):
    url = BASE + slug
    try:
        html = subprocess.check_output(
            ["curl", "-s", "--max-time", "25", "-A", UA, url],
            timeout=35,
        ).decode("utf-8", "replace")
        return html
    except Exception as e:
        print(f"# Fehler bei {slug}: {e}", file=sys.stderr)
        return ""


def parse_offers(html):
    """Liefert Liste von Angeboten (nur gültige, keine 'Verpasst')."""
    offers = []
    for m in re.finditer(r'<li[^>]*class="[^"]*offer-list-item[^"]*"[^>]*>', html):
        start = m.start()
        end = html.find("</li>", start)
        block = html[start:end + 5]
        txt = re.sub(r"<[^>]+>", " | ", block)
        txt = re.sub(r"\s*\|\s*(\|\s*)+", " | ", txt)
        txt = re.sub(r"\s+", " ", txt).strip()
        if "Verpasst" in txt:
            continue
        price = re.search(r"Preis:\s*\|\s*€\s*([\d.,]+)", txt)
        if not price:
            continue
        valid = re.search(r"Gültig:\s*\|\s*([\d.]{5}\s*-\s*[\d.]{5})", txt)
        retailer = re.search(r"Händler:\s*\|\s*([^|]+)", txt)
        pack = re.search(r"(\d+)\s*[x×]\s*([\d,.]+)\s*(l|ml)\b", txt)
        single = re.search(r"(?:je|Je)\s*([\d,.]+)\s*(l|ml)\b", txt)
        offers.append({
            "price": float(price.group(1).replace(".", "").replace(",", ".")),
            "valid": valid.group(1).strip() if valid else "",
            "retailer": (retailer.group(1).strip() if retailer else "?"),
            "pack": pack.groups() if pack else None,
            "single": single.groups() if single else None,
            "text": txt,
        })
    return offers


def units_from_offer(o):
    """(Anzahl Einheiten, Größe in ml) aus Packungsangabe ableiten."""
    if o["pack"]:
        n, size, unit = o["pack"]
        ml = float(size.replace(",", ".")) * (1000 if unit == "l" else 1)
        return int(n), ml
    if o["single"]:
        size, unit = o["single"]
        ml = float(size.replace(",", ".")) * (1000 if unit == "l" else 1)
        return 1, ml
    return None, None


def main():
    with open(CONFIG) as f:
        cfg = json.load(f)

    products = [p for p in cfg["products"] if p.get("target_price_eur") is not None]
    # Brand-Seiten cachen (mehrere Produkte pro Marke)
    cache = {}
    lines = []
    checked = 0

    for p in products:
        slug = p.get("marktguru_slug")
        size_ml = p.get("size_ml")
        if not slug or not size_ml:
            continue
        checked += 1
        if slug not in cache:
            cache[slug] = parse_offers(fetch(slug))

        best = None
        for o in cache[slug]:
            n_units, ml = units_from_offer(o)
            if not n_units or not ml:
                continue
            # Größe muss zu unserer Packung passen (±5 % Toleranz)
            if abs(ml - size_ml) > size_ml * 0.05:
                continue
            unit_price = o["price"] / n_units
            if best is None or unit_price < best[0]:
                best = (unit_price, o, n_units, ml)

        if best:
            unit_price, o, n_units, ml = best
            target = p["target_price_eur"]
            ok = "✅" if unit_price <= target else "❌"
            size_txt = f"{ml:.0f} ml" if abs(ml - 330) < 5 or abs(ml - 250) < 5 else f"{ml/1000:.2f} l"
            pack_txt = f"{n_units}×{size_txt}" if n_units > 1 else size_txt
            lines.append(
                f"{p['name']} – {o['retailer']} – {unit_price:.2f} € ({pack_txt}) {ok} "
                f"– {BASE}{slug}"
            )

    if lines:
        print(f"🥤 *Wochenangebote* (Zielpreis-Check, brutto)")
        print("\n".join(lines))
    else:
        print("Sonst keine passenden Angebote.")


if __name__ == "__main__":
    main()
