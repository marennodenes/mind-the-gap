"""
Fetch all active job postings from the NAV job vacancy feed (pam-stilling-feed).

Output:
    data/raw/nav_all.csv

Run from the repo root:
    python src/fetch_nav.py
"""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

# --- Settings ---------------------------------------------------------------

BASE = "https://pam-stilling-feed.nav.no"
DAYS = 60          # include postings published or updated in the last N days
MAX_WORKERS = 4    # parallel requests (lower to 2 if you get 429 errors)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
os.makedirs(RAW, exist_ok=True)
ALL_PATH = os.path.join(RAW, "nav_all.csv")


def get_headers():
    """Get the public token and build request headers."""
    tok = requests.get(BASE + "/api/publicToken", timeout=15).text.strip().split()[-1]
    return {"Authorization": f"Bearer {tok}", "Accept": "application/json"}


def read_feed(headers):
    """Page through the feed from DAYS ago until the end.
    Returns the latest version of each posting."""
    since = (datetime.now(timezone.utc) - timedelta(days=DAYS)).strftime(
        "%a, %d %b %Y %H:%M:%S GMT"
    )
    entries, url, i = {}, BASE + "/api/v1/feed", 0
    while url:
        hdr = dict(headers, **({"If-Modified-Since": since} if i == 0 else {}))
        page = requests.get(url, headers=hdr, timeout=30).json()
        for it in page["items"]:
            entries[it["_feed_entry"]["uuid"]] = it  # latest version wins
        i += 1
        if i % 10 == 0:
            print(f"page {i}, unique postings: {len(entries)}", flush=True)
        if page.get("next_url") and page.get("next_id"):
            url = BASE + page["next_url"]
        else:
            url = None
    print(f"Feed done: {i} pages, {len(entries)} unique postings", flush=True)
    return entries


def fetch_posting(item, headers):
    """Fetch one posting and return the fields we need."""
    try:
        ad = requests.get(BASE + item["url"], headers=headers, timeout=20).json()
    except Exception:
        return None
    ad = ad.get("ad_content", ad)
    occ = (ad.get("occupationCategories") or [{}])[0]
    loc = (ad.get("workLocations") or [{}])[0]
    return {
        "id": item["id"],
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
        futures = [ex.submit(fetch_posting, it, headers) for it in active]
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


def main():
    headers = get_headers()
    entries = read_feed(headers)
    active = [e for e in entries.values() if e["_feed_entry"]["status"] == "ACTIVE"]
    print("Active postings:", len(active), flush=True)

    df = fetch_all(active, headers)
    print(f"\nSaved {len(df)} postings to {ALL_PATH}")


if __name__ == "__main__":
    main()