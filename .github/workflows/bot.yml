#!/usr/bin/env python3
"""
Hlídač inzerátů aut: Bazoš + Sauto -> ocenění -> Telegram.

Použití:
  python bot.py            jeden průchod (GitHub Actions)
  python bot.py --loop 10  běží pořád, kontrola každých 10 minut (PC)
  python bot.py --test     pošle zkušební zprávu do Telegramu
"""
import argparse
import html
import json
import os
import re
import statistics
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

BASE = Path(__file__).resolve().parent
CONFIG_FILE = BASE / "config.yaml"
SEEN_FILE = BASE / "seen.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.8",
}


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# ---------------------------------------------------------------- parsování

def to_int(text):
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


YEAR_RE = re.compile(r"(?<!\d)(19[89]\d|20[0-3]\d)(?!\d)")
KM_RE = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:[ .\u00a0]\d{3})+|\d{1,6})\s*(tis\.?|t\.?)?\s*km\b",
    re.IGNORECASE,
)


def parse_year(text):
    now = datetime.now().year
    for m in YEAR_RE.finditer(text or ""):
        y = int(m.group(1))
        if 1985 <= y <= now:
            return y
    return None


def parse_km(text):
    for m in KM_RE.finditer(text or ""):
        val = int(re.sub(r"\D", "", m.group(1)))
        if m.group(2) or val < 1000:
            val *= 1000
        if 5000 <= val <= 700000:
            return val
    return None


# ---------------------------------------------------------------- zdroje

def fetch_bazos(s, pages):
    params = {
        "hledat": s["bazos_query"],
        "rubriky": "auto",
        "hlokalita": s.get("psc", ""),
        "humkreis": s.get("okruh_km", ""),
        "cenaod": s.get("price_min", ""),
        "cenado": s.get("price_max", ""),
        "Submit": "Hledat",
        "kitx": "ano",
    }
    out = []
    for page in range(pages):
        url = "https://auto.bazos.cz/" + (f"{page * 20}/" if page else "")
        r = requests.get(url, params=params, headers=HEADERS, timeout=25)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        items = soup.select("div.inzeraty")
        if not items:
            break
        for it in items:
            a = it.select_one("h2.nadpis a") or it.select_one(".nadpis a")
            if not a:
                continue
            href = a.get("href", "")
            m = re.search(r"/inzerat/(\d+)/", href)
            if not m:
                continue
            link = href if href.startswith("http") else "https://auto.bazos.cz" + href
            title = a.get_text(" ", strip=True)
            price_el = it.select_one(".inzeratycena")
            desc_el = it.select_one(".popis")
            img_el = it.select_one("img.obrazek") or it.select_one("img")
            loc_el = it.select_one(".inzeratylok")
            desc = desc_el.get_text(" ", strip=True) if desc_el else ""
            text = f"{title} {desc}"
            out.append({
                "id": "bazos:" + m.group(1),
                "source": "Bazoš",
                "title": title,
                "price": to_int(price_el.get_text()) if price_el else None,
                "year": parse_year(text),
                "km": parse_km(text),
                "url": link,
                "image": img_el.get("src") if img_el else None,
                "location": loc_el.get_text(" ", strip=True) if loc_el else "",
                "desc": desc,
            })
        if len(items) < 20:
            break
        time.sleep(1.5)
    return out


def fetch_sauto(s, pages):
    params = {
        "limit": 100,
        "category_id": 838,
        "manufacturer_model_seo": s["sauto_model"],
    }
    if s.get("price_min"):
        params["price_from"] = s["price_min"]
    if s.get("price_max"):
        params["price_to"] = s["price_max"]
    hdr = dict(HEADERS, Accept="application/json")
    out = []
    for page in range(pages):
        params["offset"] = page * 100
        r = requests.get("https://www.sauto.cz/api/v1/items/search",
                         params=params, headers=hdr, timeout=25)
        r.raise_for_status()
        results = r.json().get("results") or []
        for it in results:
            iid = it.get("id")
            if not iid:
                continue
            man = (it.get("manufacturer_cb") or {}).get("seo_name", "")
            mod = (it.get("model_cb") or {}).get("seo_name", "")
            ym = re.match(r"(\d{4})", str(it.get("manufacturing_date") or ""))
            imgs = it.get("images") or []
            img = imgs[0].get("url") if imgs and isinstance(imgs[0], dict) else None
            if img and img.startswith("//"):
                img = "https:" + img
            loc = it.get("locality") or {}
            out.append({
                "id": f"sauto:{iid}",
                "source": "Sauto",
                "title": it.get("name") or f"{man} {mod}",
                "price": it.get("price"),
                "year": int(ym.group(1)) if ym else None,
                "km": it.get("tachometer"),
                "url": f"https://www.sauto.cz/osobni/detail/{man}/{mod}/{iid}",
                "image": img,
                "location": loc.get("municipality") or loc.get("district") or "",
                "desc": "",
            })
        if len(results) < 100:
            break
        time.sleep(1.5)
    return out


def fetch_description(l):
    """Celý popis z detailu inzerátu (jen pro Claude zhodnocení)."""
    try:
        if l["source"] == "Bazoš":
            r = requests.get(l["url"], headers=HEADERS, timeout=25)
            el = BeautifulSoup(r.text, "html.parser").select_one(".popisdetail")
            return el.get_text(" ", strip=True) if el else l["desc"]
        iid = l["id"].split(":", 1)[1]
        r = requests.get(f"https://www.sauto.cz/api/v1/items/{iid}", headers=HEADERS, timeout=25)
        return (r.json().get("result") or {}).get("description") or ""
    except Exception as e:  # noqa: BLE001
        log(f"  popis nešel stáhnout: {e}")
        return l["desc"]


# ---------------------------------------------------------------- vyhodnocení

def fmt(n):
    return f"{int(round(n)):,}".replace(",", " ")


def text_of(l):
    return f"{l['title']} {l['desc']}".lower()


def passes_filters(l, s, cfg):
    t = text_of(l)
    if any(w.lower() in t for w in cfg.get("exclude", [])):
        return False
    if s.get("year_min") and l["year"] and l["year"] < s["year_min"]:
        return False
    if s.get("km_max") and l["km"] and l["km"] > s["km_max"]:
        return False
    if l["price"] and s.get("price_min") and l["price"] < s["price_min"]:
        return False
    return True


def valuate(l, pool, cfg):
    v = cfg["valuation"]
    if not l["price"]:
        return None
    others = [p for p in pool if p["id"] != l["id"] and p["price"] and p["price"] >= 5000]

    def comparables(yr, kmr):
        res = []
        for p in others:
            if yr is not None and l["year"]:
                if not p["year"] or abs(p["year"] - l["year"]) > yr:
                    continue
            if kmr is not None and l["km"] and p["km"] and abs(p["km"] - l["km"]) > kmr:
                continue
            res.append(p["price"])
        return res

    prices, label = [], ""
    for yr, kmr, label in [(1, 40000, "rok ±1, km ±40 tis."),
                           (2, None, "rok ±2"),
                           (None, None, "celé hledání")]:
        prices = comparables(yr, kmr)
        if len(prices) >= v["min_comparables"]:
            break
    if len(prices) < 3:
        return None
    median = statistics.median(prices)
    sale = median * (1 - v["sale_discount_pct"] / 100)
    return {
        "median": median,
        "discount": (median - l["price"]) / median * 100,
        "sale": sale,
        "margin": sale - l["price"] - v["costs"],
        "count": len(prices),
        "basis": label,
    }


def claude_eval(l, val, cfg):
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    desc = fetch_description(l)[:3000]
    market = (f"Medián podobných inzerátů: {fmt(val['median'])} Kč ({val['count']} aut)."
              if val else "Tržní cenu se nepodařilo určit.")
    prompt = (
        "Jsi zkušený překupník ojetých aut v ČR. Zhodnoť inzerát stručně česky "
        "(max 5 krátkých řádků): 1) rizika a varovné signály, 2) realistická prodejní "
        "cena po opravě, 3) doporučení: volat / zvážit / nevolat.\n\n"
        f"Název: {l['title']}\nCena: {fmt(l['price'] or 0)} Kč\n"
        f"Rok: {l['year'] or '?'}  Km: {fmt(l['km']) if l['km'] else '?'}\n"
        f"{market}\nPopis: {desc or '(bez popisu)'}"
    )
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": cfg["claude"]["model"], "max_tokens": 400,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=60,
        )
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json().get("content", [])).strip()
    except Exception as e:  # noqa: BLE001
        log(f"  Claude chyba: {e}")
        return None


# ---------------------------------------------------------------- Telegram

def tg_creds(cfg):
    t = cfg.get("telegram") or {}
    return (os.getenv("TELEGRAM_TOKEN") or t.get("token") or "",
            str(os.getenv("TELEGRAM_CHAT_ID") or t.get("chat_id") or ""))


def tg_send(cfg, text, image=None):
    token, chat = tg_creds(cfg)
    if not token or not chat:
        log("Telegram není nastaven, zpráva jen do konzole:\n" + text)
        return
    base = f"https://api.telegram.org/bot{token}"
    if image and len(text) <= 1024:
        r = requests.post(base + "/sendPhoto", timeout=25, data={
            "chat_id": chat, "photo": image, "caption": text, "parse_mode": "HTML"})
        if r.ok:
            return
    r = requests.post(base + "/sendMessage", timeout=25, data={
        "chat_id": chat, "text": text, "parse_mode": "HTML"})
    if not r.ok:
        log(f"Telegram chyba: {r.status_code} {r.text[:200]}")


def build_message(l, s, val, kw, ai, cfg):
    e = html.escape
    lines = [f"🚗 <b>{e(l['title'])}</b>  <i>({e(s['name'])})</i>"]
    price = f"{fmt(l['price'])} Kč" if l["price"] else "cena neuvedena"
    if val:
        sign = "−" if val["discount"] >= 0 else "+"
        lines.append(f"💰 {price}  (trh ~{fmt(val['median'])} Kč, {sign}{abs(val['discount']):.0f} %)")
    else:
        lines.append(f"💰 {price}")
    info = []
    if l["year"]:
        info.append(f"📅 {l['year']}")
    if l["km"]:
        info.append(f"🛣 {fmt(l['km'])} km")
    if l["location"]:
        info.append(f"📍 {e(l['location'])}")
    if info:
        lines.append(" · ".join(info))
    if val:
        lines.append(f"📈 Odhad prodeje: {fmt(val['sale'])} Kč")
        lines.append(f"💵 Odhad marže: {fmt(val['margin'])} Kč "
                     f"(po nákladech {fmt(cfg['valuation']['costs'])} Kč)")
        lines.append(f"📊 Srovnáno s {val['count']} inzeráty ({val['basis']})")
    if kw:
        lines.append("⚠️ Klíčová slova: " + e(", ".join(kw)))
    if ai:
        lines.append(f"\n🤖 {e(ai)}")
    lines.append(f'\n🔗 <a href="{e(l["url"])}">Otevřít na {l["source"]}</a>')
    return "\n".join(lines)


# ---------------------------------------------------------------- stav

def load_seen():
    if SEEN_FILE.exists():
        try:
            data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
            data.setdefault("items", {})
            data.setdefault("searches", [])
            return data
        except Exception:  # noqa: BLE001
            pass
    return {"items": {}, "searches": []}


def save_seen(seen):
    limit = (datetime.now() - timedelta(days=45)).isoformat()
    seen["items"] = {k: v for k, v in seen["items"].items() if v >= limit}
    SEEN_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=0), encoding="utf-8")


# ---------------------------------------------------------------- hlavní běh

def run_once(cfg):
    seen = load_seen()
    now = datetime.now().isoformat()
    sent = 0
    max_sent = cfg["notify"]["max_per_run"]

    for s in cfg["searches"]:
        pool = []
        if s.get("bazos_query"):
            try:
                pool += fetch_bazos(s, cfg["pages"]["bazos"])
            except Exception as e:  # noqa: BLE001
                log(f"[{s['name']}] Bazoš chyba: {e}")
        if s.get("sauto_model"):
            try:
                pool += fetch_sauto(s, cfg["pages"]["sauto"])
            except Exception as e:  # noqa: BLE001
                log(f"[{s['name']}] Sauto chyba: {e}")
        log(f"[{s['name']}] staženo {len(pool)} inzerátů")

        first = s["name"] not in seen["searches"]
        new = [l for l in pool if l["id"] not in seen["items"]]
        for l in pool:
            seen["items"][l["id"]] = now
        if first:
            seen["searches"].append(s["name"])
            if pool:
                tg_send(cfg, f"✅ Začínám hlídat <b>{html.escape(s['name'])}</b> "
                             f"– na trhu je teď {len(pool)} inzerátů. "
                             f"Posílat budu jen nové.")
            continue

        for l in new:
            if sent >= max_sent:
                break
            if not passes_filters(l, s, cfg):
                continue
            val = valuate(l, pool, cfg)
            t = text_of(l)
            kw = [k for k in cfg.get("keywords", []) if k.lower() in t]
            cheap = val is not None and val["discount"] >= cfg["notify"]["min_discount_pct"]
            if not (cheap or (kw and cfg["notify"]["on_keywords"])):
                continue
            ai = claude_eval(l, val, cfg) if cfg["claude"]["enabled"] else None
            tg_send(cfg, build_message(l, s, val, kw, ai, cfg), l["image"])
            log(f"  -> posláno: {l['title']} ({l['price']} Kč)")
            sent += 1
            time.sleep(1)

    save_seen(seen)
    log(f"Hotovo, odesláno {sent} upozornění.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, metavar="MINUT", help="běžet pořád s tímto intervalem")
    ap.add_argument("--test", action="store_true", help="poslat zkušební zprávu")
    args = ap.parse_args()
    cfg = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))

    if args.test:
        tg_send(cfg, "👋 Bot funguje! Zprávy o nových autech budou chodit sem.")
        return
    if args.loop:
        while True:
            try:
                run_once(cfg)
            except Exception as e:  # noqa: BLE001
                log(f"Chyba běhu: {e}")
            time.sleep(args.loop * 60)
    run_once(cfg)


if __name__ == "__main__":
    main()
