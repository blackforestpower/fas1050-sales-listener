#!/usr/bin/env python3
"""Getränke-Angebotscheck über marktguru.de (Brand-Seiten, eingebettete JSON-Daten).

Quelle: https://www.marktguru.de/b/<brand>
Die Seite enthält die Angebote als JSON-Objekte:
  {"description":"... 24 x 0,33 l ...","retailer":{"name":...},
   "brand":{"uniqueName":"warsteiner"},"price":9.99,...,"validFrom":...,"validTo":...}

Features:
- liest Packungsgrößen auch aus Mehrfach-Angaben ("20 x 0,5 l / 24 x 0,33 l")
- ergänzt Einzelgrößen ("je 250 ml", "0,5-l-Dose")
- berücksichtigt manuelle Angebote aus der Config (manual_offers), wenn marktguru
  ein Angebot (noch) nicht listet
- vergleicht brutto gegen target_price_eur, gruppiert die Ausgabe nach Markt
"""
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
CONFIG = os.path.join(PROJECT_DIR, "data", "drinks_offers_config.json")

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
BASE = "https://www.marktguru.de/b/"
NEEDLE = '{"images":{"count":'


def fetch(slug):
    try:
        return subprocess.check_output(
            ["curl", "-s", "--max-time", "25", "-A", UA, BASE + slug],
            timeout=35,
        ).decode("utf-8", "replace")
    except Exception as e:
        print(f"# Fehler bei {slug}: {e}", file=sys.stderr)
        return ""


def _extract_json_objects(html):
    objs, idx = [], 0
    while True:
        i = html.find(NEEDLE, idx)
        if i < 0:
            break
        depth, j, in_str, esc = 0, i, False, False
        while j < len(html):
            ch = html[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        j += 1
                        break
            j += 1
        try:
            objs.append(json.loads(html[i:j]))
        except Exception:
            pass
        idx = j
    return objs


def _pack_sizes(desc):
    """Alle (Anzahl, ml) aus einer Beschreibung ziehen."""
    packs = []
    # "24 x 0,33 l", "20 x 0,5 l", "10 x 0,20 l", "6 x 0,33-l-Fl."
    for mm in re.finditer(r"(\d+)\s*[x×]\s*([\d,.]+)\s*([a-zA-Z]{0,2})", desc):
        try:
            n = int(mm.group(1))
            size = float(mm.group(2).replace(",", "."))
        except ValueError:
            continue
        if n <= 0 or size <= 0 or size > 100:
            continue
        unit = (mm.group(3) or "").lower()
        if unit.startswith("ml"):
            ml = size
        elif unit.startswith("l"):
            ml = size * 1000
        else:
            ml = size * 1000 if size < 10 else size
        packs.append((n, ml))
    # Einzelgrößen: "je 250 ml", "je 0,33-l-Dose", "0,5 l Dose"
    for mm in re.finditer(r"(?:je|Je)?\s*([\d,.]+)\s*(ml|l)(?![a-zA-Z])", desc):
        try:
            size = float(mm.group(1).replace(",", "."))
        except ValueError:
            continue
        if size <= 0 or size > 100:
            continue
        ml = size if mm.group(2).lower() == "ml" else size * 1000
        packs.append((1, ml))
    # Duplikate entfernen
    return list(dict.fromkeys(packs))


def parse_offers(html):
    offers = []
    for o in _extract_json_objects(html):
        try:
            brand = (o.get("brand") or {}).get("uniqueName") or ""
            retailer = (o.get("retailer") or {}).get("name") or "?"
            desc = o.get("description") or ""
            price = float(o.get("price") or 0)
            valid_to = o.get("validTo") or ""
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        offers.append({
            "brand": brand,
            "retailer": retailer,
            "desc": desc,
            "price": price,
            "valid_to": valid_to,
            "packs": _pack_sizes(desc),
        })
    return offers


def _valid(valid_to, now):
    if not valid_to:
        return True
    try:
        return datetime.fromisoformat(valid_to.replace("Z", "+00:00")) >= now
    except ValueError:
        return True


def main():
    with open(CONFIG) as f:
        cfg = json.load(f)

    now = datetime.now(timezone.utc)
    today = datetime.now().date()
    manual = cfg.get("manual_offers") or []
    products = [p for p in cfg["products"] if p.get("target_price_eur") is not None]

    cache = {}
    entries = {}  # (slug, retailer, price, pack) -> {"names": [...], "ok": ...}

    for p in products:
        slug = p.get("marktguru_slug")
        size_ml = p.get("size_ml")
        if not slug or not size_ml:
            continue
        if slug not in cache:
            cache[slug] = parse_offers(fetch(slug))

        best = None
        for o in cache[slug]:
            if o["brand"] != slug or not _valid(o["valid_to"], now):
                continue
            for n_units, ml in o["packs"]:
                if abs(ml - size_ml) > size_ml * 0.05:
                    continue
                unit_price = o["price"] / n_units
                if best is None or unit_price < best[0]:
                    best = (unit_price, o, n_units, ml)
                break

        # Manuelle Angebote (vom Nutzer bestätigt, fehlen in marktguru)
        for mo in manual:
            if mo.get("product") != p["name"]:
                continue
            try:
                vf = datetime.strptime(mo["valid_from"], "%Y-%m-%d").date()
                vt = datetime.strptime(mo["valid_to"], "%Y-%m-%d").date()
                if not (vf <= today <= vt):
                    continue
            except (KeyError, ValueError):
                pass
            mo_ml = mo.get("pack_ml")
            if not mo_ml or abs(mo_ml - size_ml) > size_ml * 0.05:
                continue
            n = int(mo.get("pack_count") or 1)
            up = float(mo["price_eur"]) / n
            if best is None or up < best[0]:
                best = (up, {"retailer": mo["retailer"]}, n, mo_ml)

        if not best:
            continue
        unit_price, o, n_units, ml = best
        ok = "✅" if unit_price <= p["target_price_eur"] else "❌"
        size_txt = (f"{ml:.0f} ml" if abs(ml - 330) < 5 or abs(ml - 250) < 5
                    else f"{ml/1000:.2f} l".replace(".", ","))
        pack_txt = f"{n_units}×{size_txt}" if n_units > 1 else size_txt
        key = (slug, o["retailer"], round(unit_price, 2), pack_txt)
        entry = entries.get(key)
        if entry is None:
            entries[key] = entry = {"names": [], "ok": ok}
        entry["names"].append(p["name"])

    by_market = defaultdict(list)
    for (slug, retailer, uprice, pack_txt), e in entries.items():
        names = e["names"]
        label = names[0] if len(names) == 1 else f"{names[0]} (+{len(names)-1} Sorten)"
        price_txt = f"{uprice:.2f}".replace(".", ",")
        by_market[retailer].append(
            (uprice, f"• {label} – {price_txt} € ({pack_txt}) {e['ok']} – {BASE}{slug}")
        )

    if not by_market:
        print("Sonst keine passenden Angebote.")
        return

    print("🥤 *Wochenangebote* (nach Markt, brutto)")
    shown = 0
    for market in sorted(by_market):
        print(f"\n📍 *{market}*")
        for _, line in sorted(by_market[market]):
            if shown >= 16:
                break
            print(line)
            shown += 1


if __name__ == "__main__":
    main()
