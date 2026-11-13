"""
Filter healthcare postings from the raw NAV data and assign occupational groups.

Input:
    data/raw/nav_all.csv            (created by src/fetch_nav.py)
Output:
    data/processed/nav_health.csv

Run from the repo root:
    python src/filter_health.py
"""

import os
import re

import pandas as pd

# --- Settings ---------------------------------------------------------------

MIN_DESC_LEN = 200  # drop postings with very short descriptions

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALL_PATH = os.path.join(ROOT, "data", "raw", "nav_all.csv")
PROCESSED = os.path.join(ROOT, "data", "processed")
os.makedirs(PROCESSED, exist_ok=True)
HEALTH_PATH = os.path.join(PROCESSED, "nav_health.csv")

# Norwegian title keywords used to catch uncategorized healthcare postings
HEALTH_TERMS = (
    r"sykepleier|helsefag|lege\b|legespesialist|overlege|helsesekret|vernepleier|"
    r"jordmor|fysioterapeut|ergoterapeut|psykolog|bioingeniør|miljøterapeut|"
    r"omsorg|pleie|personlig assistent|hjemmehjelp|tannlege|tannpleier|"
    r"radiograf|farmasøyt"
)


def assign_group(row):
    """Map a posting to an occupational group."""
    t = f"{row['level2'] if pd.notna(row['level2']) else ''} {row['title'] if pd.notna(row['title']) else ''}".lower()
    if "sykepleier" in t:
        return "Nurse"
    if "helsefag" in t:
        return "Healthcare worker"
    if re.search(r"\blege|overlege|legespesialist", t):
        return "Physician"
    if "hjemmehjelp" in t or "personlig assistent" in t:
        return "Home helper/assistant"
    return "Other healthcare"


def filter_health(df):
    """Keep healthcare postings: NAV category, or uncategorized with a health title."""
    is_health = (df["level1"] == "Helse og sosial") | (
        df["level1"].isna()
        & df["title"].fillna("").str.contains(HEALTH_TERMS, case=False, regex=True)
    )
    health = df[is_health].copy()
    health["group"] = health.apply(assign_group, axis=1)
    health["desc_len"] = health["description"].fillna("").str.len()
    return health[health["desc_len"] >= MIN_DESC_LEN]


def main():
    df = pd.read_csv(ALL_PATH)
    health = filter_health(df)
    health.to_csv(HEALTH_PATH, index=False)

    print(f"All postings: {len(df)}")
    print(f"Healthcare postings: {len(health)} ({len(health) / len(df):.0%})")
    print(f"Saved to {HEALTH_PATH}\n")
    print(health["group"].value_counts())
    print("\nMedian description length per group:")
    print(health.groupby("group")["desc_len"].median().sort_values(ascending=False))


if __name__ == "__main__":
    main()