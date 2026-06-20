#!/usr/bin/env python3
"""
GD Top 1 Race Tracker — Wiki Scraper
Scrapes the Geometry Dash Fandom Wiki and GD Colon Wiki for verifier
progress on GRIEF, Heliopolis, Sweeping Demon II, and Society.
Merges with existing data.json to preserve historical milestones.
"""

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────

LEVELS = [
    "GRIEF",
    "Heliopolis",
    "Sweeping Demon II",
    "Society",
]

FANDOM_BASE = "https://geometry-dash.fandom.com/wiki/"
COLON_BASE  = "https://gdcolon.com/gdwiki/article/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; GDTop1Tracker/1.0; "
        "+https://github.com/arhamakhtaralam/gd-top1-tracker)"
    )
}

DATA_PATH = Path(__file__).parent.parent / "data.json"

# Patterns to detect progress percentages in wiki text
PCT_PATTERNS = [
    # "best: 74%" / "best run: 74%" / "current best: 74%"
    re.compile(r"(?:current\s+)?best(?:\s+run)?[:\s]+(\d{1,3})\s*%", re.I),
    # "reached 74%" / "reached the 74%"
    re.compile(r"reached(?:\s+the)?\s+(\d{1,3})\s*%", re.I),
    # "74% (latest)" / plain "74%" in progress tables
    re.compile(r"\b(\d{1,3})\s*%\s*(?:best|pb|record|run|coin)", re.I),
    # fallback: any standalone percentage
    re.compile(r"\b(\d{1,3})\s*%"),
]

ATTEMPT_PATTERNS = [
    re.compile(r"(\d[\d,]+)\s*(?:total\s+)?attempts?", re.I),
    re.compile(r"attempts?[:\s]+(\d[\d,]+)", re.I),
]

VERIFIER_PATTERNS = [
    re.compile(r"(?:verif(?:ied?\s+by|ier)|creator)[:\s]+([A-Za-z0-9_ ]+)", re.I),
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def fetch(url: str) -> BeautifulSoup | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        print(f"  [warn] fetch failed for {url}: {e}", file=sys.stderr)
        return None


def first_match(patterns: list, text: str) -> str | None:
    for pat in patterns:
        m = pat.search(text)
        if m:
            return m.group(1).replace(",", "").strip()
    return None


def extract_percentages(soup: BeautifulSoup) -> list[int]:
    """Return all unique percentages found on the page, sorted descending."""
    text = soup.get_text(" ", strip=True)
    found = set()
    for pat in PCT_PATTERNS:
        for m in pat.finditer(text):
            v = int(m.group(1))
            if 1 <= v <= 100:
                found.add(v)
    return sorted(found, reverse=True)


def extract_best_pct(soup: BeautifulSoup) -> int | None:
    text = soup.get_text(" ", strip=True)
    # Try targeted patterns first
    for pat in PCT_PATTERNS[:-1]:
        m = pat.search(text)
        if m:
            v = int(m.group(1))
            if 1 <= v <= 100:
                return v
    # Fallback: highest percentage on the page that's < 100
    all_pcts = extract_percentages(soup)
    for pct in all_pcts:
        if pct < 100:
            return pct
    return None


def extract_attempts(soup: BeautifulSoup) -> int | None:
    text = soup.get_text(" ", strip=True)
    raw = first_match(ATTEMPT_PATTERNS, text)
    if raw:
        try:
            return int(raw.replace(",", ""))
        except ValueError:
            pass
    return None


def extract_verifier(soup: BeautifulSoup) -> str | None:
    text = soup.get_text(" ", strip=True)
    raw = first_match(VERIFIER_PATTERNS, text)
    if raw:
        # Trim trailing noise
        return raw.strip().split("\n")[0][:60]
    # Try infobox rows
    for row in soup.select("table.article-table tr, aside.portable-infobox section"):
        cells = row.select("td, div.pi-data-value")
        if len(cells) >= 2:
            label = cells[0].get_text(strip=True).lower()
            if any(k in label for k in ("verif", "creator", "player")):
                return cells[1].get_text(strip=True)[:60]
    return None


def scrape_level(name: str) -> dict:
    """Scrape fandom wiki (primary) then colon wiki (secondary) for a level."""
    slug = name.replace(" ", "_")
    print(f"Scraping: {name}")

    result = {
        "level_name": name,
        "verifier": None,
        "current_best_pct": None,
        "attempt_count": None,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "_sources": [],
    }

    # 1) Fandom wiki
    fandom_url = FANDOM_BASE + slug
    soup = fetch(fandom_url)
    if soup:
        result["_sources"].append(fandom_url)
        if result["current_best_pct"] is None:
            result["current_best_pct"] = extract_best_pct(soup)
        if result["attempt_count"] is None:
            result["attempt_count"] = extract_attempts(soup)
        if result["verifier"] is None:
            result["verifier"] = extract_verifier(soup)
        print(f"  Fandom → pct={result['current_best_pct']} attempts={result['attempt_count']}")

    time.sleep(1)  # polite delay

    # 2) GD Colon Wiki (secondary)
    colon_url = COLON_BASE + slug
    soup2 = fetch(colon_url)
    if soup2:
        result["_sources"].append(colon_url)
        if result["current_best_pct"] is None:
            result["current_best_pct"] = extract_best_pct(soup2)
        if result["attempt_count"] is None:
            result["attempt_count"] = extract_attempts(soup2)
        if result["verifier"] is None:
            result["verifier"] = extract_verifier(soup2)
        print(f"  Colon  → pct={result['current_best_pct']} attempts={result['attempt_count']}")

    time.sleep(1)

    return result


# ── Merge with existing data ──────────────────────────────────────────────────

def load_existing() -> dict:
    if DATA_PATH.exists():
        try:
            return json.loads(DATA_PATH.read_text())
        except Exception:
            pass
    return {"last_fetched": None, "levels": []}


def merge(existing: dict, fresh: dict) -> dict:
    """Merge fresh scrape results into existing data, preserving milestone history."""
    existing_map: dict[str, dict] = {
        lvl["level_name"]: lvl for lvl in existing.get("levels", [])
    }

    levels_out = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for item in fresh:
        name = item["level_name"]
        prev = existing_map.get(name, {
            "level_name": name,
            "verifier": None,
            "current_best_pct": 0,
            "attempt_count": None,
            "last_updated": None,
            "milestones": [],
        })

        milestones: list[dict] = prev.get("milestones", [])
        new_pct = item.get("current_best_pct")
        prev_pct = prev.get("current_best_pct") or 0

        # Record a milestone if we have a new best % (or first reading)
        if new_pct is not None and new_pct > prev_pct:
            # Avoid duplicate date entries
            existing_dates = {m["date"] for m in milestones}
            if today not in existing_dates:
                milestones.append({"pct": new_pct, "date": today})
            else:
                # Update today's entry if it improved
                for m in milestones:
                    if m["date"] == today and new_pct > m.get("pct", 0):
                        m["pct"] = new_pct

        merged_entry = {
            "level_name": name,
            "verifier": item.get("verifier") or prev.get("verifier"),
            "current_best_pct": new_pct if new_pct is not None else prev_pct,
            "attempt_count": item.get("attempt_count") or prev.get("attempt_count"),
            "last_updated": item.get("last_updated") or prev.get("last_updated"),
            "milestones": sorted(milestones, key=lambda m: m["date"]),
        }
        levels_out.append(merged_entry)

    return {
        "last_fetched": datetime.now(timezone.utc).isoformat(),
        "levels": levels_out,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=== GD Top 1 Race Tracker — Scraper ===")
    existing = load_existing()

    fresh_results = []
    for level in LEVELS:
        try:
            fresh_results.append(scrape_level(level))
        except Exception as e:
            print(f"  [error] {level}: {e}", file=sys.stderr)
            # Keep existing data for this level
            for lvl in existing.get("levels", []):
                if lvl["level_name"] == level:
                    fresh_results.append(lvl)
                    break

    output = merge(existing, fresh_results)

    DATA_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"\n✓ Wrote {DATA_PATH}")
    for lvl in output["levels"]:
        print(f"  {lvl['level_name']}: {lvl['current_best_pct']}% | "
              f"verifier={lvl['verifier']} | attempts={lvl['attempt_count']}")


if __name__ == "__main__":
    main()
