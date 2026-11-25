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
HOME = {}  # doplní se z config.yaml (home)
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


DIESEL_TOKENS = ["tdi", "tdci", "cdti", "hdi", "bluehdi", "dci", "crdi", "jtd", "multijet", "cdi", "sdi", "d4d", "diesel", "nafta"]
PETROL_TOKENS = ["tsi", "tfsi", "fsi", "mpi", "htp", "ecoboost", "vtec", "benzín", "benzin"]
ENGINE_TYPES = ["tfsi", "tsi", "fsi", "mpi", "htp", "tdci", "tdi", "cdti", "bluehdi", "hdi", "dci", "crdi",
                "jtd", "multijet", "cdi", "sdi", "ecoboost", "g-tec"]
TRIMS = ["vrs", "rs", "scout", "gti", "gtd", "gte", "r-line", "sportline", "laurin", "l&k"]
DISP_RE = re.compile(r"(?<![\d.,])([0-6][.,]\d)(?![\d])\s*(?:l\b)?")
KW_RE = re.compile(r"(?<!\d)(\d{2,3})\s*kw\b", re.I)
HP_RE = re.compile(r"(?<!\d)(\d{2,3})\s*(?:ps|hp|koní|koni|k)\b", re.I)


def has_token(text, tok):
    return re.search(rf"(?<![a-z]){re.escape(tok)}(?![a-z])", text) is not None


def parse_specs(title, desc, extra=None):
    """Z názvu/popisu vytáhne motor, výkon, palivo, převodovku, karoserii a verzi.
    Název má přednost (popis často zmiňuje jiná auta / díly)."""
    extra = extra or {}
    t = (title or "").lower()
    full = f"{t} {(desc or '').lower()}"

    disp = extra.get("disp")
    if not disp:
        m = DISP_RE.search(t) or DISP_RE.search(full)
        disp = m.group(1).replace(",", ".") if m else None

    etype = next((e for e in ENGINE_TYPES if has_token(t, e)), None) \
        or next((e for e in ENGINE_TYPES if has_token(full, e)), None)

    kw = extra.get("kw")
    if not kw:
        m = KW_RE.search(t) or KW_RE.search(full)
        if m:
            kw = int(m.group(1))
        else:
            m = HP_RE.search(t)
            kw = round(int(m.group(1)) * 0.7355) if m else None
    if kw and not 30 <= kw <= 450:
        kw = None

    fuel = extra.get("fuel")
    if not fuel:
        if has_token(full, "lpg"):
            fuel = "LPG"
        elif has_token(full, "cng") or has_token(full, "g-tec"):
            fuel = "CNG"
        elif any(has_token(full, x) for x in DIESEL_TOKENS):
            fuel = "nafta"
        elif any(has_token(full, x) for x in PETROL_TOKENS):
            fuel = "benzín"

    gearbox = extra.get("gearbox")
    if not gearbox:
        if any(x in full for x in ["dsg", "automat", "tiptronic", "s tronic", "s-tronic", "multitronic"]):
            gearbox = "automat"
        else:
            gearbox = "manuál"          # neuvedeno = skoro vždy manuál

    body = extra.get("body")
    if not body:
        body = "kombi" if any(x in full for x in ["combi", "kombi", "variant", "touring", "avant", "estate", "sw "]) \
            else "standard"

    trim = next((x.upper() for x in TRIMS if has_token(t, x)), None) or "standard"
    if trim == "VRS":
        trim = "RS"

    engine = f"{disp} {etype.upper()}" if disp and etype else (disp or (etype.upper() if etype else None))
    return {"disp": disp, "engine": engine, "kw": kw, "fuel": fuel,
            "gearbox": gearbox, "body": body, "trim": trim}


DEALER_WORDS = ["autobazar", "auto bazar", "aaa auto", "aaaauto", "auto esa", "autoesa", "mototechna",
                "carvago", "s.r.o", "spol. s r.o", "a.s.", "na splátky", "splátky", "financování",
                "leasing", "úvěr", "uver", "odpočet dph", "odpocet dph", "možnost dph", "výkup aut",
                "vykup aut", "zápočet", "protiúčet", "prověřeno", "garance původu", "záruka na",
                "cena při financování", "cena bez dph", "akční cena", "showroom", "prodejní místo"]


def looks_like_dealer(text):
    t = (text or "").lower()
    return sum(w in t for w in DEALER_WORDS) >= 1


def specs_label(l):
    parts = []
    if l.get("engine"):
        parts.append(l["engine"] + (f" {l['kw']} kW" if l.get("kw") else ""))
    elif l.get("kw"):
        parts.append(f"{l['kw']} kW")
    for k in ("fuel", "gearbox"):
        if l.get(k):
            parts.append(l[k])
    if l.get("body") == "kombi":
        parts.append("kombi")
    if l.get("trim") and l["trim"] != "standard":
        parts.append(l["trim"])
    return " · ".join(parts)


# Rozpoznání modelu z názvu inzerátu -> Sauto (značka:model) pro tržní cenu
MODELS = [
    ("octavia", "skoda:octavia", "Škoda Octavia"),
    ("touareg", "volkswagen:touareg", "VW Touareg"),
    ("audi a3", "audi:a3", "Audi A3"),
    ("audi a4", "audi:a4", "Audi A4"),
    ("audi a5", "audi:a5", "Audi A5"),
    ("audi a6", "audi:a6", "Audi A6"),
    ("audi q3", "audi:q3", "Audi Q3"),
    ("audi q5", "audi:q5", "Audi Q5"),
    ("audi q7", "audi:q7", "Audi Q7"),
    ("audi tt", "audi:tt", "Audi TT"),
    ("superb", "skoda:superb", "Škoda Superb"),
    ("kodiaq", "skoda:kodiaq", "Škoda Kodiaq"),
    ("passat", "volkswagen:passat", "VW Passat"),
    ("touran", "volkswagen:touran", "VW Touran"),
    ("sharan", "volkswagen:sharan", "VW Sharan"),
    ("tiguan", "volkswagen:tiguan", "VW Tiguan"),
    ("fabia", "skoda:fabia", "Škoda Fabia"),
    ("karoq", "skoda:karoq", "Škoda Karoq"),
    ("caddy", "volkswagen:caddy", "VW Caddy"),
    ("polo", "volkswagen:polo", "VW Polo"),
    ("golf", "volkswagen:golf", "VW Golf"),
]
PART_START = ["motor", "převodovk", "prevodovk", "dveře", "dvere", "světl", "svetl", "nárazník",
              "naraznik", "kola", "pneu", "disky", "alu kola", "sedačk", "kapota", "blatník",
              "hlava", "turbo", "vstřik", "vstrik", "airbag", "rozprodám", "rozprodam",
              "díly", "dily", "náhradní díly", "nahradni dily", "zrcátk", "volant", "řídicí", "jednotka"]


def detect_model(title):
    t = (title or "").lower()
    for key, seo, label in MODELS:
        if has_token(t, key):
            return seo, label
    for key, seo, label in MODELS:          # bez značky: "A4 Avant", "Rapid 1.2"
        short = key.split()[-1]
        if len(short) >= 2 and short not in ("up", "tt") and has_token(t, short):
            return seo, label
    return None, None


def looks_like_part(title):
    t = (title or "").lower().strip()
    return any(t.startswith(p) for p in PART_START)


# ---------------------------------------------------------------- zdroje

def fetch_bazos(s, pages):
    params = {
        "hledat": s["bazos_query"],
        "rubriky": "auto",
        "hlokalita": s.get("psc", "") or HOME.get("psc", ""),
        "humkreis": s.get("okruh_km", "") or HOME.get("radius_km", ""),
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
            l = {
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
            }
            l.update(parse_specs(title, desc))
            l["dealer"] = looks_like_dealer(text)
            out.append(l)
        if len(items) < 20:
            break
        time.sleep(1)
    return out


def sauto_extra(it):
    """Strukturované údaje ze Sauta (pokud je API vrací)."""
    def cb(key):
        v = it.get(key)
        return (v.get("name") or v.get("seo_name") or "").lower() if isinstance(v, dict) else ""
    extra = {}
    vol = it.get("engine_volume")
    if isinstance(vol, (int, float)) and 500 < vol < 7000:
        extra["disp"] = f"{vol / 1000:.1f}"
    pw = it.get("engine_power")
    if isinstance(pw, (int, float)) and 30 <= pw <= 450:
        extra["kw"] = int(pw)
    f = cb("fuel_cb")
    if "naft" in f or "diesel" in f:
        extra["fuel"] = "nafta"
    elif "lpg" in f:
        extra["fuel"] = "LPG"
    elif "cng" in f:
        extra["fuel"] = "CNG"
    elif "benz" in f:
        extra["fuel"] = "benzín"
    g = cb("gearbox_cb")
    if "autom" in g:
        extra["gearbox"] = "automat"
    elif "manu" in g:
        extra["gearbox"] = "manuál"
    b = cb("vehicle_body_cb")
    if "kombi" in b:
        extra["body"] = "kombi"
    elif b:
        extra["body"] = "standard"
    return extra


def sauto_gps(loc):
    """Souřadnice z lokality Sauta (API je vrací pod různými klíči)."""
    for a, b in (("gps_lat", "gps_lon"), ("latitude", "longitude"), ("lat", "lon"), ("lat", "lng")):
        try:
            if loc.get(a) is not None and loc.get(b) is not None:
                return float(loc[a]), float(loc[b])
        except (TypeError, ValueError):
            pass
    g = loc.get("gps") or loc.get("coordinates")
    if isinstance(g, dict):
        try:
            return float(g.get("lat")), float(g.get("lon") or g.get("lng"))
        except (TypeError, ValueError):
            pass
    return None, None


def distance_km(lat1, lon1, lat2, lon2):
    from math import radians, sin, cos, asin, sqrt
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 6371 * 2 * asin(sqrt(a))


def too_far(l, cfg):
    """True = inzerát je dál, než chceš jet. Bazoš filtruje už při hledání."""
    home = cfg.get("home") or {}
    if not home.get("radius_km") or l["source"] != "Sauto":
        return False
    if l.get("lat") is not None and home.get("lat"):
        d = distance_km(home["lat"], home["lon"], l["lat"], l["lon"])
        l["distance"] = round(d)
        return d > home["radius_km"]
    regions = [r.lower() for r in home.get("regions", [])]
    if regions and l.get("region"):
        return not any(r in l["region"].lower() for r in regions)
    return False


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
            extra = sauto_extra(it)
            l = {
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
            }
            l["lat"], l["lon"] = sauto_gps(loc)
            l["region"] = str(loc.get("region") or loc.get("region_name") or "")
            l.update(parse_specs(l["title"], "", extra))
            user = it.get("user") if isinstance(it.get("user"), dict) else {}
            l["dealer"] = bool(it.get("premise") or it.get("premise_id") or user.get("premise")
                               or user.get("is_company") or looks_like_dealer(l["title"]))
            out.append(l)
        if len(results) < 100:
            break
        time.sleep(1)
    return out


def bazos_seller_info(l):
    """Otevře detail inzerátu na Bazoši: celý popis + kolik aut prodejce inzeruje.
    Vrací {"desc": str|None, "ads": int|None}. None = nepodařilo se zjistit."""
    from urllib.parse import urljoin
    info = {"desc": None, "ads": None}
    try:
        r = requests.get(l["url"], headers=HEADERS, timeout=25)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        el = soup.select_one(".popisdetail")
        if el:
            info["desc"] = el.get_text(" ", strip=True)
        link = None
        for a in soup.find_all("a", href=True):
            h = a["href"]
            if "hodnoceni" in h or "idphone=" in h or "idmail=" in h:
                link = urljoin(l["url"], h)
                break
        if not link:
            log("  odkaz na další inzeráty prodejce nenalezen")
            return info
        time.sleep(1)
        r = requests.get(link, headers=HEADERS, timeout=25)
        r.raise_for_status()
        own = l["id"].split(":", 1)[1]
        ids = set(re.findall(r"auto\.bazos\.cz/inzerat/(\d+)/", r.text))
        if not ids:  # odkazy bez domény
            ids = set(re.findall(r"/inzerat/(\d+)/", r.text))
        ids.add(own)
        info["ads"] = len(ids)
    except Exception as e:  # noqa: BLE001
        log(f"  kontrola prodejce selhala: {e}")
    return info


def fetch_description(l):
    """Celý popis z detailu inzerátu (jen pro Claude zhodnocení)."""
    if l.get("desc_full"):
        return l["desc_full"]
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
    if too_far(l, cfg):
        return False
    t = text_of(l)
    if cfg.get("only_private", True) and l.get("dealer"):
        return False
    if any(w.lower() in t for w in cfg.get("exclude", [])):
        return False
    if s.get("year_min") and l["year"] and l["year"] < s["year_min"]:
        return False
    km_max = s.get("km_max") or cfg.get("km_max")
    if km_max and l["km"] and l["km"] > km_max:
        return False
    if l["price"] and s.get("price_min") and l["price"] < s["price_min"]:
        return False
    if l["price"] and s.get("price_max") and l["price"] > s["price_max"]:
        return False
    if s.get("fuel") and l.get("fuel") and l["fuel"].lower() != str(s["fuel"]).lower():
        return False
    if s.get("gearbox") and l.get("gearbox") and l["gearbox"].lower() != str(s["gearbox"]).lower():
        return False
    return True


DEFECT_WORDS = ["nepojízd", "nepojizd", "na opravu", "závad", "zavad", "kontrolk", "bez stk",
                "na díly", "na dily", "na nd", "náhradní díly", "nahradni dily", "havar", "nestartuje",
                "neřadí", "neradi", "klepe", "kouří", "kouri", "netěsn", "prasklý", "nejede"]


def is_defective(l):
    t = f"{l.get('title', '')} {l.get('desc', '')} {l.get('desc_full', '')}".lower()
    return any(w in t for w in DEFECT_WORDS)


def car_costs(l, cfg):
    """Náklady: u funkčního auta costs_functional (výchozí 0), u auta s vadou costs."""
    v = cfg["valuation"]
    return v.get("costs", 15000) if is_defective(l) else v.get("costs_functional", 0)


def valuate(l, pool, cfg):
    """Ocenění podle co nejpodobnějších aut. Začíná přísně (stejný motor, výkon,
    převodovka, karoserie, verze, rok ±1, km ±40 tis.) a postupně povoluje."""
    v = cfg["valuation"]
    if not l["price"]:
        return None
    everyone = [p for p in pool if p["id"] != l["id"] and p["price"] and p["price"] >= 5000]
    private = [p for p in everyone if not p.get("dealer")]
    min_n = v.get("min_comparables", 5)

    def same(p, key):
        return l.get(key) is None or p.get(key) == l.get(key)

    def match(p, engine, kw, year, km, gear_body, fuel):
        if l["trim"] != p.get("trim"):                       # RS nesrovnávat s běžnou
            return False
        if engine and l.get("engine") and p.get("engine") != l["engine"]:
            return False
        if kw and l.get("kw") and p.get("kw") and abs(p["kw"] - l["kw"]) > 4:
            return False
        if gear_body and not (same(p, "gearbox") and same(p, "body")):
            return False
        if fuel and l.get("fuel") and p.get("fuel") and p["fuel"] != l["fuel"]:
            return False
        if year and l["year"] and (not p["year"] or abs(p["year"] - l["year"]) > year):
            return False
        if km and l["km"] and p["km"] and abs(p["km"] - l["km"]) > km:
            return False
        return True

    #        motor  kW    rok km     převod+karos palivo  popis
    tiers = [(True, True, 1, 40000, True, True, "stejný motor a výkon, rok ±1, km ±40 tis.", True),
             (True, True, 2, 60000, True, True, "stejný motor a výkon, rok ±2", True),
             (True, False, 2, None, True, True, "stejný motor, rok ±2", True),
             (False, False, 2, None, True, True, "stejné palivo, rok ±2", False),
             (False, False, 2, None, False, False, "jen model a rok ±2 – NEPŘESNÉ", False)]
    def search(others):
        prices, label, precise = [], "", False
        for eng, kw, yr, km, gb, fu, label, precise in tiers:
            if eng and not l.get("engine"):
                continue
            prices = [p["price"] for p in others if match(p, eng, kw, yr, km, gb, fu)]
            if len(prices) >= min_n:
                break
        return prices, label, precise

    # nejdřív jen soukromníci (bazary mají ceny nahoře), když jich je málo, tak všichni
    prices, label, precise = search(private)
    if len(prices) < min_n:
        prices, label, precise = search(everyone)
        if prices:
            label += ", vč. bazarů"
    if len(prices) < 3:
        return None

    median = statistics.median(prices)
    sale = median * (1 - v.get("sale_discount_pct", 0) / 100)
    buy = l["price"] * (1 - v.get("negotiation_pct", 0) / 100)
    return {
        "median": median,
        "discount": (median - l["price"]) / median * 100,
        "sale": sale,
        "buy": buy,
        "margin": sale - buy - car_costs(l, cfg),
        "costs": car_costs(l, cfg),
        "count": len(prices),
        "basis": label,
        "precise": precise,
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
        dist = f" (~{l['distance']} km)" if l.get("distance") is not None else ""
        info.append(f"📍 {e(l['location'])}{dist}")
    if info:
        lines.append(" · ".join(info))
    spec = specs_label(l)
    if spec:
        lines.append(f"⚙️ {e(spec)}")
    if val:
        lines.append(f"📈 Odhad prodeje: {fmt(val['sale'])} Kč")
        haggle = "po smlouvání" if cfg["valuation"].get("negotiation_pct", 0) else "za cenu z inzerátu"
        lines.append(f"💵 Odhad marže: <b>{fmt(val['margin'])} Kč</b> "
                     f"(při nákupu {haggle} {fmt(val['buy'])} Kč, "
                     + (f"náklady {fmt(val['costs'])} Kč)" if val.get("costs") else "bez nákladů, funkční auto)"))
        lines.append(f"📊 Srovnáno s {val['count']} auty ({e(val['basis'])})")
    if l["source"] == "Bazoš" and "seller_ads" in l:
        if l["seller_ads"] is None:
            lines.append("👤 Počet inzerátů prodejce se nepodařilo zjistit")
        else:
            lines.append(f"👤 Prodejce má na Bazoši {l['seller_ads']} inzerát(ů) aut")
    if kw:
        lines.append("⚠️ Klíčová slova: " + e(", ".join(kw)))
    if ai:
        lines.append(f"\n🤖 {e(ai)}")
    lines.append(f'\n🔗 <a href="{e(l["url"])}">Otevřít na {l["source"]}</a>')
    return "\n".join(lines)


def fingerprints(l, search_name):
    """Otisky inzerátu nezávislé na ID – stejné auto vložené znovu
    nebo na Bazoši i Sautu se pozná podle ceny, roku, km a názvu."""
    fps = []
    title = re.sub(r"[^a-z0-9]", "", (l["title"] or "").lower())
    if title and l["price"]:
        fps.append(f"fp:{search_name}:{title}:{l['price']}")
    if l["price"] and l["year"] and l["km"]:
        fps.append(f"fp:{search_name}:{l['price']}:{l['year']}:{l['km'] // 1000}")
    return fps


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

def prague_now():
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Europe/Prague"))


def stats_for_today(seen):
    today = prague_now().date().isoformat()
    st = seen.get("stats")
    if not st or st.get("date") != today:
        st = {"date": today, "downloaded": 0, "new": 0, "sent": 0, "reasons": {}, "top": [],
              "errors": []}
        seen["stats"] = st
    return st


def count(st, reason):
    st["reasons"][reason] = st["reasons"].get(reason, 0) + 1


def remember_top(st, l, val, label):
    if not val:
        return
    st["top"].append({"title": l["title"][:70], "price": l["price"], "margin": int(val["margin"]),
                      "url": l["url"], "label": label})
    st["top"] = sorted(st["top"], key=lambda x: -x["margin"])[:5]


def check_seller(l, cfg, st):
    """Bazoš: celý popis + počet inzerátů prodejce. True = v pořádku."""
    if l["source"] != "Bazoš" or not cfg.get("only_private", True):
        return True
    si = bazos_seller_info(l)
    if si["desc"]:
        l["desc_full"] = si["desc"]
        if looks_like_dealer(si["desc"]):
            log(f"  přeskočeno (bazar podle popisu): {l['title']}")
            count(st, "bazar")
            return False
    l["seller_ads"] = si["ads"]
    if si["ads"] is not None and si["ads"] > cfg.get("max_seller_ads", 4):
        log(f"  přeskočeno (prodejce má {si['ads']} aut): {l['title']}")
        count(st, "překupník")
        return False
    return True


def mark_new(pool, name, seen, now):
    new = []
    for l in pool:
        fps = fingerprints(l, name)
        if l["id"] not in seen["items"] and not any(f in seen["items"] for f in fps):
            new.append(l)
        seen["items"][l["id"]] = now
        for f in fps:
            seen["items"][f] = now
    return new


def first_run(name, pool, seen, cfg):
    if name in seen["searches"]:
        return False
    seen["searches"].append(name)
    if pool:
        tg_send(cfg, f"✅ Začínám hlídat <b>{html.escape(name)}</b> – na trhu je teď "
                     f"{len(pool)} inzerátů. Posílat budu jen nové.")
    return True


def run_models(cfg, seen, now, st, budget):
    sent = 0
    for s in cfg["searches"]:
        pool = []
        if s.get("bazos_query"):
            try:
                pool += fetch_bazos(s, cfg["pages"]["bazos"])
            except Exception as e:  # noqa: BLE001
                log(f"[{s['name']}] Bazoš chyba: {e}")
                st["errors"].append(f"{s['name']} Bazoš")
        if s.get("sauto_model"):
            try:
                pool += fetch_sauto(s, cfg["pages"]["sauto"])
            except Exception as e:  # noqa: BLE001
                log(f"[{s['name']}] Sauto chyba: {e}")
                st["errors"].append(f"{s['name']} Sauto")
        pool = list({l["id"]: l for l in pool}.values())
        log(f"[{s['name']}] staženo {len(pool)} inzerátů")
        st["downloaded"] += len(pool)

        new = mark_new(pool, s["name"], seen, now)
        if first_run(s["name"], pool, seen, cfg):
            continue
        st["new"] += len(new)

        for l in new:
            if not passes_filters(l, s, cfg):
                count(st, "daleko" if l.get("distance") or too_far(l, cfg) else "filtr (cena/rok/km/palivo)")
                continue
            val = valuate(l, pool, cfg)
            kw = [k for k in cfg.get("keywords", []) if k.lower() in text_of(l)]
            n = cfg["notify"]
            remember_top(st, l, val, s["name"])
            cheap = (val is not None
                     and val["margin"] >= n.get("min_margin", 5000)
                     and val["discount"] >= n.get("min_discount_pct", 0)
                     and (val["precise"] or not n.get("only_precise", False)))
            if not (cheap or (kw and n.get("on_keywords", True))):
                count(st, "malá marže" if val else "nejde ocenit")
                continue
            if sent >= budget:
                count(st, "limit zpráv")
                continue
            if not check_seller(l, cfg, st):
                continue
            ai = claude_eval(l, val, cfg) if cfg["claude"]["enabled"] else None
            tg_send(cfg, build_message(l, s, val, kw, ai, cfg), l["image"])
            log(f"  -> posláno: {l['title']} ({l['price']} Kč)")
            sent += 1
            time.sleep(1)
    return sent


def run_hunter(cfg, seen, now, st, budget):
    """Lovec závad: nepojízdná / auta s vadou u všech podporovaných modelů."""
    h = cfg.get("lovec") or {}
    if not h.get("enabled"):
        return 0
    name = "Lovec závad"
    pool = {}
    for q in h.get("queries", []):
        s = {"name": name, "bazos_query": q, "price_min": h.get("price_min", 5000),
             "price_max": h.get("price_max", 90000)}
        try:
            for l in fetch_bazos(s, h.get("pages", 2)):
                pool.setdefault(l["id"], l)
        except Exception as e:  # noqa: BLE001
            log(f"[{name}] Bazoš chyba ({q}): {e}")
            st["errors"].append(f"Lovec {q}")
        time.sleep(1)
    pool = list(pool.values())
    log(f"[{name}] staženo {len(pool)} inzerátů")
    st["downloaded"] += len(pool)
    new = mark_new(pool, name, seen, now)
    if first_run(name, pool, seen, cfg):
        return 0
    st["new"] += len(new)

    market, sent = {}, 0
    for l in new:
        if looks_like_part(l["title"]) or any(w in text_of(l) for w in cfg.get("exclude", [])):
            count(st, "lovec: díl / nechtěné")
            continue
        if too_far(l, cfg):
            count(st, "daleko")
            continue
        if not l["price"] or l["price"] < h.get("price_min", 5000):
            count(st, "lovec: bez ceny")
            continue
        km_max = h.get("km_max") or cfg.get("km_max")
        if km_max and l["km"] and l["km"] > km_max:
            count(st, "nájezd nad limit")
            continue
        if h.get("year_min") and l["year"] and l["year"] < h["year_min"]:
            count(st, "filtr (cena/rok/km/palivo)")
            continue
        seo, label = detect_model(l["title"])
        if not seo:
            count(st, "lovec: neznámý model")
            continue
        if seo not in market:
            try:
                market[seo] = [p for p in fetch_sauto({"sauto_model": seo}, 2)
                               if not any(k in p["title"].lower() for k in ("nepojízd", "havar", "na opravu", "na díly"))]
            except Exception as e:  # noqa: BLE001
                log(f"  tržní cena {label} nešla stáhnout: {e}")
                market[seo] = []
        val = valuate(l, market[seo], cfg)
        if not val:
            count(st, "lovec: málo srovnání")
            continue
        room = val["margin"] - h.get("target_profit", 20000)
        val["repair_room"] = room
        remember_top(st, l, val, f"Lovec · {label}")
        if room < h.get("min_repair_room", 15000):
            count(st, "lovec: málo prostoru na opravu")
            continue
        if sent >= budget:
            count(st, "limit zpráv")
            continue
        if not check_seller(l, cfg, st):
            continue
        kw = [k for k in cfg.get("keywords", []) if k.lower() in text_of(l)]
        ai = claude_eval(l, val, cfg) if cfg["claude"]["enabled"] else None
        s = {"name": f"Lovec závad · {label}"}
        msg = build_message(l, s, val, kw, ai, cfg).replace(
            "📈 Odhad prodeje", "📈 Prodej po opravě", 1)
        extra = (f"🔧 <b>Na opravu zbývá až {fmt(room)} Kč</b> "
                 f"(při zisku {fmt(h.get('target_profit', 20000))} Kč)")
        msg = msg.replace("\n📊", f"\n{extra}\n📊", 1)
        tg_send(cfg, msg, l["image"])
        log(f"  -> lovec posláno: {l['title']} ({l['price']} Kč)")
        sent += 1
        time.sleep(1)
    return sent


def maybe_summary(cfg, seen, st):
    hour = cfg.get("summary_hour", 21)
    if hour is None or prague_now().hour < int(hour) or seen.get("summary_sent") == st["date"]:
        return
    lines = [f"📋 <b>Denní souhrn {st['date']}</b>",
             f"Prošlo {st['downloaded']} inzerátů (souhrnně za běhy), nových {st['new']}, "
             f"posláno {st['sent']}."]
    if st["reasons"]:
        lines.append("\nProč nové inzeráty nepřišly:")
        for r, c in sorted(st["reasons"].items(), key=lambda x: -x[1]):
            lines.append(f"• {html.escape(r)}: {c}")
    if st["top"]:
        lines.append("\nNejlepší kusy dne podle odhadu marže:")
        for t in st["top"][:3]:
            lines.append(f'• <a href="{html.escape(t["url"])}">{html.escape(t["title"])}</a> – '
                         f'{fmt(t["price"] or 0)} Kč, marže ~{fmt(t["margin"])} Kč '
                         f'({html.escape(t["label"])})')
    if st["errors"]:
        errs = sorted(set(st["errors"]))
        lines.append("\n⚠️ Chyby zdrojů: " + html.escape(", ".join(errs[:8])))
    tg_send(cfg, "\n".join(lines))
    seen["summary_sent"] = st["date"]


def run_once(cfg):
    seen = load_seen()
    now = datetime.now().isoformat()
    st = stats_for_today(seen)
    budget = cfg["notify"]["max_per_run"]
    sent = run_models(cfg, seen, now, st, budget)
    sent += run_hunter(cfg, seen, now, st, budget - sent)
    st["sent"] += sent
    maybe_summary(cfg, seen, st)
    save_seen(seen)
    log(f"Hotovo, odesláno {sent} upozornění.")


def within_hours(cfg):
    """Je teď v Česku čas, kdy se smí posílat upozornění?"""
    hours = cfg.get("notify_hours")
    if not hours:
        return True
    from zoneinfo import ZoneInfo
    h = datetime.now(ZoneInfo("Europe/Prague")).hour
    start, end = int(hours[0]), int(hours[1])
    return start <= h < end


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, metavar="MINUT", help="běžet pořád s tímto intervalem")
    ap.add_argument("--test", action="store_true", help="poslat zkušební zprávu")
    args = ap.parse_args()
    cfg = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))
    HOME.update(cfg.get("home") or {})

    if args.test:
        tg_send(cfg, "👋 Bot funguje! Zprávy o nových autech budou chodit sem.")
        return
    if args.loop:
        while True:
            try:
                if within_hours(cfg):
                    run_once(cfg)
                else:
                    log("Mimo povolené hodiny, čekám.")
            except Exception as e:  # noqa: BLE001
                log(f"Chyba běhu: {e}")
            time.sleep(args.loop * 60)
    if not within_hours(cfg):
        log("Mimo povolené hodiny – nic nestahuji, noční inzeráty pošlu ráno.")
        return
    run_once(cfg)


if __name__ == "__main__":
    main()
