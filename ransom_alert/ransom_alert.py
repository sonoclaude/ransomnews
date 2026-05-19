#!/usr/bin/env python3
"""
ransom_alert.py — Monitor rivendicazioni ransomware Italia
Versione GitHub Actions: gira sui server GitHub ogni 30 minuti.

Sorgenti:
  - bsky.app/profile/ecrime.ch (API Bluesky diretta)
  - ransom-db.com/live-updates (scraping)
  - ransomlook.io/recent (scraping)

Ping di stato: 07:30 e 20:30 ora italiana
"""

import json
import os
import time
import hashlib
import logging
import re
from pathlib import Path
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup

# ─── CONFIGURAZIONE ───────────────────────────────────────────────────────────

TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Schedule corrente passata dal workflow
CURRENT_SCHEDULE = os.environ.get("GITHUB_EVENT_SCHEDULE", "")

# Cron dei ping (in UTC)
PING_SCHEDULES = {"30 5 * * *", "30 18 * * *"}

SEEN_FILE = Path(__file__).parent / "seen.json"
TIMEOUT   = 15

ITALY_KEYWORDS = [
    "italy", "italia", "italian",
    r"\.it\b",
    r"\bS\.r\.l\b", r"\bSrl\b", r"\bS\.p\.A\b", r"\bSpA\b",
    r"\bs\.n\.c\b", r"\bsnc\b", r"\bs\.a\.s\b", r"\bsas\b",
    "milano", "roma", "napoli", "torino", "bologna",
    "firenze", "venezia", "genova", "palermo", "bari",
    "comune di", "provincia di", "regione ",
]
ITALY_RE = re.compile("|".join(ITALY_KEYWORDS), re.IGNORECASE)

HEADERS       = {"User-Agent": "RansomAlert-Monitor/1.0"}
BSKY_API      = "https://public.api.bsky.app/xrpc"
ECRIME_HANDLE = "ecrime.ch"

# ─── LOGGING ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ─── STATO PERSISTENTE ────────────────────────────────────────────────────────

def load_seen() -> set:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()

def save_seen(seen: set):
    SEEN_FILE.write_text(json.dumps(sorted(list(seen)), indent=2))

def make_id(source: str, uid: str) -> str:
    return hashlib.md5(f"{source}:{uid}".encode()).hexdigest()

# ─── TELEGRAM ─────────────────────────────────────────────────────────────────

def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=TIMEOUT)
        r.raise_for_status()
        log.info("✅ Telegram inviato")
    except Exception as e:
        log.error(f"Telegram error: {e}")

def format_alert(source: str, victim: str, group: str, date: str,
                 extra: str = "", url: str = "") -> str:
    lines = [
        "🚨 <b>NUOVA RIVENDICAZIONE — ITALIA</b>",
        f"🏴‍☠️ Gruppo: <b>{group}</b>",
        f"🏢 Vittima: <b>{victim}</b>",
    ]
    if extra:
        lines.append(extra)
    lines += [
        f"📅 Data: {date}",
        f"📡 Fonte: {source}",
    ]
    if url:
        lines.append(f"🔗 <a href='{url}'>Dettaglio</a>")
    return "\n".join(lines)

def send_ping(label: str):
    now_it = datetime.now(timezone(timedelta(hours=2)))
    seen   = load_seen()
    text = (
        f"{'🌅' if 'mattino' in label else '🌆'} <b>RansomAlert — {label}</b>\n"
        f"📡 Sistema operativo\n"
        f"🕐 {now_it.strftime('%d/%m/%Y %H:%M')} (ora italiana)\n"
        f"📋 Rivendicazioni monitorate: {len(seen)}"
    )
    send_telegram(text)

# ─── PARSER TESTO ecrime.ch ───────────────────────────────────────────────────

def parse_ecrime_post(text: str) -> dict:
    result = {"organization": "", "location": "", "industry": "",
              "staff": "", "group": "", "url": ""}
    m = re.search(r"group\s+#(\S+)", text, re.IGNORECASE)
    if m:
        result["group"] = m.group(1).rstrip(".")
    for field, key in [
        (r"Organization:\s*(.+)", "organization"),
        (r"Location:\s*(.+)",     "location"),
        (r"Industry:\s*(.+)",     "industry"),
        (r"Staff:\s*(.+)",        "staff"),
        (r"Learn more:\s*(https?://\S+)", "url"),
    ]:
        m = re.search(field, text, re.IGNORECASE)
        if m:
            result[key] = m.group(1).strip()
    return result

# ─── SORGENTE 1: ecrime.ch Bluesky ───────────────────────────────────────────

def fetch_ecrime_bsky(seen: set) -> list:
    new_items = []
    try:
        url    = f"{BSKY_API}/app.bsky.feed.getAuthorFeed"
        params = {"actor": ECRIME_HANDLE, "limit": 30, "filter": "posts_no_replies"}
        r      = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        for item in r.json().get("feed", []):
            if item.get("reason"):
                continue
            post   = item.get("post", {})
            record = post.get("record", {})
            text   = record.get("text", "")
            if not ITALY_RE.search(text):
                continue
            uri = post.get("uri", "")
            vid = make_id("ecrime_bsky", hashlib.md5(uri.encode()).hexdigest())
            if vid in seen:
                continue
            parsed     = parse_ecrime_post(text)
            victim     = parsed["organization"] or text[:60]
            group      = parsed["group"] or "ecrime.ch"
            date_raw   = record.get("createdAt", "")[:16].replace("T", " ")
            rkey       = uri.split("/")[-1] if uri else ""
            post_url   = (f"https://bsky.app/profile/{ECRIME_HANDLE}/post/{rkey}"
                          if rkey else "https://bsky.app/profile/ecrime.ch")
            detail_url = parsed["url"] or post_url
            extra_parts = []
            if parsed["location"]: extra_parts.append(f"📍 {parsed['location']}")
            if parsed["industry"]: extra_parts.append(f"🏭 {parsed['industry']}")
            if parsed["staff"]:    extra_parts.append(f"👥 {parsed['staff']}")
            seen.add(vid)
            new_items.append(format_alert("ecrime.ch (Bluesky)", victim, group,
                                          date_raw, "  ".join(extra_parts), detail_url))
        log.info(f"ecrime.ch bsky: {len(new_items)} nuovi")
    except Exception as e:
        log.error(f"ecrime.ch bsky error: {e}")
    return new_items

# ─── SORGENTE 2: ransom-db.com ───────────────────────────────────────────────

def fetch_ransomdb(seen: set) -> list:
    new_items = []
    try:
        url = "https://www.ransom-db.com/live-updates"
        r   = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for row in soup.select("table tbody tr, .victim-row, .entry"):
            text = row.get_text(" ", strip=True)
            if not ITALY_RE.search(text):
                continue
            uid  = hashlib.md5(text[:120].encode()).hexdigest()
            vid  = make_id("ransomdb", uid)
            if vid in seen:
                continue
            cells  = row.find_all(["td", "div"])
            victim = cells[0].get_text(strip=True) if cells else text[:60]
            group  = cells[1].get_text(strip=True) if len(cells) > 1 else "N/A"
            date   = cells[2].get_text(strip=True) if len(cells) > 2 else "N/A"
            seen.add(vid)
            new_items.append(format_alert("ransom-db.com", victim, group, date,
                                          url="https://www.ransom-db.com/live-updates"))
        log.info(f"ransom-db.com: {len(new_items)} nuovi")
    except Exception as e:
        log.error(f"ransom-db error: {e}")
    return new_items

# ─── SORGENTE 3: ransomware.live (API Italia) ────────────────────────────────

def fetch_ransomwarelive(seen: set) -> list:
    new_items = []
    try:
        # Endpoint recenti vittime, filtriamo per country IT/ITA/Italy
        url = "https://api.ransomware.live/v2/recentvictims"
        r   = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        victims = r.json() if isinstance(r.json(), list) else r.json().get("victims", [])
        for item in victims:
            country = item.get("country", "").lower().strip()
            if country not in ("it", "ita", "italy", "italia"):
                continue
            uid = str(item.get("id", item.get("post_id",
                      hashlib.md5(str(item).encode()).hexdigest())))
            vid = make_id("ransomwarelive", uid)
            if vid in seen:
                continue
            victim = item.get("victim", item.get("post_title", "N/A"))
            group  = item.get("group",  item.get("gang", "N/A"))
            date   = (item.get("discoverdate") or item.get("discovered") or item.get("published") or item.get("date") or "N/A")[:19]
            link   = item.get("url", "https://www.ransomware.live/")
            seen.add(vid)
            new_items.append(format_alert("ransomware.live", victim, group, date, url=link))
        log.info(f"ransomware.live: {len(new_items)} nuovi")
    except Exception as e:
        log.error(f"ransomware.live error: {e}")
    return new_items

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    # Se è un ping di stato, invia e basta
    if CURRENT_SCHEDULE in PING_SCHEDULES:
        label = "Ping mattutino" if CURRENT_SCHEDULE == "30 5 * * *" else "Ping serale"
        log.info(f"=== {label} ===")
        send_ping(label)
        return

    # Altrimenti check normale
    log.info("=== Avvio check ===")
    seen    = load_seen()
    all_new = []

    all_new += fetch_ecrime_bsky(seen)
    all_new += fetch_ransomdb(seen)
    all_new += fetch_ransomwarelive(seen)

    save_seen(seen)

    if all_new:
        log.info(f"🚨 {len(all_new)} nuove rivendicazioni italiane trovate!")
        for msg in all_new:
            send_telegram(msg)
            time.sleep(1)
    else:
        log.info("Nessuna novità italiana.")

    log.info("=== Check completato ===")

if __name__ == "__main__":
    main()
