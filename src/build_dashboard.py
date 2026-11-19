"""
Build an interactive dashboard of the results as one HTML file.

The page needs no server and no internet: open dashboard/index.html in a
browser. It has a Norwegian / English switch (translations in
src/dashboard_translations.py) and a light / dark switch.

Input:
    data/processed/gap_by_programme.csv              (src/build_gap.py)
    data/processed/demand_history_by_programme.csv   (src/demand_history.py)
    data/processed/skills_by_programme_llm.csv       (src/extract_skills_llm.py)
    data/processed/nav_health.csv                    (src/filter_health.py)
    data/eval/ranking_ranks.csv                      (src/evaluate_ranking.py)
    data/eval/ranking_scores.csv                     (src/evaluate_ranking.py)
    data/eval/skills_scores.csv                      (src/evaluate_skills.py)
    src/dashboard_template.html
Output:
    dashboard/index.html

Run from the repo root:
    python src/build_dashboard.py
"""

import json
import os

import pandas as pd

from dashboard_translations import PROGRAMMES_EN, SKILLS_EN

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROCESSED = os.path.join(ROOT, "data", "processed")
EVAL = os.path.join(ROOT, "data", "eval")
TEMPLATE_PATH = os.path.join(ROOT, "src", "dashboard_template.html")
OUT_PATH = os.path.join(ROOT, "dashboard", "index.html")

N_SKILLS = 15  # skills shown per programme (in each of the two skill views)

# ranking_ranks.csv column -> key used in the page
RANK_VARIANTS = {
    "rank_method": "main",
    "rank_method_title_only": "title_only",
    "rank_method_snapshot": "snapshot",
    "rank_method_permanent": "permanent",
    "rank_method_fulltime": "fulltime",
}


def read(*parts):
    return pd.read_csv(os.path.join(*parts), encoding="utf-8")


def programmes():
    gap = read(PROCESSED, "gap_by_programme.csv")
    ranks = read(EVAL, "ranking_ranks.csv").set_index("programme")
    history = read(PROCESSED, "demand_history_by_programme.csv").set_index("programme")
    rows = []
    for g in gap.itertuples():
        r = ranks.loc[g.programme]
        rows.append({
            "id": g.programme,
            "en": PROGRAMMES_EN.get(g.programme, g.programme),
            "rank": int(g.rank),
            "postings": int(g.postings),
            "places": int(g.study_places),
            "ppp": float(g.postings_per_place),
            "applicants": float(g.first_choice_per_place),
            "fulltime": float(g.share_fulltime),
            "temporary": float(g.share_temporary),
            "n_snapshot": int(g.n_snapshot),
            "title_precision": float(history.loc[g.programme, "precision"]),
            "title_recall": float(history.loc[g.programme, "recall"]),
            "rank_baseline": int(r["rank_baseline"]),
            "ranks": {key: int(r[col]) for col, key in RANK_VARIANTS.items()},
        })
    return rows


def skills(names):
    s = read(PROCESSED, "skills_by_programme_llm.csv")
    s = s[s.programme.isin(names)].sort_values("share_postings", ascending=False)
    # top skills overall, plus top skills from the ESCO profile, so the
    # "only ESCO profile" filter still has N_SKILLS skills to show
    top_all = s.groupby("programme").head(N_SKILLS)
    top_profile = s[s.esco_relation != "none"].groupby("programme").head(N_SKILLS)
    top = pd.concat([top_all, top_profile]).drop_duplicates(["programme", "skill"])
    top = top.sort_values(["programme", "share_postings"], ascending=[True, False])

    missing = sorted(set(top.skill) - set(SKILLS_EN))
    if missing:
        print(f"warning: {len(missing)} skills have no English name:", ", ".join(missing))

    out = {}
    for programme, group in top.groupby("programme"):
        out[programme] = [
            {
                "no": row.skill,
                "en": SKILLS_EN.get(row.skill, row.skill),
                "rel": row.esco_relation,
                "share": round(float(row.share_postings), 3),
                "n": int(row.n_postings),
            }
            for row in group.itertuples()
        ]
    return out


def counties(names):
    health = read(PROCESSED, "nav_health.csv")[["programme", "county"]]
    health = health[health.programme.isin(names) & health.county.notna()]
    health["county"] = health.county.str.title().str.replace(" Og ", " og ")
    counts = health.groupby(["programme", "county"]).size().reset_index(name="n")
    out = {"_all": health.county.value_counts().to_dict()}
    for programme, group in counts.groupby("programme"):
        out[programme] = dict(zip(group.county, group.n.astype(int)))
    return out


def evaluation():
    scores = read(EVAL, "skills_scores.csv")
    total = scores[scores.programme == "ALL"].set_index("method")
    skill_scores = {
        method: {k: round(float(total.loc[method, k]), 3) for k in ["precision", "recall", "f1"]}
        for method in ["baseline", "llm"]
    }
    ranking = read(EVAL, "ranking_scores.csv")
    method_rows = ranking[ranking.question.isin(["decision_change", "robustness_method"])]
    spearman = [round(float(x), 3) for x in method_rows.spearman]
    return {"skills": skill_scores, "spearman": spearman}


def main():
    progs = programmes()
    names = [p["id"] for p in progs]
    data = {
        "programmes": progs,
        "skills": skills(names),
        "counties": counties(names),
        "eval": evaluation(),
    }
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        html = f.read()
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    html = html.replace("/*__DATA__*/null", payload)

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"wrote {os.path.relpath(OUT_PATH, ROOT)} ({len(html) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
