"""
Evaluate skill extraction: baseline (exact ESCO name matching, src/build_skills.py)
vs. LLM (src/extract_skills_llm.py), against hand-labelled postings.

Steps (run from the repo root):

1. Draw a sample of postings (stratified by programme):
       python src/evaluate_skills.py sample            -> data/eval/sample_ids.csv

2. Run the LLM on the sample:
       python src/extract_skills_llm.py --ids data/eval/sample_ids.csv

3. Make the labelling sheet:
       python src/evaluate_skills.py sheet             -> data/eval/skills_gold.csv
   One row per (posting, skill) predicted by either method. The sheet does not
   say which method predicted what, so the labelling is blind. Read the posting
   (data/eval/sample_postings.csv) and set gold = 1 if the posting asks for the
   skill, 0 if not. Add a row (with gold = 1) for skills both methods missed;
   use the ESCO skill name as it is written in the sheet or in
   skills_by_programme.csv.

4. Score:
       python src/evaluate_skills.py score             -> data/eval/skills_scores.csv

Recall is "pooled": it counts the skills found by at least one method plus those
added by hand, so it is an upper bound if labellers do not add missed skills.
"""

import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_skills import (  # noqa: E402
    ESCO, HEALTH_PATH, NOT_LANGUAGE, ROOT, load_labels, match_skills,
)
from extract_skills_llm import BY_POSTING_PATH as LLM_PATH  # noqa: E402

EVAL = os.path.join(ROOT, "data", "eval")
SAMPLE_PATH = os.path.join(EVAL, "sample_ids.csv")
POSTINGS_PATH = os.path.join(EVAL, "sample_postings.csv")
GOLD_PATH = os.path.join(EVAL, "skills_gold.csv")
SCORES_PATH = os.path.join(EVAL, "skills_scores.csv")

SAMPLE_SIZE = 50
MIN_PER_PROGRAMME = 2
SEED = 42


def sample():
    health = pd.read_csv(HEALTH_PATH)
    # At least MIN_PER_PROGRAMME per programme, the rest proportional to size.
    parts = [g.sample(min(len(g), MIN_PER_PROGRAMME), random_state=SEED)
             for _, g in health.groupby("programme")]
    picked = pd.concat(parts)
    rest = health.drop(picked.index)
    picked = pd.concat([picked, rest.sample(SAMPLE_SIZE - len(picked), random_state=SEED)])
    os.makedirs(EVAL, exist_ok=True)
    picked[["id", "programme"]].sort_values(["programme", "id"]).to_csv(SAMPLE_PATH, index=False)
    picked[["id", "programme", "title", "employer", "description_clean"]] \
        .sort_values(["programme", "id"]).to_csv(POSTINGS_PATH, index=False)
    print(picked["programme"].value_counts().to_string())
    print(f"\nSaved {SAMPLE_PATH}\nSaved {POSTINGS_PATH}")


def baseline_predictions(ids):
    """Run the baseline matcher from build_skills.py on the given postings."""
    health = pd.read_csv(HEALTH_PATH, usecols=["id", "programme", "description_clean"])
    health = health[health["id"].isin(ids)].reset_index(drop=True)
    skills = pd.read_csv(os.path.join(ESCO, "skills_no.csv"))
    labels = load_labels(skills)
    texts = [NOT_LANGUAGE.sub(" ", t) for t in health["description_clean"].fillna("").str.lower()]
    health["skill_uri"] = match_skills(texts, labels)
    # The matcher returns ESCO URIs; the LLM output and the label sheet use names.
    uri_to_name = (skills.drop_duplicates("conceptUri").set_index("conceptUri")
                   ["preferredLabel"].str.strip().str.lower())
    out = health[["id", "skill_uri"]].explode("skill_uri").dropna()
    out["skill"] = out["skill_uri"].map(uri_to_name)
    return out[["id", "skill"]].drop_duplicates()


def predictions():
    ids = pd.read_csv(SAMPLE_PATH)["id"]
    base = baseline_predictions(ids).assign(baseline=1)
    llm = pd.read_csv(LLM_PATH)
    missing = set(ids) - set(llm["id"])
    llm = llm[llm["id"].isin(ids)][["id", "skill"]].assign(llm=1)
    if missing:
        print(f"Warning: {len(missing)} sample postings have no LLM result. "
              "Run: python src/extract_skills_llm.py --ids data/eval/sample_ids.csv")
    pred = base.merge(llm, on=["id", "skill"], how="outer").fillna(0)
    return pred, missing


def sheet():
    if os.path.exists(GOLD_PATH):
        sys.exit(f"{GOLD_PATH} exists; delete it first to avoid overwriting labels.")
    pred, missing = predictions()
    pred = pred[~pred["id"].isin(missing)]
    info = pd.read_csv(POSTINGS_PATH, usecols=["id", "programme", "title"])
    out = info.merge(pred[["id", "skill"]], on="id").sort_values(["programme", "id", "skill"])
    out["gold"] = ""
    out.to_csv(GOLD_PATH, index=False)
    print(f"{len(out)} rows to label for {out['id'].nunique()} postings.\nSaved {GOLD_PATH}")


def prf(df, col):
    tp = ((df[col] == 1) & (df["gold"] == 1)).sum()
    fp = ((df[col] == 1) & (df["gold"] == 0)).sum()
    fn = ((df[col] == 0) & (df["gold"] == 1)).sum()
    p = tp / (tp + fp) if tp + fp else float("nan")
    r = tp / (tp + fn) if tp + fn else float("nan")
    f = 2 * p * r / (p + r) if p + r else float("nan")
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(f, 3),
            "tp": tp, "fp": fp, "fn": fn}


def score():
    gold = pd.read_csv(GOLD_PATH)
    if gold["gold"].isna().any():
        print(f"Warning: {gold['gold'].isna().sum()} rows have no label and are skipped.")
        gold = gold.dropna(subset=["gold"])
    gold["gold"] = gold["gold"].astype(int)
    gold["skill"] = gold["skill"].str.strip().str.lower()

    pred, _ = predictions()
    df = gold.merge(pred, on=["id", "skill"], how="left").fillna({"baseline": 0, "llm": 0})

    rows = []
    for method in ["baseline", "llm"]:
        rows.append({"method": method, "programme": "ALL", **prf(df, method)})
        for prog, g in df.groupby("programme"):
            rows.append({"method": method, "programme": prog, **prf(g, method)})
    scores = pd.DataFrame(rows)
    scores.to_csv(SCORES_PATH, index=False)
    print(f"Postings: {df['id'].nunique()}, labelled skill rows: {len(df)}\n")
    print(scores[scores["programme"] == "ALL"].to_string(index=False))
    print(f"\nSaved {SCORES_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate skill extraction.")
    parser.add_argument("step", choices=["sample", "sheet", "score"])
    {"sample": sample, "sheet": sheet, "score": score}[parser.parse_args().step]()
