# mind-the-gap

Which health programmes should Norway prioritise to meet employer demand?

We compare job postings from NAV (demand) with study places in Samordna opptak
2026 (supply) for health professions that need a higher education degree. We
also find which skills employers ask for, using the ESCO skills list.

**Answer so far:** Medisin, Psykologi and Odontologi have the most job postings
per study place. Ranking by the number of postings alone would instead put
Sykepleie first, because it is the largest programme.

## Quickstart

All data is already in the repo, so you don't need to download anything.

```bash
pip install -r requirements.txt
python src/build_gap.py           # prints the ranking of programmes
python src/evaluate_ranking.py    # compares the ranking with the baseline
```

The results are saved as CSV files in `data/processed/` and `data/eval/`.

## Folders

| Folder | Contents |
|---|---|
| `src/` | the scripts. Each one says at the top what it reads and writes |
| `data/raw/` | the downloaded data (NAV, Samordna opptak, ESCO) |
| `data/processed/` | the results the analysis uses |
| `data/eval/` | everything used to check how good the methods are |

## Scripts

Run them from the repo root, in this order. Steps 1 and 8 are only needed if
you want new data; their results are already in the repo.

| # | Script | What it does |
|---|---|---|
| 1 | `fetch_nav.py` (and `--history`) | downloads job postings from NAV. **Overwrites the data in the repo** |
| 2 | `filter_health.py` | keeps health postings and links each one to a study programme |
| 3 | `demand_history.py` | counts postings per programme for the last 12 months |
| 4 | `build_supply.py` | counts study places per programme |
| 5 | `build_gap.py` | postings per study place, ranked. **This is the main result** |
| 6 | `build_skills.py` | finds skills by searching the postings for ESCO skill names |
| 7 | `evaluate_ranking.py` | evaluates the ranking (see below) |
| 8 | `extract_skills_llm.py` | finds skills with an LLM (Claude). Slow, needs a Claude login |
| 9 | `evaluate_skills.py score` | compares the two skill methods with the hand labels |

## How we measure

- **Demand** is the number of job postings in the last 12 months. NAV deletes
  the text of old postings, so we sort them into programmes by job title. We
  check those title rules against current postings, where NAV's occupation code
  tells us the right answer.
- **Supply** is the number of study places in Samordna opptak 2026, all
  institutions added together.
- **Gap** is postings per study place. A high number means many jobs compared
  with how many students are trained. First-choice applicants per place show
  whether more places would be filled.
- **Skills** come from two methods. The simple one searches the postings for
  the exact ESCO skill names. It misses skills written in other words
  ("sårstell" for "sårbehandling"). The LLM method lets Claude read each
  posting and choose skills from the ESCO list, so it also finds those.

## Evaluation

Each method is compared with a simple baseline.

**Ranking** (`evaluate_ranking.py`). Baseline: rank by number of postings.
Method: rank by postings per study place.

| | Top 3 |
|---|---|
| Baseline | Sykepleie, Medisin, Vernepleie |
| Method | Medisin, Psykologi, Odontologi |

So the method changes the decision. Its top 3 also stays the same when demand
is counted in other ways (only permanent jobs, only full-time, title rules
without correction). There is no "correct" ranking to compare with, so this
shows that the result is different and stable, not that it is right.

**Skills** (`evaluate_skills.py`). We labelled 50 postings by hand
(`data/eval/skills_gold.csv`), without knowing which method had found each
skill.

| | Precision | Recall |
|---|---|---|
| Baseline (exact ESCO names) | 0.87 | 0.25 |
| LLM | 0.86 | 0.96 |

Both methods are about as precise, but the LLM finds almost all the skills,
while the exact search misses three out of four. Two caveats: recall only
counts skills that one of the methods found or that we added by hand, so it is
too high. And the LLM chooses from a list that includes skills the baseline
found, so the methods are not fully independent.
