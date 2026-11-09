#!/usr/bin/env python3
"""Getränke-Angebotscheck über marktguru.de + HTML-Mail.

Quelle: https://www.marktguru.de/b/<brand> (JSON-Angebote in der Seite)
Ausgabe: kompakte Textliste (stdout/Telegram) + gestylte HTML-Mail an
info@camiro24.de (Betreff "Wochenangebote", Links hinter dem Produktnamen).
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
PW_FILE = "/root/.openclaw/workspace/secrets/gmail_app_password.txt"
SMTP_USER = "camirobot24@gmail.com"


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
    packs = []

    def ml_of(size, unit):
        u = (unit or "").lower()
        if u.startswith("ml"):
            val = size
        elif u.startswith("l"):
            val = size * 1000
        else:
            val = size * 1000 if size < 10 else size
        return val if 50 <= val <= 5000 else None

    for mm in re.finditer(r"(\d+)\s*[x×]\s*([\d,.]+)\s*([a-zA-Z]{0,2})", desc):
        try:
            n, size = int(mm.group(1)), float(mm.group(2).replace(",", "."))
        except ValueError:
            continue
        if not (1 <= n <= 200):
            continue
        ml = ml_of(size, mm.group(3))
        if ml:
            packs.append((n, ml))
    for mm in re.finditer(r"(?:je|Je)?\s*([\d,.]+)\s*(ml|l)(?![a-zA-Z])", desc):
        try:
            size = float(mm.group(1).replace(",", "."))
        except ValueError:
            continue
        ml = ml_of(size, mm.group(2))
        if ml:
            packs.append((1, ml))
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
        offers.append({"brand": brand, "retailer": retailer, "desc": desc,
                       "price": price, "valid_to": valid_to, "packs": _pack_sizes(desc)})
    return offers


def _valid(valid_to, now):
    if not valid_to:
        return True
    try:
        return datetime.fromisoformat(valid_to.replace("Z", "+00:00")) >= now
    except ValueError:
        return True


def _badge(ok):
    bg, fg = ("#dcfce7", "#166534") if ok else ("#fee2e2", "#991b1b")
    return (f'<span style="display:inline-block;padding:2px 8px;border-radius:999px;'
            f'background:{bg};color:{fg};font:12px Arial,sans-serif;">{ok}</span>')


def build_html(entries):
    """Gestylte HTML-Mail: Produktname ist der Link (kein separater URL-Text)."""
    parts = []
    for market in sorted(entries):
        parts.append(
            f'<tr><td colspan="3" style="padding:18px 22px 6px;font:bold 15px Arial,sans-serif;'
            f'color:#111827;background:#f9fafb;">📍 {market}</td></tr>'
        )
        for price, name, pack_txt, ok, slug, total, unitp in sorted(entries[market]):
            parts.append(
                f'<tr>'
                f'<td style="padding:9px 22px;border-bottom:1px solid #e5e7eb;font:14px Arial,sans-serif;">'
                f'<a href="{BASE}{slug}" style="color:#1d4ed8;text-decoration:none;">{name}</a></td>'
                f'<td style="padding:9px 10px;border-bottom:1px solid #e5e7eb;font:14px Arial,sans-serif;'
                f'color:#111827;white-space:nowrap;"><b>{price:.2f}</b> € '
                f'<span style="color:#6b7280;font-size:12px;">({pack_txt})</span></td>'
                f'<td style="padding:9px 22px;border-bottom:1px solid #e5e7eb;text-align:right;">{_badge(ok)}</td>'
                f'</tr>'
            )
    return f'''<!DOCTYPE html>
<html><body style="margin:0;padding:24px;background:#eef1f5;">
  <div style="max-width:660px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;
              box-shadow:0 1px 4px rgba(0,0,0,.08);font-family:Arial,sans-serif;">
    <div style="background:#0f172a;padding:22px 24px;">
      <div style="color:#fff;font:bold 21px Arial,sans-serif;">🥤 Wochenangebote</div>
      <div style="color:#94a3b8;font:13px Arial,sans-serif;margin-top:4px;">
        Preisvergleich gegen unsere Zielpreise · brutto · KW{datetime.now().isocalendar()[1]}
      </div>
    </div>
    <table style="width:100%;border-collapse:collapse;">{''.join(parts)}</table>
    <div style="padding:14px 22px;color:#6b7280;font:12px Arial,sans-serif;">
      ✅ unter Zielpreis · ❌ über Zielpreis · Quelle: marktguru.de
    </div>
  </div>
</body></html>'''


def send_email(to_addr, subject, text_body, html_body):
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.header import Header

    try:
        with open(PW_FILE) as f:
            password = f.read().strip()
    except OSError:
        return False
    if not password:
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = SMTP_USER
    msg["To"] = to_addr
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(SMTP_USER, password)
            server.sendmail(SMTP_USER, [to_addr], msg.as_string())
        return True
    except Exception as e:
        print(f"# E-Mail-Fehler: {e}", file=sys.stderr)
        return False


def main():
    with open(CONFIG) as f:
        cfg = json.load(f)

    now = datetime.now(timezone.utc)
    today = datetime.now().date()
    manual = cfg.get("manual_offers") or []
    products = [p for p in cfg["products"] if p.get("target_price_eur") is not None]
    cache = {}
    entries = {}   # (slug, retailer, price, pack) -> {"names": [], "ok": ...}

    for p in products:
        slug, size_ml = p.get("marktguru_slug"), p.get("size_ml")
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
                up = o["price"] / n_units
                if best is None or up < best[0]:
                    best = (up, o, n_units, ml, o["price"])
                break

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
                best = (up, {"retailer": mo["retailer"]}, n, mo_ml, float(mo["price_eur"]))

        if not best:
            continue
        unit_price, o, n_units, ml, total_price = best
        ok = unit_price <= p["target_price_eur"]
        size_txt = (f"{ml:.0f} ml" if abs(ml - 330) < 5 or abs(ml - 250) < 5
                    else f"{ml/1000:.2f} l".replace(".", ","))
        pack_txt = f"{n_units}×{size_txt}" if n_units > 1 else size_txt
        key = (slug, o["retailer"], round(unit_price, 2), pack_txt)
        e = entries.get(key)
        if e is None:
            entries[key] = e = {"names": [], "ok": ok, "total": total_price,
                                "unit": unit_price, "n": n_units, "ml": ml}
        e["names"].append(p["name"])

    if not entries:
        text = "Sonst keine passenden Angebote."
        print(text)
        return

    # ── Textversion (stdout/Telegram) ──
    by_market_text = defaultdict(list)
    by_market_html = defaultdict(list)
    for (slug, retailer, price, pack_txt), e in entries.items():
        names = e["names"]
        label = names[0] if len(names) == 1 else f"{names[0]} (+{len(names)-1} Sorten)"
        ok = "✅" if e["ok"] else "❌"
        price_txt = f"{price:.2f}".replace(".", ",")
        total_txt = f"{e['total']:.2f}".replace(".", ",")
        by_market_text[retailer].append(
            (price, f"• {label} – {total_txt} € · {price_txt} €/Stk ({pack_txt}) {ok} – {BASE}{slug}"))
        by_market_html[retailer].append((price, label, pack_txt, ok, slug, e["total"], price))

    out = ["🥤 Wochenangebote (nach Markt, brutto)"]
    shown = 0
    for market in sorted(by_market_text):
        out.append(f"\n📍 {market}")
        for _, line in sorted(by_market_text[market]):
            if shown >= 16:
                break
            out.append(line)
            shown += 1
    text = "\n".join(out)
    print(text)

    mail = cfg.get("email") or {}
    if mail.get("enabled"):
        html = build_html(by_market_html)
        ok = send_email(mail.get("to", "info@camiro24.de"),
                        mail.get("subject", "Wochenangebote"), text, html)
        print(("📧 E-Mail (HTML) gesendet an " + mail.get("to", "")) if ok
              else "⚠️ E-Mail-Versand fehlgeschlagen", file=sys.stderr)


if __name__ == "__main__":
    main()
