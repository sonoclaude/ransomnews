#!/usr/bin/env python3
"""
ransom_alert.py — Monitor rivendicazioni ransomware Italia
Versione GitHub Actions: gira sui server GitHub ogni 30 minuti.

Sorgenti:
  - bsky.app/profile/ecrime.ch (API Bluesky diretta)
  - ransom-db.com/live-updates (scraping)
  - ransomlook.io/recent (scraping)
"""

import json
import os
import time
import hashlib
import logging
import re
from pathlib import Path
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

# ─── CONFIGURAZIONE ───────────────────────────────────────────────────────────

TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Il file seen.json è nella stessa cartella dello script (nel repo)
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

HEADERS      = {"User-Agent": "RansomAlert-Monitor/1.0"}
BSKY_API     = "https://public.api.bsky.app/xrpc"
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
        url = f"{BSKY_API}/app.bsky.feed.getAuthorFeed"
        params = {"actor": ECRIME_HANDLE, "limit": 30, "filter": "posts_no_replies"}
        r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
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
            parsed   = parse_ecrime_post(text)
            victim   = parsed["organization"] or text[:60]
            group    = parsed["group"] or "ecrime.ch"
            date_raw = record.get("createdAt", "")[:16].replace("T", " ")
            rkey     = uri.split("/")[-1] if uri else ""
            post_url = (f"https://bsky.app/profile/{ECRIME_HANDLE}/post/{rkey}"
                        if rkey else "https://bsky.app/profile/ecrime.ch")
            detail_url = parsed["url"] or post_url
            extra_parts = []
            if parsed["location"]:  extra_parts.append(f"📍 {parsed['location']}")
            if parsed["industry"]:  extra_parts.append(f"🏭 {parsed['industry']}")
            if parsed["staff"]:     extra_parts.append(f"👥 {parsed['staff']}")
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
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for row in soup.select("table tbody tr, .victim-row, .entry"):
            text = row.get_text(" ", strip=True)
            if not ITALY_RE.search(text):
                continue
            uid = hashlib.md5(text[:120].encode()).hexdigest()
            vid = make_id("ransomdb", uid)
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

# ─── SORGENTE 3: ransomlook.io ───────────────────────────────────────────────

def fetch_ransomlook(seen: set) -> list:
    new_items = []
    try:
        url = "https://www.ransomlook.io/recent"
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for row in soup.select("table tbody tr, .victim-row, .card, .entry"):
            text = row.get_text(" ", strip=True)
            if not ITALY_RE.search(text):
                continue
            uid = hashlib.md5(text[:120].encode()).hexdigest()
            vid = make_id("ransomlook", uid)
            if vid in seen:
                continue
            cells    = row.find_all(["td", "div"])
            victim   = cells[0].get_text(strip=True) if cells else text[:60]
            group    = cells[1].get_text(strip=True) if len(cells) > 1 else "N/A"
            date     = cells[2].get_text(strip=True) if len(cells) > 2 else "N/A"
            link_tag = row.find("a", href=True)
            link = ("https://www.ransomlook.io" + link_tag["href"]
                    if link_tag and link_tag["href"].startswith("/")
                    else link_tag["href"] if link_tag
                    else "https://www.ransomlook.io/recent")
            seen.add(vid)
            new_items.append(format_alert("ransomlook.io", victim, group, date, url=link))
        log.info(f"ransomlook.io: {len(new_items)} nuovi")
    except Exception as e:
        log.error(f"ransomlook.io error: {e}")
    return new_items

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    log.info("=== Avvio check ===")
    seen    = load_seen()
    all_new = []

    all_new += fetch_ecrime_bsky(seen)
    all_new += fetch_ransomdb(seen)
    all_new += fetch_ransomlook(seen)

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
