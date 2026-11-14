"""
Combine demand and supply into one gap table, ranked by demand relative
to supply.

Input:
    data/processed/demand_history_by_programme.csv  (src/demand_history.py)
    data/processed/supply_by_programme.csv          (src/build_supply.py)
    data/processed/nav_health.csv                   (src/filter_health.py)
Output:
    data/processed/gap_by_programme.csv             one row per programme

Columns:
    postings             estimated job postings, last 12 months
    study_places         study places in Samordna opptak 2026
    postings_per_place   postings / study_places   (main gap measure)
    first_choice_per_place
                         first-choice applicants per study place
                         (can more places actually be filled?)
    share_fulltime       share of current postings that are full-time
    share_temporary      share of current postings that look temporary
                         (vikariat, ekstravakt, summer job, etc.)
    n_snapshot           current postings the two shares are based on

Run from the repo root:
    python src/build_gap.py
"""

import os

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROCESSED = os.path.join(ROOT, "data", "processed")
DEMAND_PATH = os.path.join(PROCESSED, "demand_history_by_programme.csv")
SUPPLY_PATH = os.path.join(PROCESSED, "supply_by_programme.csv")
HEALTH_PATH = os.path.join(PROCESSED, "nav_health.csv")
GAP_PATH = os.path.join(PROCESSED, "gap_by_programme.csv")

TEMPORARY = r"vikar|ekstravakt|tilkalling|sommer|ferie|helg"


def posting_profile():
    """Share of full-time and temporary postings per programme (current snapshot)."""
    health = pd.read_csv(HEALTH_PATH, usecols=["title", "extent", "programme"])
    health["fulltime"] = health["extent"].eq("Heltid")
    health["temporary"] = health["title"].str.contains(TEMPORARY, case=False, na=False)
    return (
        health.groupby("programme")
        .agg(
            share_fulltime=("fulltime", "mean"),
            share_temporary=("temporary", "mean"),
            n_snapshot=("title", "count"),
        )
        .round(2)
        .reset_index()
    )


def main():
    demand = pd.read_csv(DEMAND_PATH)[["programme", "postings_estimated"]]
    demand = demand.rename(columns={"postings_estimated": "postings"})
    supply = pd.read_csv(SUPPLY_PATH)[["programme", "study_places", "first_choice_per_place"]]

    # Inner join: programmes without study places in Samordna opptak drop out.
    gap = demand.merge(supply, on="programme", how="inner")
    gap = gap.merge(posting_profile(), on="programme", how="left")

    gap["postings"] = gap["postings"].astype(int)
    gap["postings_per_place"] = (gap["postings"] / gap["study_places"]).round(2)
    gap = gap.sort_values("postings_per_place", ascending=False).reset_index(drop=True)
    gap.insert(0, "rank", gap.index + 1)

    gap = gap[[
        "rank", "programme", "postings", "study_places", "postings_per_place",
        "first_choice_per_place", "share_fulltime", "share_temporary", "n_snapshot",
    ]]
    gap.to_csv(GAP_PATH, index=False)

    dropped = sorted(set(demand["programme"]) - set(gap["programme"]))
    print(gap.to_string(index=False))
    if dropped:
        print(f"\nNo study places in Samordna opptak (dropped): {', '.join(dropped)}")
    print(f"\nSaved {GAP_PATH}")


if __name__ == "__main__":
    main()
