"""
Fetch job postings from the NAV job vacancy feed (pam-stilling-feed).

Two modes:

    python src/fetch_nav.py
        All active postings from the last DAYS days, with full ad content
        (description, occupation category, etc.).
        Output: data/raw/nav_all.csv

    python src/fetch_nav.py --history
        Every posting seen in the feed during the last HISTORY_DAYS days,
        including postings that have since expired. NAV removes the ad content
        once a posting is inactive, so this mode only keeps what the feed list
        itself contains: title, employer, municipality, status and dates.
        Output: data/raw/nav_history.csv

Run from the repo root.
"""

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

# --- Settings ---------------------------------------------------------------

BASE = "https://pam-stilling-feed.nav.no"
DAYS = 60            # snapshot mode: postings published or updated in the last N days
HISTORY_DAYS = 365   # history mode: how far back to read the feed
MAX_WORKERS = 4      # parallel requests (lower to 2 if you get 429 errors)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
os.makedirs(RAW, exist_ok=True)
ALL_PATH = os.path.join(RAW, "nav_all.csv")
HISTORY_PATH = os.path.join(RAW, "nav_history.csv")


def get_headers():
    """Get the public token and build request headers."""
    tok = requests.get(BASE + "/api/publicToken", timeout=15).text.strip().split()[-1]
    return {"Authorization": f"Bearer {tok}", "Accept": "application/json"}


def get_json(url, headers, retries=5):
    """GET with simple retry/backoff (handles 429 and network hiccups)."""
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=headers, timeout=60)
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"HTTP {r.status_code}")
            return r.json()
        except Exception as e:
            wait = 2 ** attempt
            print(f"  retry {attempt + 1}/{retries} in {wait}s ({e})", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"Giving up on {url}")


def read_feed(headers, days):
    """Page through the feed from `days` ago until the end.

    Returns one record per posting (uuid) with the latest status, plus the
    first and last time the posting appeared in the feed. Only lightweight
    fields are kept, so a full year fits in memory.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%a, %d %b %Y %H:%M:%S GMT"
    )
    entries, url, i = {}, BASE + "/api/v1/feed", 0
    while url:
        hdr = dict(headers, **({"If-Modified-Since": since} if i == 0 else {}))
        page = get_json(url, hdr)
        for it in page["items"]:
            fe = it["_feed_entry"]
            uuid = fe["uuid"]
            modified = it.get("date_modified") or fe.get("sistEndret")
            rec = entries.get(uuid)
            if rec is None:
                rec = entries[uuid] = {"uuid": uuid, "first_seen": modified}
            rec.update(  # latest version wins
                url=it["url"],
                status=fe.get("status"),
                title=fe.get("title") or it.get("title"),
                employer=fe.get("businessName"),
                municipal=fe.get("municipal"),
                last_seen=modified,
            )
        i += 1
        if i % 10 == 0:
            print(f"page {i}, unique postings: {len(entries)}, at {modified[:10]}", flush=True)
        if page.get("next_url") and page.get("next_id"):
            url = BASE + page["next_url"]
        else:
            url = None
    print(f"Feed done: {i} pages, {len(entries)} unique postings", flush=True)
    return entries


def fetch_posting(entry, headers):
    """Fetch one posting and return the fields we need."""
    try:
        ad = requests.get(BASE + entry["url"], headers=headers, timeout=20).json()
    except Exception:
        return None
    ad = ad.get("ad_content", ad)
    occ = (ad.get("occupationCategories") or [{}])[0]
    loc = (ad.get("workLocations") or [{}])[0]
    return {
        "id": entry["uuid"],
        "title": ad.get("title"),
        "jobtitle": ad.get("jobtitle"),
        "employer": (ad.get("employer") or {}).get("name"),
        "county": loc.get("county"),
        "municipal": loc.get("municipal"),
        "level1": occ.get("level1"),
        "level2": occ.get("level2"),
        "published": ad.get("published"),
        "expires": ad.get("expires"),
        "extent": ad.get("extent"),
        "sector": ad.get("sector"),
        "description": ad.get("description"),
    }


def fetch_all(active, headers):
    """Fetch details for all active postings in parallel, saving progress."""
    rows = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [ex.submit(fetch_posting, e, headers) for e in active]
        for n, f in enumerate(as_completed(futures), 1):
            r = f.result()
            if r:
                rows.append(r)
            if n % 200 == 0:
                print(f"fetched {n}/{len(active)}", flush=True)
                pd.DataFrame(rows).to_csv(ALL_PATH, index=False)  # save progress
    df = pd.DataFrame(rows).drop_duplicates("id")
    df["fetched_at"] = datetime.now().date().isoformat()
    df.to_csv(ALL_PATH, index=False)
    return df


def main_snapshot():
    headers = get_headers()
    entries = read_feed(headers, DAYS)
    active = [e for e in entries.values() if e["status"] == "ACTIVE"]
    print("Active postings:", len(active), flush=True)

    df = fetch_all(active, headers)
    print(f"\nSaved {len(df)} postings to {ALL_PATH}")


def main_history():
    headers = get_headers()
    entries = read_feed(headers, HISTORY_DAYS)
    df = pd.DataFrame(entries.values()).drop(columns="url")
    df["fetched_at"] = datetime.now().date().isoformat()
    df = df[["uuid", "title", "employer", "municipal", "status",
             "first_seen", "last_seen", "fetched_at"]]
    df.to_csv(HISTORY_PATH, index=False)
    print(f"\nSaved {len(df)} postings to {HISTORY_PATH}")
    print(df["status"].value_counts().to_string())


if __name__ == "__main__":
    if "--history" in sys.argv:
        main_history()
    else:
        main_snapshot()
