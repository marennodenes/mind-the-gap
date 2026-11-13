"""
Select healthcare postings that require higher education, clean the text,
and link each occupation to its study programme.

Input:
    data/raw/nav_all.csv                       (created by src/fetch_nav.py)
Output:
    data/processed/nav_health.csv              one row per posting
    data/processed/demand_by_programme.csv     postings per study programme

Run from the repo root:
    python src/filter_health.py
"""

import html
import os
import re

import pandas as pd

# --- Settings ---------------------------------------------------------------

MIN_DESC_LEN = 200  # drop postings with very short descriptions (after cleaning)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALL_PATH = os.path.join(ROOT, "data", "raw", "nav_all.csv")
PROCESSED = os.path.join(ROOT, "data", "processed")
os.makedirs(PROCESSED, exist_ok=True)
HEALTH_PATH = os.path.join(PROCESSED, "nav_health.csv")
DEMAND_PATH = os.path.join(PROCESSED, "demand_by_programme.csv")

# NAV occupation (level2) -> study programme in higher education.
# Only occupations that require a higher education degree are included.
OCCUPATION_TO_PROGRAMME = {
    "Sykepleier": "Sykepleie",
    "Lege": "Medisin",
    "Vernepleier": "Vernepleie",
    "Psykolog": "Psykologi (profesjonsstudium)",
    "Fysioterapeut": "Fysioterapi",
    "Bioingeniør": "Bioingeniør",
    "Ergoterapeut": "Ergoterapi",
    "Radiograf og audiograf": "Radiografi / audiografi",
    "Audiografer og logopeder": "Audiografi / logopedi",
    "Ernæringsfysiolog": "Klinisk ernæring",
}

# "Tannhelse/-pleie" mixes upper secondary and higher education,
# so it is split by job title.
DENTAL_TITLES = {
    r"tannlege": "Odontologi",
    r"tannpleier": "Tannpleie",
}


# --- Text cleaning ------------------------------------------------------------

def clean_text(raw):
    """Convert HTML description to plain text."""
    if not isinstance(raw, str):
        return ""
    t = re.sub(r"<\s*(br|/p|/li|/h\d)\s*/?>", "\n", raw, flags=re.I)  # keep line breaks
    t = re.sub(r"<li[^>]*>", "\n- ", t, flags=re.I)                   # keep bullet points
    t = re.sub(r"<[^>]+>", " ", t)                                    # remove other tags
    t = html.unescape(t)                                              # &amp; -> & etc.
    t = t.replace(" ", "\n").replace(" ", "\n").replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\s*\n\s*", "\n", t)
    return t.strip()


# --- Programme mapping ----------------------------------------------------------

def assign_programme(row):
    """Return the study programme for a posting, or None if not higher education."""
    occ = row["level2"]
    if occ in OCCUPATION_TO_PROGRAMME:
        return OCCUPATION_TO_PROGRAMME[occ]
    if occ == "Tannhelse/-pleie":
        title = f"{row['title']} {row['jobtitle']}".lower()
        for pattern, programme in DENTAL_TITLES.items():
            if re.search(pattern, title):
                return programme
    return None


# --- Main -------------------------------------------------------------------------

def main():
    df = pd.read_csv(ALL_PATH)
    n_all = len(df)

    health = df[df["level1"] == "Helse og sosial"].copy()
    n_health = len(health)

    health["programme"] = health.apply(assign_programme, axis=1)
    health = health[health["programme"].notna()].copy()
    n_higher_ed = len(health)

    health["description_clean"] = health["description"].apply(clean_text)
    health["desc_len"] = health["description_clean"].str.len()
    health = health[health["desc_len"] >= MIN_DESC_LEN]

    # Remove near-duplicates: same employer, title and text
    health = health.drop_duplicates(subset=["employer", "title", "description_clean"])

    cols = [
        "id", "title", "jobtitle", "employer", "county", "municipal",
        "level1", "level2", "programme", "published", "expires",
        "extent", "sector", "description_clean", "desc_len", "fetched_at",
    ]
    health[cols].to_csv(HEALTH_PATH, index=False)

    demand = (
        health.groupby("programme")
        .agg(postings=("id", "count"), median_desc_len=("desc_len", "median"))
        .sort_values("postings", ascending=False)
        .reset_index()
    )
    demand["share_of_health_postings"] = (demand["postings"] / len(health)).round(3)
    demand.to_csv(DEMAND_PATH, index=False)

    print(f"All postings:                 {n_all}")
    print(f"Healthcare postings:          {n_health} ({n_health / n_all:.0%})")
    print(f"Requiring higher education:   {n_higher_ed}")
    print(f"After cleaning/deduplication: {len(health)}")
    print(f"\nSaved {HEALTH_PATH}")
    print(f"Saved {DEMAND_PATH}\n")
    print(demand.to_string(index=False))


if __name__ == "__main__":
    main()