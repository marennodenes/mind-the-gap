# mind-the-gap

Which health programmes should Norway prioritise to meet employer demand?

The project compares job postings from NAV (demand) with study places in
Samordna opptak 2026 (supply) for health professions that require a higher
education degree, and uses ESCO to find the skills employers ask for.

## Data

| Source | Gives us | File |
|---|---|---|
| [NAV job feed](https://navikt.github.io/pam-stilling-feed/) | Job postings: current with full text, and 12 months of titles | `data/raw/nav_all.csv`, `data/raw/nav_history.csv` |
| [HK-dir / Samordna opptak](https://hkdir.no/sokertall-fra-samordna-opptak-til-nedlasting) | Study places and applicants per study | `data/raw/hkdir.xlsx` |
| [ESCO](https://esco.ec.europa.eu) | Standard list of skills (Norwegian) | `data/raw/esco/` |

## Pipeline

Run from the repo root, in this order:

```bash
python src/fetch_nav.py
python src/fetch_nav.py --history
python src/filter_health.py
python src/demand_history.py
python src/build_supply.py
python src/build_gap.py
python src/build_skills.py
```

Each script describes its input and output at the top of the file.

## Method notes

- **Demand** is the number of postings in the last 12 months. NAV deletes the
  ad text when a posting expires, so older postings are classified by title.
  The title rules are calibrated against current postings, where the NAV
  occupation code is known (precision and recall are in
  `demand_history_by_programme.csv`).
- **Supply** is the number of study places in Samordna opptak 2026, summed
  over all institutions, campuses and part-time variants.
- **Gap** is postings per study place. First-choice applicants per place show
  whether more places would actually be filled.
- **Skills** are found by searching the posting texts for the Norwegian names
  of ESCO skills. Each programme is linked to its closest ESCO occupations
  (`PROGRAMME_TO_ESCO` in `src/build_skills.py`), so mentioned skills can be
  compared with the skills ESCO lists as essential or optional. Exact name
  matching misses paraphrases, so mention shares are a lower bound. Most ESCO
  skill names are long phrases that never occur word for word in a posting, so
  profile coverage is computed only over skills with a name of at most two
  words. Compound words that turn a skill into a job title or workplace
  ("barnevernspedagog", "rehabiliteringssenter") and ESCO alternative names that
  change the meaning are excluded.

## Setup

```bash
pip install -r requirements.txt
```
