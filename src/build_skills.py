"""
Find which ESCO skills employers mention in healthcare postings, and compare
them with the skills ESCO lists for each profession.

Each programme is linked to one or more ESCO occupations. ESCO says which
skills are essential or optional for those occupations (the "ESCO profile").
The cleaned posting texts are then searched for the Norwegian name (and
alternative names) of every ESCO skill.

Input:
    data/raw/esco/occupations_no.csv
    data/raw/esco/skills_no.csv
    data/raw/esco/occupationSkillRelations_no.csv
    data/processed/nav_health.csv                 (src/filter_health.py)
Output:
    data/processed/skills_by_programme.csv        one row per programme x skill
    data/processed/esco_coverage_by_programme.csv one row per programme

Columns in skills_by_programme.csv:
    programme
    skill               ESCO preferred label (Norwegian)
    skill_type          knowledge or skill/competence
    reuse_level         transversal, cross-sector, sector-specific, occupation-specific
    esco_relation       essential / optional = in the ESCO profile of the programme,
                        none = mentioned by employers but not in the profile
    n_postings          postings for the programme that mention the skill
    share_postings      n_postings / all postings for the programme
Profile skills that no posting mentions are kept with n_postings = 0.

Run from the repo root:
    python src/build_skills.py
"""

import os
import re

import pandas as pd

# --- Settings ---------------------------------------------------------------

MIN_LABEL_LEN = 5      # ignore very short skill names (too many false matches)
COMPOUND_LEN = 8       # names at least this long also match as the start of a
                       # compound word ("smittevern" in "smittevernrutiner")

# Skill names that match in the text but almost never mean the skill.
STOP_LABELS = {
    "høste",            # "høsten" (autumn)
    "svømme",           # "svømmebasseng" in descriptions of the workplace
    "geografi",         # "geografisk" (location)
    "kunst",            # culture/leisure descriptions of the municipality
    "søknadsprosess",   # how to apply for the job
    "kunnskapsbase",    # "kunnskapsbasert praksis" is a different concept
    "personlig utvikling",  # mostly "muligheter for personlig utvikling" (a perk)
    "samtale",          # "samtale" = interview in "innkalles til samtale"
    "beregning",        # "beregning av lønn"
    "økonomi",          # mostly about the employer's budget
}

# Phrases removed from the text before matching. "norsk autorisasjon" is the
# Norwegian licence to practise, not a language skill (it would otherwise make
# "norsk" appear in ~90% of postings instead of the ~50% that ask for the language).
NOT_LANGUAGE = re.compile(
    r"(?<!\w)(norsk|svensk|dansk)\w*\s+(autorisasjon|helsevesen|helsetjeneste|"
    r"spesialistgodkjenning|godkjenning|lisens|personnummer|legeforening|helsenett)\w*"
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ESCO = os.path.join(ROOT, "data", "raw", "esco")
PROCESSED = os.path.join(ROOT, "data", "processed")
HEALTH_PATH = os.path.join(PROCESSED, "nav_health.csv")
SKILLS_PATH = os.path.join(PROCESSED, "skills_by_programme.csv")
COVERAGE_PATH = os.path.join(PROCESSED, "esco_coverage_by_programme.csv")

# Programme -> ESCO occupation codes (column "code" in occupations_no.csv).
# Vernepleie, Bioingeniør and Klinisk ernæring have no exact ESCO match;
# the closest occupation is used.
PROGRAMME_TO_ESCO = {
    "Sykepleie": ["2221.2", "2221.3"],                   # sykepleier, spesialsykepleier
    "Medisin": ["2211.1", "2212.1"],                     # allmennpraktiserende lege, spesiallege
    "Odontologi": ["2261.1", "2261.2"],                  # allmennpraktiserende tannlege, spesialtannlege
    "Tannpleie": ["2261.3"],                             # tannpleier
    "Psykologi (profesjonsstudium)": ["2634.2", "2634.2.1"],  # psykolog, klinisk psykolog
    "Fysioterapi": ["2264.2", "2264.1"],                 # fysioterapeut, spesialist i fysioterapi
    "Ergoterapi": ["2269.4"],                            # ergoterapeut
    "Radiografi / audiografi": ["2269.8", "2269.8.2"],   # radiograf (+ nuklearmedisin)
    "Audiografi / logopedi": ["3214.1", "2266.2"],       # audiograf, talepedagog
    "Bioingeniør": ["2269.13"],                          # biomedisinsk forsker (biomedical scientist)
    "Klinisk ernæring": ["2265.1"],                      # ernæringsekspert
    "Vernepleie": ["2635.2"],                            # sosialpedagog (closest match)
}


# --- ESCO ---------------------------------------------------------------------

def load_labels(skills):
    """One row per (skill, searchable name): preferred label + alternative labels."""
    rows = []
    for s in skills.itertuples():
        names = [s.preferredLabel]
        if isinstance(s.altLabels, str):
            names += s.altLabels.split("\n")
        for name in names:
            name = name.strip().lower()
            if len(name) >= MIN_LABEL_LEN and name not in STOP_LABELS:
                rows.append({"skill": s.preferredLabel.strip().lower(), "name": name})
    return pd.DataFrame(rows).drop_duplicates()


def esco_profiles(occupations, relations, skills):
    """Programme x skill with relation type (essential wins over optional)."""
    code_to_uri = occupations.set_index("code")["conceptUri"]
    rows = [
        {"programme": prog, "occupationUri": code_to_uri[code]}
        for prog, codes in PROGRAMME_TO_ESCO.items()
        for code in codes
    ]
    prof = pd.DataFrame(rows).merge(relations, on="occupationUri")
    prof = prof.merge(
        skills[["conceptUri", "preferredLabel"]], left_on="skillUri", right_on="conceptUri"
    )
    prof["skill"] = prof["preferredLabel"].str.strip().str.lower()
    prof["esco_relation"] = prof["relationType"]
    prof = (
        prof.sort_values("esco_relation")  # "essential" sorts before "optional"
        .drop_duplicates(["programme", "skill"])
    )
    return prof[["programme", "skill", "esco_relation"]]


# --- Matching -------------------------------------------------------------------

def name_pattern(name):
    """Regex for one skill name: must start a word; short names must also end one
    (allowing common Norwegian endings), long names may start a compound word."""
    rx = r"(?<!\w)" + re.escape(name)
    if len(name) < COMPOUND_LEN:
        rx += r"(e|en|er|ene|et|a|s)?(?!\w)"
    return re.compile(rx)


def match_skills(texts, labels):
    """Return a list with the set of matched skills for each text."""
    corpus = "\n".join(texts)
    # Cheap pre-filter: only names that occur somewhere in the corpus.
    labels = labels[[name in corpus for name in labels["name"]]]
    found = [set() for _ in texts]
    for skill, name in labels.itertuples(index=False):
        rx = name_pattern(name)
        for i, text in enumerate(texts):
            if skill not in found[i] and rx.search(text):
                found[i].add(skill)
    return found


# --- Main -------------------------------------------------------------------------

def main():
    occupations = pd.read_csv(os.path.join(ESCO, "occupations_no.csv"))
    skills = pd.read_csv(os.path.join(ESCO, "skills_no.csv"))
    relations = pd.read_csv(os.path.join(ESCO, "occupationSkillRelations_no.csv"))
    health = pd.read_csv(HEALTH_PATH, usecols=["id", "programme", "description_clean"])

    labels = load_labels(skills)
    skill_info = skills.assign(skill=skills["preferredLabel"].str.strip().str.lower())
    skill_info = skill_info.drop_duplicates("skill").set_index("skill")[["skillType", "reuseLevel"]]

    texts = [
        NOT_LANGUAGE.sub(" ", t)
        for t in health["description_clean"].fillna("").str.lower()
    ]
    health["skills"] = match_skills(texts, labels)

    # Programme x skill counts.
    mentions = (
        health[["programme", "skills"]].explode("skills").dropna()
        .rename(columns={"skills": "skill"})
        .groupby(["programme", "skill"]).size().rename("n_postings").reset_index()
    )
    profile = esco_profiles(occupations, relations, skills)
    table = profile.merge(mentions, on=["programme", "skill"], how="outer")
    table["esco_relation"] = table["esco_relation"].fillna("none")
    table["n_postings"] = table["n_postings"].fillna(0).astype(int)

    n_prog = health.groupby("programme").size()
    table["share_postings"] = (table["n_postings"] / table["programme"].map(n_prog)).round(3)
    table["skill_type"] = table["skill"].map(skill_info["skillType"])
    table["reuse_level"] = table["skill"].map(skill_info["reuseLevel"])
    table = table.sort_values(["programme", "n_postings", "skill"], ascending=[True, False, True])
    table = table[[
        "programme", "skill", "skill_type", "reuse_level",
        "esco_relation", "n_postings", "share_postings",
    ]]
    table.to_csv(SKILLS_PATH, index=False)

    # How much of each ESCO profile do employers mention?
    in_profile = table[table["esco_relation"] != "none"]
    coverage = in_profile.groupby("programme").agg(
        profile_skills=("skill", "count"),
        profile_skills_mentioned=("n_postings", lambda s: (s > 0).sum()),
    )
    coverage["share_profile_mentioned"] = (
        coverage["profile_skills_mentioned"] / coverage["profile_skills"]
    ).round(3)
    coverage["other_skills_mentioned"] = (
        table[(table["esco_relation"] == "none")].groupby("programme").size()
    )
    coverage["postings"] = n_prog
    coverage["postings_with_any_skill"] = health.groupby("programme")["skills"].apply(
        lambda s: (s.str.len() > 0).sum()
    )
    coverage["esco_occupations"] = pd.Series(
        {p: ", ".join(c) for p, c in PROGRAMME_TO_ESCO.items()}
    )
    coverage = coverage.fillna(0).reset_index().sort_values("postings", ascending=False)
    coverage.to_csv(COVERAGE_PATH, index=False)

    print(f"Postings searched:       {len(health)}")
    print(f"Skill names searched:    {len(labels)}")
    print(f"Postings with any skill: {(health['skills'].str.len() > 0).sum()}\n")
    print(coverage.to_string(index=False))
    print("\nTop 5 skills per programme:")
    for prog, g in table[table["n_postings"] > 0].groupby("programme"):
        top = ", ".join(f"{s} ({p:.0%})" for s, p in zip(g["skill"][:5], g["share_postings"][:5]))
        print(f"  {prog}: {top}")
    print(f"\nSaved {SKILLS_PATH}")
    print(f"Saved {COVERAGE_PATH}")


if __name__ == "__main__":
    main()
