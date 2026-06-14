#!/usr/bin/env python3
"""
🔍 Test: Replay raw stream data through detection logic
========================================================
Zeigt Schritt für Schritt, was der Erkennungsmechanismus aus den
Rohdaten macht — ohne die echte DB zu verändern.

Usage:
  python3 tests/test_card_detection.py            # Heute (00:00 – jetzt)
  python3 tests/test_card_detection.py --full     # Komplettes Log
  python3 tests/test_card_detection.py --since 2026-06-12T22:00
"""

import sys
import os
import json
import re
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_FILE = os.path.join(SCRIPT_DIR, "data", "raw_stream.log")
STATE_FILE = os.path.join(SCRIPT_DIR, "data", "current_state.json")
CATALOG_FILE = os.path.join(SCRIPT_DIR, "data", "product_catalog.json")

# ============================================================
#  PARSER (identisch zum live-Listener)
# ============================================================
def parse_line(line):
    if "ack " not in line and "Hello" in line:
        return {"type": "greeting", "raw": line}
    if ": ack" in line:
        idx = line.index(": ack")
        cmd = line[:idx].strip()
        rest = line[idx + 5:].strip()
        values = rest.strip().split()
        result = {"type": "data", "command": cmd, "values": values, "raw": line}

        if "temperaturetwo" in cmd and len(values) >= 1:
            result["temp_c"] = int(values[0]) / 10.0
        elif "temperature" in cmd and len(values) >= 1:
            result["temp_c"] = int(values[0]) / 10.0
        elif "readprice" in cmd:
            parts = cmd.split()
            if len(parts) >= 3:
                result["rack"] = int(parts[1])
                result["slot"] = int(parts[2])
            if len(values) >= 2:
                result["slot_id"] = int(values[0])
                result["price_cent"] = int(values[1])
                result["price_eur"] = int(values[1]) / 100.0
        elif "readerrors" in cmd and len(values) >= 3:
            result["error_byte"] = int(values[0])
            result["error_code"] = int(values[1])
            result["error_count"] = int(values[2])
        elif "selstate" in cmd:
            result["state"] = values[0] if values else "unknown"
            if "viewprice" in result["state"] and len(values) >= 2:
                try:
                    result["viewprice_cent"] = int(values[1])
                except ValueError:
                    pass
        elif "readcredit" in cmd and len(values) >= 2:
            result["credit_units"] = int(values[0])
            result["credit_count"] = int(values[1])
        elif "readclock" in cmd and len(values) >= 7:
            result["datetime"] = {"day": int(values[0]), "month": int(values[1]),
                                   "year": int(values[2]), "weekday": int(values[3]),
                                   "hour": int(values[4]), "minute": int(values[5]),
                                   "second": int(values[6])}
        return result
    return {"type": "unknown", "raw": line}


# ============================================================
#  SIMULATION
# ============================================================
def simulate(raw_lines, state, title="Simulation", verbose=False):
    """Replay lines through detection, report results."""
    results = []
    
    # Globale Zustände wie im Live-Listener
    last_credit_count = 0
    sale_in_progress = False
    sale_price_cent = 0
    current_sale_slot = None
    last_erog_time = 0.0
    sale_credit_before = 0
    sale_is_card = False
    sale_credit_price = None
    recorded_sales = []

    sim_time = 0.0  # simulierte Zeit in Sekunden
    verbose = verbose or "--verbose" in sys.argv
    
    for line in raw_lines:
        sim_time += 1.0  # ~1 Sekunde pro Zeile (grob)
        
        # Timestamp aus der Raw-Log-Zeile extrahieren
        ts_match = re.match(r'\[([^\]]+)\] (.*)', line)
        if not ts_match:
            continue
        raw_ts = ts_match.group(1)
        payload = ts_match.group(2)
        
        parsed = parse_line(payload.strip())
        if parsed.get("type") != "data":
            continue
        
        cmd = parsed.get("command", "")
        events = []
        
        # ---- Zustand aktualisieren (identisch zu process_line) ----
        if "readprice" in cmd:
            slot_num = str(parsed['rack'] * 100 + parsed['slot'])
            val = {"slot_id": parsed["slot_id"], "price_cent": parsed["price_cent"], "price_eur": parsed["price_eur"]}
            prices = state.setdefault("prices", {})
            prices[slot_num] = val
        
        elif "readerrors" in cmd:
            state["errors"] = {"byte": parsed["error_byte"], "code": parsed["error_code"], "count": parsed["error_count"]}
        
        elif "selstate" in cmd:
            state["selstate"] = parsed["state"]
        
        elif "readcredit" in cmd:
            state["credit"] = {"units": parsed["credit_units"], "count": parsed["credit_count"]}
        
        elif "readclock" in cmd:
            state["clock"] = parsed["datetime"]
        
        # ---- Detektion (identisch zum live detect_sale) ----
        ctx = {"sale": False, "card": False, "cash": False, "skip": False, "price": None}
        
        if cmd.startswith("select "):
            parts = cmd.split()
            if len(parts) >= 3:
                rack = int(parts[1])
                slot_num = int(parts[2])
                current_sale_slot = str(rack * 100 + slot_num)
                sale_price_cent = 0
            ctx["select"] = current_sale_slot
            if verbose:
                print(f"  📍 SELECT  Slot {current_sale_slot}  (Rack {rack}, Nummer {slot_num})")
        
        if "selstate" in cmd:
            new_state = parsed.get("state", "")
            
            if new_state.startswith("viewprice"):
                vp = parsed.get("viewprice_cent", 0)
                if vp and 1 <= vp <= 10000:
                    sale_price_cent = vp
                ctx["viewprice"] = sale_price_cent
                if verbose:
                    print(f"  💲 viewprice: {sale_price_cent} Cent (sale_price_cent={sale_price_cent})")
            elif new_state.startswith("ageprotection"):
                if verbose:
                    print(f"  🔞 Altersprüfung")
        
        if "readcredit" in cmd:
            cu = parsed.get("credit_units", 0)
            if cu == 4900 and not sale_in_progress:
                sale_is_card = True
                ctx["card_detected"] = True
            # Cash: ersten Kredit-Drop tracken
            if sale_in_progress and not sale_is_card:
                if sale_credit_price is None and cu < sale_credit_before and cu >= 0:
                    diff = sale_credit_before - cu
                    if 1 <= diff <= 10000:
                        sale_credit_price = diff
            if verbose and cu in (0, 4900, 4450, 1000, 550, 200):
                print(f"  💰 readcredit: {cu} Cent (sale_in_progress={sale_in_progress}, sale_is_card={sale_is_card})")
        
        if "selstate" in cmd:
            new_state = parsed.get("state", "")
            
            if new_state.startswith("erog"):
                if sim_time - last_erog_time > 3:
                    last_erog_time = sim_time
                    if not sale_in_progress:
                        sale_in_progress = True
                        credit = state.get("credit", {})
                        sale_credit_before = credit.get("units", 0) or 0
                        sale_is_card = (sale_credit_before == 4900)
                        ctx["erog"] = "EROG START"
                        ctx["credit_before"] = sale_credit_before
                        ctx["card"] = sale_is_card
            
            # ── SALE ABSCHLIESSEN (enderog + take) ──────
            if new_state.startswith("enderog") or new_state.startswith("take"):
                if sale_in_progress:
                    if sim_time - last_erog_time < 45:
                        price = None
                        
                        if sale_is_card:
                            # 💳 KARTE: Preis aus Slot-Tabelle
                            slot_idx = str(current_sale_slot) if current_sale_slot else None
                            prices = state.get("prices", {})
                            if slot_idx and slot_idx in prices:
                                p_entry = prices[slot_idx]
                                if isinstance(p_entry, dict) and "price_cent" in p_entry:
                                    price = p_entry["price_cent"]
                                elif isinstance(p_entry, (int, float)):
                                    price = int(p_entry)
                            if price:
                                ctx["price_source"] = "slot_table"
                        else:
                            # 💵 CASH: Preis aus Kredit-Drop (während erog getrackt)
                            if sale_credit_price and 1 <= sale_credit_price <= 10000:
                                price = sale_credit_price
                            else:
                                # Fallback: Kredit-Differenz
                                credit = state.get("credit", {})
                                sale_credit_after = credit.get("units", 0) or 0
                                diff = sale_credit_before - sale_credit_after
                                if 1 <= diff <= 10000:
                                    price = diff
                            ctx["price_source"] = "credit_drop" if sale_credit_price else "credit_diff"
                        
                        ctx["price"] = price
                        ctx["state"] = new_state
                        
                        if verbose:
                            icon = "💳 KARTE" if sale_is_card else "💵 CASH"
                            print(f"  🔚 {new_state.upper()} | {icon} | credit_before={sale_credit_before} | price={price}¢")
                        
                        if price and price > 0:
                            amount_eur = price / 100.0
                            payment = "card" if sale_is_card else "cash"
                            
                            product_name = None
                            if current_sale_slot:
                                cat_entry = state.get("catalog", {}).get(str(current_sale_slot))
                                if cat_entry:
                                    product_name = cat_entry.get("name")
                            
                            recorded_sales.append({
                                "ts": raw_ts,
                                "slot": current_sale_slot,
                                "product": product_name or "?",
                                "amount": amount_eur,
                                "payment": payment,
                                "credit_before": sale_credit_before
                            })
                            ctx["sale"] = True
                            ctx["payment"] = payment
                            
                            # Reset
                            sale_in_progress = False
                            sale_price_cent = 0
                            sale_is_card = False
                            sale_credit_price = None
                            current_sale_slot = None
                        else:
                            ctx["skip"] = True
                    else:
                        ctx["skip"] = "timeout"
                    
                    sale_in_progress = False
                    sale_price_cent = 0
                    sale_credit_before = 0
                    sale_credit_price = None
        
            elif new_state.startswith("erog"):
                if sim_time - last_erog_time > 3:
                    if verbose:
                        icon = "💳 KARTE" if sale_is_card else "💵 CASH"
                        if sale_in_progress:
                            print(f"  ⏩ EROG (bereits in_progress) — übersprungen")
                        else:
                            print(f"  ▶️  EROG START | {icon} | credit_before={sale_credit_before} | slot={current_sale_slot}")
    
    return recorded_sales


# ============================================================
#  MAIN
# ============================================================
def main():
    since_filter = None
    if "--since" in sys.argv:
        idx = sys.argv.index("--since")
        if idx + 1 < len(sys.argv):
            since_filter = sys.argv[idx + 1].replace("T", " ")
    
    # Nur heute ab 00:00, oder gesamtes Log
    if "--full" in sys.argv:
        filter_str = None
        title = "KOMPLETTES LOG"
    else:
        filter_str = "2026-06-13"
        title = f"HEUTE ({filter_str})"
    
    # Raw-Log laden
    with open(RAW_FILE) as f:
        all_lines = f.readlines()
    
    if filter_str:
        lines = [l for l in all_lines if filter_str in l]
    else:
        lines = all_lines
    
    if since_filter:
        lines = [l for l in lines if since_filter in l]
    
    print(f"{'='*70}")
    print(f"🔍 TEST: {title}")
    print(f"   Zeilen: {len(lines)}")
    print(f"{'='*70}")
    
    # Katalog laden
    catalog = {}
    try:
        with open(CATALOG_FILE) as f:
            catalog = json.load(f)
    except FileNotFoundError:
        pass
    
    # State initialisieren
    state = {"catalog": catalog}
    
    try:
        with open(STATE_FILE) as f:
            old_state = json.load(f)
            if "prices" in old_state:
                state["prices"] = old_state["prices"]
    except FileNotFoundError:
        pass
    
    # Simulieren
    sales = simulate(lines, state, title)
    
    # Ergebnisse
    print(f"\n{'─'*70}")
    if not sales:
        print("❌ Keine Verkäufe erkannt.")
    else:
        print(f"✅ {len(sales)} Verkäufe erkannt:\n")
        cash_total = 0
        card_total = 0
        for s in sales:
            icon = "💳" if s["payment"] == "card" else "💵"
            print(f"  {s['ts']}  Slot {s['slot']:>4s}  {s['product']:<25s}  {s['amount']:>5.2f}€  {icon}")
            if s["payment"] == "card":
                card_total += s["amount"]
            else:
                cash_total += s["amount"]
        print(f"\n  {'─'*50}")
        print(f"  💵 Cash:  {cash_total:.2f}€")
        print(f"  💳 Karte: {card_total:.2f}€")
        print(f"  {'─'*50}")
        print(f"  💰 Total: {cash_total + card_total:.2f}€")
    
    print(f"\n{'='*70}")
    print(f"💡 KARTEN-ERKENNUNG:")
    print(f"   Signal: credit_before == 4900 (49,00€ Reserve)")
    print(f"   Preis:  Slot-Preis-Tabelle (prices[\"<rack*100+slot>\"])")
    print(f"   Abschluss: enderog (kein take-State bei Karte)")
    print(f"")
    print(f"💡 CASH-ERKENNUNG:")
    print(f"   Signal: credit_before < 4900 (eingeworfener Betrag)")
    print(f"   Preis:  viewprice (Display-Anzeige)")
    print(f"   Abschluss: take (physische Entnahme)")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
