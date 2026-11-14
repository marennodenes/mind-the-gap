"""
Estimate 12-month demand per study programme from posting titles.

NAV deletes ad content once a posting expires, so the history file only has
titles. Postings are classified to a programme with regex on the title, and
the regex is calibrated against the current snapshot, where we also know the
NAV occupation (level2) and therefore the "true" programme.

Input:
    data/raw/nav_history.csv     (python src/fetch_nav.py --history)
    data/raw/nav_all.csv         (python src/fetch_nav.py)
Output:
    data/processed/demand_history_by_programme.csv
        12-month postings per programme, with the calibration
        (precision, recall, correction) used to estimate them

Run from the repo root:
    python src/demand_history.py
"""

import os
import re

import pandas as pd

from filter_health import assign_programme

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
PROCESSED = os.path.join(ROOT, "data", "processed")
HISTORY_PATH = os.path.join(RAW, "nav_history.csv")
ALL_PATH = os.path.join(RAW, "nav_all.csv")
TOTAL_PATH = os.path.join(PROCESSED, "demand_history_by_programme.csv")

# Titles that mention a profession but are not a job for a graduate in it.
EXCLUDE = re.compile(
    r"student|stipendiat|førsteamanuensis|førstelektor|professor|"
    r"\blektor|undervisning|sekretær|assistent\b|tannlegeassistent",
    re.I,
)

# Programme -> title pattern. Checked in order; first match wins.
# Sykepleie comes before Vernepleie: "Sykepleier/vernepleier" ads are
# coded as Sykepleier by NAV in most cases.
TITLE_PATTERNS = [
    ("Odontologi", r"tannleg(e|ar|er|en)\b|tannlegestilling|oralkirurg|kjeveortoped"),
    ("Tannpleie", r"tannpleie"),
    ("Sykepleie", r"sykepleie|sjukepleia|sykepleia"),
    ("Vernepleie", r"vernepleie|vernepleia"),
    ("Psykologi (profesjonsstudium)", r"psykolog"),
    ("Fysioterapi", r"fysioterapeut"),
    ("Bioingeniør", r"bioingeni"),
    ("Ergoterapi", r"ergoterapeut"),
    ("Radiografi / audiografi", r"radiograf|stråleterapeut"),
    ("Audiografi / logopedi", r"audiograf|logoped"),
    ("Klinisk ernæring", r"ernæringsfysiolog|klinisk ernæring"),
    ("Medisin",
     r"\b(lege|leger|legar|legen|legevikar|legestilling\w*|legespesialist)\b"
     r"|(over|fast|turnus|sykehjems|sjukeheims|allmenn|assistent|avdelings|"
     r"bedrifts|legevakts|kommune|spesialist|vakt|lis\d?)-?lege"
     r"|lege i spesialisering|\blis ?[123]?\b|psykiater|legetjeneste|legeteneste|läkare"),
]
TITLE_PATTERNS = [(p, re.compile(rx, re.I)) for p, rx in TITLE_PATTERNS]


def classify_title(title):
    """Return the study programme suggested by a posting title, or None."""
    if not isinstance(title, str) or EXCLUDE.search(title):
        return None
    for programme, rx in TITLE_PATTERNS:
        if rx.search(title):
            return programme
    return None


def calibrate():
    """Compare title classification with level2-based programme on the snapshot."""
    snap = pd.read_csv(ALL_PATH, usecols=["title", "jobtitle", "level2"])
    snap["truth"] = snap.apply(assign_programme, axis=1)
    snap["pred"] = snap["title"].apply(classify_title)

    rows = []
    for prog in sorted(snap["truth"].dropna().unique()):
        tp = ((snap["truth"] == prog) & (snap["pred"] == prog)).sum()
        n_true = (snap["truth"] == prog).sum()
        n_pred = (snap["pred"] == prog).sum()
        rows.append({
            "programme": prog,
            "snapshot_level2": n_true,
            "snapshot_title": n_pred,
            "precision": round(tp / n_pred, 3) if n_pred else None,
            "recall": round(tp / n_true, 3) if n_true else None,
        })
    calib = pd.DataFrame(rows)
    # Multiply title counts by this to get a level2-comparable estimate.
    calib["correction"] = (calib["snapshot_level2"] / calib["snapshot_title"]).round(3)
    return calib


def main():
    calib = calibrate()

    hist = pd.read_csv(HISTORY_PATH)
    n_all = len(hist)
    hist["programme"] = hist["title"].apply(classify_title)
    hist = hist[hist["programme"].notna()].copy()

    # Same employer re-posting the same title in the same month = one posting.
    hist["month"] = pd.to_datetime(hist["first_seen"], utc=True).dt.strftime("%Y-%m")
    hist = hist.drop_duplicates(subset=["employer", "title", "month"])

    total = hist.groupby("programme").size().rename("postings_title").reset_index()
    total = total.merge(calib, on="programme", how="left")
    total["postings_estimated"] = (total["postings_title"] * total["correction"]).round()
    total["share_title"] = (total["postings_title"] / total["postings_title"].sum()).round(3)
    total = total.sort_values("postings_title", ascending=False)
    total = total[[
        "programme", "postings_title", "postings_estimated", "share_title",
        "precision", "recall", "correction", "snapshot_level2", "snapshot_title",
    ]]
    total.to_csv(TOTAL_PATH, index=False)

    print(f"History postings:            {n_all}")
    print(f"Classified to a programme:   {len(hist)}")
    print(f"Period:                      {hist['month'].min()} – {hist['month'].max()}\n")
    print(total.to_string(index=False))
    print(f"\nSaved {TOTAL_PATH}")


if __name__ == "__main__":
    main()
