"""
Evaluate the programme ranking: does ranking by postings per study place
(method) give a different and stable answer than ranking by the number of
postings (baseline)?

There is no true ranking to score against, so the script checks:
1. Decision change: baseline vs. method (Spearman, top 3 in common).
2. Robustness: the same rankings with other definitions of demand
   (see VARIANT_TEXT).

Input:
    data/processed/gap_by_programme.csv               (src/build_gap.py)
    data/processed/demand_history_by_programme.csv    (src/demand_history.py)
Output:
    data/eval/ranking_ranks.csv    one row per programme (rank 1 = highest priority)
    data/eval/ranking_scores.csv   one row per comparison

Run from the repo root:
    python src/evaluate_ranking.py
"""

import os

import pandas as pd
from scipy.stats import spearmanr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROCESSED = os.path.join(ROOT, "data", "processed")
EVAL = os.path.join(ROOT, "data", "eval")
GAP_PATH = os.path.join(PROCESSED, "gap_by_programme.csv")
DEMAND_PATH = os.path.join(PROCESSED, "demand_history_by_programme.csv")
RANKS_PATH = os.path.join(EVAL, "ranking_ranks.csv")
SCORES_PATH = os.path.join(EVAL, "ranking_scores.csv")

TOP_K = 3


def demand_variants():
    gap = pd.read_csv(GAP_PATH)
    hist = pd.read_csv(DEMAND_PATH)[["programme", "postings_title", "snapshot_level2"]]
    df = gap.merge(hist, on="programme", how="left")
    variants = pd.DataFrame({
        "main": df["postings"],
        "title_only": df["postings_title"],
        "snapshot": df["snapshot_level2"],
        "permanent": df["postings"] * (1 - df["share_temporary"]),
        "fulltime": df["postings"] * df["share_fulltime"],
    })
    variants.index = df["programme"]
    places = df.set_index("programme")["study_places"]
    return variants, places


def rank(values):
    # Rank 1 = highest priority. Ties share the best rank.
    return values.rank(ascending=False, method="min").astype(int)


def top(ranks, k=TOP_K):
    return set(ranks[ranks <= k].index)


VARIANT_TEXT = {
    "title_only": "title rules only, no correction",
    "snapshot": "current postings only",
    "permanent": "temporary postings removed",
    "fulltime": "full-time postings only",
}


def main():
    variants, places = demand_variants()
    base = {v: rank(variants[v]) for v in variants}
    meth = {v: rank(variants[v] / places) for v in variants}

    rows = []

    def compare(question, description, a, b):
        ta, tb = top(a), top(b)
        rows.append({
            "question": question,
            "description": description,
            "spearman": round(spearmanr(a, b).statistic, 3),
            f"top{TOP_K}_same": len(ta & tb),
            f"top{TOP_K}_first": ", ".join(sorted(ta, key=a.get)),
            f"top{TOP_K}_second": ", ".join(sorted(tb, key=b.get)),
            "largest_rank_change": int((a - b).abs().max()),
        })

    # 1. Does the method change the decision?
    compare("decision_change",
            "rank by number of postings (baseline) vs. by postings per study place (method)",
            base["main"], meth["main"])
    # 2. Robustness, for both.
    for v, text in VARIANT_TEXT.items():
        compare("robustness_method", f"method: main demand vs. {text}", meth["main"], meth[v])
    for v, text in VARIANT_TEXT.items():
        compare("robustness_baseline", f"baseline: main demand vs. {text}", base["main"], base[v])

    ranks = pd.DataFrame({
        "postings": variants["main"].astype(int),
        "study_places": places,
        "postings_per_place": (variants["main"] / places).round(2),
        "rank_baseline": base["main"],
        "rank_method": meth["main"],
    })
    ranks["rank_change"] = ranks["rank_baseline"] - ranks["rank_method"]
    for v in VARIANT_TEXT:
        ranks[f"rank_method_{v}"] = meth[v]
    ranks = ranks.sort_values("rank_method")

    scores = pd.DataFrame(rows)
    os.makedirs(EVAL, exist_ok=True)
    ranks.to_csv(RANKS_PATH)
    scores.to_csv(SCORES_PATH, index=False)

    pd.set_option("display.width", 200)
    print(ranks.iloc[:, :6].to_string())
    print()
    print(scores[["description", "spearman", f"top{TOP_K}_same",
                  f"top{TOP_K}_second"]].to_string(index=False))
    print(f"\nSaved {RANKS_PATH}\nSaved {SCORES_PATH}")


if __name__ == "__main__":
    main()
