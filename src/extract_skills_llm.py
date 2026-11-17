"""
Extract ESCO skills from healthcare postings with an LLM (Claude).

This is the method we compare against the baseline in src/build_skills.py
(exact matching of ESCO skill names). Instead of searching for exact names,
the model reads each posting and picks the skills it asks for from a fixed
candidate list, so paraphrases ("erfaring med sårstell") can still be mapped
to an ESCO skill, and words used in another sense ("svangerskapsvikariat")
are not counted.

Candidate list per programme:
    the ESCO profile of the programme (essential + optional skills)
    + skills the baseline found in at least MIN_BASELINE_POSTINGS postings
      for that programme (e.g. "norsk", "kommunikasjon", which ESCO does not
      list in the health profiles).
The model can only answer with ids from this list, so the output always maps
to ESCO.

Input:
    data/raw/esco/*.csv
    data/processed/nav_health.csv                 (src/filter_health.py)
    data/processed/skills_by_programme.csv        (src/build_skills.py)
Output:
    data/processed/llm_skills_raw.jsonl           one line per posting (cache: reruns
                                                  skip postings already done)
    data/processed/skills_by_posting_llm.csv      one row per posting x skill (+ evidence)
    data/processed/skills_by_programme_llm.csv    same columns as skills_by_programme.csv

Two ways to call Claude (--backend):
    claude-code (default)  runs `claude -p` (Claude Code CLI) for each posting. Uses the
                           Claude Pro/Max login on this machine and counts against that
                           plan's usage limits; no API key needed. Default model: Haiku.
    api                    calls the Anthropic API with the `anthropic` package. Needs
                           ANTHROPIC_API_KEY (or a .env file in the repo root) and is
                           billed per token. Default model: Opus.
If the plan's usage limit is reached, the run stops; rerun later and it continues
where it left off.

Run from the repo root:
    python src/extract_skills_llm.py --limit 10               # try a few first
    python src/extract_skills_llm.py --ids data/eval/sample_ids.csv
    python src/extract_skills_llm.py                          # all postings
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_skills import ESCO, PROCESSED, HEALTH_PATH, SKILLS_PATH, esco_profiles  # noqa: E402

# --- Settings ---------------------------------------------------------------

BACKEND = "claude-code"
MODELS = {"claude-code": "claude-haiku-5-5", "api": "claude-opus-5-5"}
EFFORT = "low"                 # simple extraction task; raise if quality is poor
MIN_BASELINE_POSTINGS = 2      # baseline skills added to the candidate list
WORKERS = 4                    # parallel requests
MAX_ERRORS_IN_A_ROW = 5        # stop (e.g. usage limit reached) and rerun later
CLI_TIMEOUT = 300              # seconds per `claude -p` call

RAW_PATH = os.path.join(PROCESSED, "llm_skills_raw.jsonl")
BY_POSTING_PATH = os.path.join(PROCESSED, "skills_by_posting_llm.csv")
BY_PROGRAMME_PATH = os.path.join(PROCESSED, "skills_by_programme_llm.csv")

# Models that support server-side refusal fallbacks.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-2"}

SYSTEM = """You read Norwegian job postings for health professionals and identify \
which skills from a fixed list (ESCO, the European skills classification) the \
employer asks for.

Count a skill when the posting:
- states it as a requirement or a desired qualification, or
- describes work tasks of the role that require it.

Do not count a skill when the word appears in another sense, for example:
- describing the employer, the municipality, leisure activities or perks
- the type of position ("svangerskapsvikariat" is a maternity-cover position, not a skill)
- the application process ("samtale" = interview)
- "norsk autorisasjon" (a licence to practise), which is not a language requirement.
  Count "norsk" only when Norwegian language skills are required.

Map paraphrases to the closest skill in the list when the meaning is the same.
Only use ids from the list. For each skill give a short quote (max 15 words) \
from the posting as evidence. Return an empty list if no skill applies."""

SCHEMA = {
    "type": "object",
    "properties": {
        "skills": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "evidence": {"type": "string"},
                },
                "required": ["id", "evidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["skills"],
    "additionalProperties": False,
}


# --- Candidates ------------------------------------------------------------------

def candidate_lists():
    """programme -> sorted list of candidate skills (lower-cased ESCO labels)."""
    occupations = pd.read_csv(os.path.join(ESCO, "occupations_no.csv"))
    skills = pd.read_csv(os.path.join(ESCO, "skills_no.csv"))
    relations = pd.read_csv(os.path.join(ESCO, "occupationSkillRelations_no.csv"))
    profile = esco_profiles(occupations, relations)  # skill URIs -> names below
    uri_to_name = (skills.drop_duplicates("conceptUri").set_index("conceptUri")
                   ["preferredLabel"].str.strip().str.lower())
    profile["skill"] = profile["skill_uri"].map(uri_to_name)

    baseline = pd.read_csv(SKILLS_PATH)
    common = baseline[baseline["n_postings"] >= MIN_BASELINE_POSTINGS]

    both = pd.concat([profile[["programme", "skill"]], common[["programme", "skill"]]])
    return {p: sorted(set(g["skill"])) for p, g in both.groupby("programme")}


def candidate_block(programme, skills):
    lines = [f"{i}: {s}" for i, s in enumerate(skills)]
    return f"Programme: {programme}\nSkill list (id: skill):\n" + "\n".join(lines)


# --- Claude -----------------------------------------------------------------------

def to_skills(ids_and_evidence, skills):
    """Map the model's ids to skill names; ignore ids outside the list."""
    return [
        {"skill": skills[item["id"]], "evidence": item["evidence"]}
        for item in ids_and_evidence
        if 0 <= item["id"] < len(skills)
    ]


def extract_claude_code(model, row, skills):
    """One posting via `claude -p` (uses the Claude plan login on this machine)."""
    text = (candidate_block(row.programme, skills)
            + f"\n\nJob posting:\nTitle: {row.title}\n\n{row.description_clean}")
    cmd = [
        "claude", "-p", "Identify the skills from the list that this job posting asks for.",
        "--system-prompt", SYSTEM,
        "--model", model,
        "--effort", EFFORT,
        "--tools", "",                       # no file/shell/web access needed
        "--no-session-persistence",
        "--output-format", "json",
        "--json-schema", json.dumps(SCHEMA),
    ]
    # Run outside the repo so no project settings or files are picked up.
    proc = subprocess.run(cmd, input=text, capture_output=True, text=True,
                          timeout=CLI_TIMEOUT, cwd=tempfile.gettempdir())
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        msg = (proc.stderr or proc.stdout).strip()[:300]
        return {"id": row.id, "error": f"claude exit {proc.returncode}: {msg}", "skills": []}
    if out.get("is_error") or "structured_output" not in out:
        return {"id": row.id, "error": str(out.get("result"))[:300], "skills": []}

    usage = out.get("usage", {})
    return {
        "id": row.id,
        "programme": row.programme,
        "model": ", ".join(out.get("modelUsage", {})) or model,
        "skills": to_skills(out["structured_output"]["skills"], skills),
        "input_tokens": usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0),
        "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "api_equivalent_usd": out.get("total_cost_usd", 0),
    }


def extract(client, model, row, skills):
    """One posting via the Anthropic API."""
    kwargs = dict(
        model=model,
        max_tokens=4000,
        system=SYSTEM,
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{
            "role": "user",
            "content": [
                # Same for all postings of a programme -> cached after the first request.
                {"type": "text", "text": candidate_block(row.programme, skills),
                 "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": f"Job posting:\nTitle: {row.title}\n\n{row.description_clean}"},
            ],
        }],
    )
    if model in FALLBACK_MODELS:
        response = client.beta.messages.create(
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
        )
    else:
        response = client.messages.create(**kwargs)

    if response.stop_reason == "refusal":
        return {"id": row.id, "error": "refusal", "skills": []}
    if response.stop_reason == "max_tokens":
        return {"id": row.id, "error": "max_tokens", "skills": []}

    text = next(b.text for b in response.content if b.type == "text")
    return {
        "id": row.id,
        "programme": row.programme,
        "model": response.model,
        "skills": to_skills(json.loads(text)["skills"], skills),
        "input_tokens": response.usage.input_tokens,
        "cache_read_tokens": response.usage.cache_read_input_tokens,
        "output_tokens": response.usage.output_tokens,
    }


def load_done():
    if not os.path.exists(RAW_PATH):
        return {}
    with open(RAW_PATH, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    return {r["id"]: r for r in rows if "error" not in r}


# --- Main -------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--limit", type=int, help="only the first N postings")
    parser.add_argument("--ids", help="CSV with an 'id' column: only these postings")
    parser.add_argument("--backend", choices=["claude-code", "api"], default=BACKEND)
    parser.add_argument("--model", help="default: Haiku for claude-code, Opus for api")
    parser.add_argument("--workers", type=int, default=WORKERS)
    args = parser.parse_args()
    model = args.model or MODELS[args.backend]

    health = pd.read_csv(HEALTH_PATH, usecols=["id", "title", "programme", "description_clean"])
    health["description_clean"] = health["description_clean"].fillna("")
    if args.ids:
        health = health[health["id"].isin(pd.read_csv(args.ids)["id"])]
    # Sorting by programme keeps the cached candidate list warm.
    health = health.sort_values(["programme", "id"])
    if args.limit:
        health = health.head(args.limit)

    candidates = candidate_lists()
    done = load_done()
    todo = health[~health["id"].isin(done)]
    print(f"Postings: {len(health)}  (already done: {len(health) - len(todo)}, to do: {len(todo)})")
    print("Candidate skills per programme: "
          + ", ".join(f"{p} {len(s)}" for p, s in candidates.items()))

    print(f"Backend: {args.backend}, model: {model}")

    if args.backend == "api":
        import anthropic
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass
        client = anthropic.Anthropic()

        def call(row):
            try:
                return extract(client, model, row, candidates[row.programme])
            except anthropic.APIError as e:
                return {"id": row.id, "error": str(e), "skills": []}
    else:
        def call(row):
            try:
                return extract_claude_code(model, row, candidates[row.programme])
            except subprocess.TimeoutExpired:
                return {"id": row.id, "error": "timeout", "skills": []}

    stop = threading.Event()

    def run(row):
        if stop.is_set():
            return None
        return call(row)

    errors = in_a_row = 0
    with open(RAW_PATH, "a", encoding="utf-8") as out, \
            ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run, row) for row in todo.itertuples()]
        for n, fut in enumerate(as_completed(futures), 1):
            result = fut.result()
            if result is None:          # skipped after stop
                continue
            if "error" in result:
                errors += 1
                in_a_row += 1
                print(f"  error for {result['id']}: {result['error']}")
                if in_a_row >= MAX_ERRORS_IN_A_ROW and not stop.is_set():
                    print(f"{MAX_ERRORS_IN_A_ROW} errors in a row (usage limit?). Stopping.")
                    stop.set()
            else:
                in_a_row = 0
                done[result["id"]] = result
            out.write(json.dumps(result, ensure_ascii=False) + "\n")
            out.flush()
            if n % 50 == 0 or n == len(futures):
                print(f"  {n}/{len(futures)}")
    if errors or stop.is_set():
        print(f"{errors} postings failed and {len(health) - len(done.keys() & set(health['id']))} "
              f"are not done; rerun the same command to continue.")

    # Tables for every posting done so far, not only this run.
    results = list(done.values())
    by_posting = pd.DataFrame([
        {"id": r["id"], "programme": r["programme"], "skill": s["skill"], "evidence": s["evidence"]}
        for r in results for s in r["skills"]
    ], columns=["id", "programme", "skill", "evidence"]).drop_duplicates(["id", "skill"])
    by_posting.sort_values(["id", "skill"]).to_csv(BY_POSTING_PATH, index=False)

    covered = pd.read_csv(HEALTH_PATH, usecols=["id", "programme"])
    covered = covered[covered["id"].isin(done)]
    table = programme_table(by_posting, covered)
    table.to_csv(BY_PROGRAMME_PATH, index=False)

    cols = ["input_tokens", "cache_read_tokens", "output_tokens", "api_equivalent_usd"]
    tokens = pd.DataFrame(results).reindex(columns=cols).fillna(0).sum()
    print(f"\nPostings with results: {len(done)}")
    print(f"Tokens so far: input {int(tokens['input_tokens']):,}, cache read "
          f"{int(tokens['cache_read_tokens']):,}, output {int(tokens['output_tokens']):,}")
    if tokens["api_equivalent_usd"]:
        print(f"Same usage on the API would cost about ${tokens['api_equivalent_usd']:.3f}")
    print(f"Saved {BY_POSTING_PATH}")
    print(f"Saved {BY_PROGRAMME_PATH}")


def programme_table(by_posting, postings):
    """Programme x skill table with the same columns as skills_by_programme.csv."""
    baseline = pd.read_csv(SKILLS_PATH)
    meta = baseline[["programme", "skill", "skill_type", "reuse_level", "esco_relation"]]
    counts = by_posting.groupby(["programme", "skill"]).size().rename("n_postings").reset_index()
    table = meta.merge(counts, on=["programme", "skill"], how="outer")
    table["n_postings"] = table["n_postings"].fillna(0).astype(int)
    table["esco_relation"] = table["esco_relation"].fillna("none")
    n_prog = postings.groupby("programme").size()
    table = table[table["programme"].isin(n_prog.index)]
    table["share_postings"] = (table["n_postings"] / table["programme"].map(n_prog)).round(3)
    # Skills the baseline never matched have no type info in its table.
    skills = pd.read_csv(os.path.join(ESCO, "skills_no.csv"))
    info = skills.assign(skill=skills["preferredLabel"].str.strip().str.lower())
    info = info.drop_duplicates("skill").set_index("skill")
    table["skill_type"] = table["skill_type"].fillna(table["skill"].map(info["skillType"]))
    table["reuse_level"] = table["reuse_level"].fillna(table["skill"].map(info["reuseLevel"]))
    table = table.sort_values(["programme", "n_postings", "skill"], ascending=[True, False, True])
    return table[[
        "programme", "skill", "skill_type", "reuse_level",
        "esco_relation", "n_postings", "share_postings",
    ]]


if __name__ == "__main__":
    main()
