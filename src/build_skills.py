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
    searchable          True if the skill has a name of at most MAX_SEARCH_WORDS
                        words. Longer names ("administrere legemidler til
                        pasienter") almost never occur word for word in a posting,
                        so they are left out of the coverage share.
    n_postings          postings for the programme that mention the skill
    share_postings      n_postings / all postings for the programme
    skill_uri           ESCO concept URI
Profile skills that no posting mentions are kept with n_postings = 0.

Columns in esco_coverage_by_programme.csv:
    profile_skills              skills in the ESCO profile
    profile_skills_searchable   of these, skills with a short enough name
    profile_skills_mentioned    searchable profile skills found in >= 1 posting
    share_searchable_mentioned  profile_skills_mentioned / profile_skills_searchable
    other_skills_mentioned      skills found in postings but not in the profile
    postings, postings_with_any_skill, esco_occupations

Run from the repo root:
    python src/build_skills.py
"""

import os
import re

import pandas as pd

# --- Settings ---------------------------------------------------------------

MIN_LABEL_LEN = 5      # ignore very short skill names (too many false matches)
COMPOUND_LEN = 8       # names whose last word is at least this long also match
                       # as the start of a compound word ("smittevern" in
                       # "smittevernrutiner")
MAX_SEARCH_WORDS = 2   # profile skills count towards coverage only if they have
                       # a name of at most this many words. In the 2026-11 data,
                       # 34% of 1-word, 4% of 2-word, 1% of 3-word and 0% of
                       # 4+-word profile skills were found.

# Skill names that match in the text but almost never mean the skill.
STOP_LABELS = {
    "høste",            # "høsten" (autumn)
    "svømme",           # "svømmebasseng" in descriptions of the workplace
    "geografi",         # "geografisk" (location)
    "kunst",            # culture/leisure descriptions of the municipality
    "fotball",          # leisure descriptions of the municipality
    "historie",         # the employer's history / "historier"
    "søknadsprosess",   # how to apply for the job
    "kunnskapsbase",    # "kunnskapsbasert praksis" is a different concept
    "personlig utvikling",  # mostly "muligheter for personlig utvikling" (a perk)
    "samtale",          # "samtale" = interview in "innkalles til samtale"
    "beregning",        # "beregning av lønn"
    "økonomi",          # mostly about the employer's budget
    "planlegge",        # mostly the employer's plans ("vi planlegger nytt sykehjem")
    "boliglån",         # "boliglånsordning" (a perk)
    "arbeidsrett",      # "arbeidsrettet" (work-oriented), not labour law
}

# Alternative names that ESCO links to a skill with a different meaning than
# the phrase has in a posting. They are not searched (preferred labels are).
BAD_ALT_LABELS = {
    "pasientsikkerhet",        # -> "pasientsikkerhetsteorier" (theories); used as
                               #    "fokus på kvalitet og pasientsikkerhet"
    "trives med å jobbe",      # -> "vise engasjement"; "trives med å jobbe i team"
    "forme fremtiden",         # -> "tenke innovativt"; slogan "forme fremtidens ..."
    "finne opp",               # -> "tenke innovativt"
    "smidig",                  # -> ICT project management methods
    "sosiale tjenester",       # -> "klinisk sosialt arbeid"
    "kvalitetskontroll",       # -> "statistisk kvalitetskontroll"
    "kvalitetskontroller",
    "familieplanlegging",      # -> "seksualundervisning"
    "ta i bruk ny teknologi",  # -> "anvende kunnskap innen vitenskap, teknologi ..."
    "påvirke beslutninger",    # -> "markedsføre ideer, produkter eller tjenester"
    "klatre",                  # -> "flytte gjenstander"
    "granske",                 # -> "analysere musikk"
    "3d-avbildning",           # -> "holografi"
}

# Second halves of compound words that turn a skill name into a job title,
# workplace or document ("barnevernspedagog", "rehabiliteringssenter",
# "svangerskapsvikariat"). A compound match followed by one of these is ignored.
COMPOUND_STOP = [
    "vikariat", "permisjon",                              # contract types
    "pedagog", "fysiolog", "lege", "koordinator",         # job titles
    "sentral", "senter", "klinikk", "avdeling", "tjenest", "tjenes",
    "institutt", "direktorat",                            # workplaces/organisations
    "forskrift",                                          # regulations
    r"st(?!\w)",                                          # English "radiologist"
]

# Phrases removed from the text before matching. "norsk autorisasjon" is the
# Norwegian licence to practise, not a language skill (it would otherwise make
# "norsk" appear in ~90% of postings instead of the ~50% that ask for the language).
NOT_LANGUAGE = re.compile(
    r"(?<!\w)(norsk|svensk|dansk)\w*\s+(autoris|helsevesen|helsetjeneste|"
    r"spesialistgodkjenning|godkjenning|lisens|personnummer|legeforening|helsenett|"
    r"politiattest|offentlig|turnustjeneste|regelverk|nasjonal|sykehus|lis\d|"
    r"spesialitet|helselovgivning)\w*"
    # documents that must be translated: "attester må være oversatt til norsk"
    r"|(?<!\w)overs[ae]tt\w*\s+til\s+(norsk|engelsk|svensk|dansk)\w*"
    r"(\s+(eller|og)\s+(norsk|engelsk|svensk|dansk)\w*)?"
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

RELATION_ORDER = {"essential": 0, "optional": 1, "none": 2}


# --- ESCO ---------------------------------------------------------------------

def load_labels(skills):
    """One row per (skill URI, searchable name): preferred label + alternative labels."""
    rows = []
    for s in skills.itertuples():
        names = [s.preferredLabel]
        if isinstance(s.altLabels, str):
            names += [n for n in s.altLabels.split("\n")
                      if n.strip().lower() not in BAD_ALT_LABELS]
        for name in names:
            name = name.strip().lower()
            if len(name) >= MIN_LABEL_LEN and name not in STOP_LABELS:
                rows.append({"skill_uri": s.conceptUri, "name": name})
    return pd.DataFrame(rows).drop_duplicates()


def esco_profiles(occupations, relations):
    """Programme x skill URI with relation type (essential wins over optional)."""
    code_to_uri = occupations.set_index("code")["conceptUri"]
    rows = [
        {"programme": prog, "occupationUri": code_to_uri[code]}
        for prog, codes in PROGRAMME_TO_ESCO.items()
        for code in codes
    ]
    prof = pd.DataFrame(rows).merge(relations, on="occupationUri")
    prof = prof.rename(columns={"skillUri": "skill_uri", "relationType": "esco_relation"})
    prof = (
        prof.sort_values("esco_relation", kind="stable")  # "essential" before "optional"
        .drop_duplicates(["programme", "skill_uri"])
    )
    return prof[["programme", "skill_uri", "esco_relation"]]


# --- Matching -------------------------------------------------------------------

def name_pattern(name):
    """Regex for one skill name: must start a word. If the last word of the name
    is short, the match must also end a word (allowing common Norwegian endings);
    if it is long, the name may start a compound word, unless the rest of the
    compound makes it a job title or workplace (COMPOUND_STOP)."""
    rx = r"(?<!\w)" + re.escape(name)
    if len(name.split()[-1]) < COMPOUND_LEN:
        rx += r"(e|en|er|ene|et|a|s)?(?!\w)"
    else:
        rx += r"(?!s?(" + "|".join(COMPOUND_STOP) + "))"
    return re.compile(rx)


def match_skills(texts, labels):
    """Return a list with the set of matched skill URIs for each text.

    Where two skill names match overlapping text, only the longer match counts
    ("generell kirurgi" is not also counted as "kirurgi", "produksjonsprosesser"
    not also as "produksjonsprosess"). Identical names of different skills both count.
    """
    corpus = "\n".join(texts)
    # Cheap pre-filter: only names that occur somewhere in the corpus.
    labels = labels[[name in corpus for name in labels["name"]]]
    spans = [[] for _ in texts]
    for uri, name in labels.itertuples(index=False):
        rx = name_pattern(name)
        for i, text in enumerate(texts):
            for m in rx.finditer(text):
                spans[i].append((m.start(), m.end(), uri))

    found = []
    for matches in spans:
        keep = set()
        for s, e, uri in matches:
            covered = any(
                s2 <= s and e <= e2 and (e2 - s2) > (e - s)
                for s2, e2, uri2 in matches if uri2 != uri
            )
            if not covered:
                keep.add(uri)
        found.append(keep)
    return found


# --- Main -------------------------------------------------------------------------

def main():
    occupations = pd.read_csv(os.path.join(ESCO, "occupations_no.csv"))
    skills = pd.read_csv(os.path.join(ESCO, "skills_no.csv"))
    # 21 skills appear twice in the export (two versions); keep the newest.
    skills = (
        skills.sort_values("modifiedDate")
        .drop_duplicates("conceptUri", keep="last")
    )
    relations = pd.read_csv(os.path.join(ESCO, "occupationSkillRelations_no.csv"))
    health = pd.read_csv(HEALTH_PATH, usecols=["id", "programme", "description_clean"])

    labels = load_labels(skills)
    skill_info = skills.set_index("conceptUri")
    skill_info["skill"] = skill_info["preferredLabel"].str.strip().str.lower()
    searchable = set(labels.loc[labels["name"].str.split().str.len() <= MAX_SEARCH_WORDS,
                                "skill_uri"])

    texts = [
        NOT_LANGUAGE.sub(" ", t)
        for t in health["description_clean"].fillna("").str.lower()
    ]
    health["skills"] = match_skills(texts, labels)

    # Programme x skill counts.
    mentions = (
        health[["programme", "skills"]].explode("skills").dropna()
        .rename(columns={"skills": "skill_uri"})
        .groupby(["programme", "skill_uri"]).size().rename("n_postings").reset_index()
    )
    profile = esco_profiles(occupations, relations)
    table = profile.merge(mentions, on=["programme", "skill_uri"], how="outer")
    table["esco_relation"] = table["esco_relation"].fillna("none")
    table["n_postings"] = table["n_postings"].fillna(0).astype(int)
    table["skill"] = table["skill_uri"].map(skill_info["skill"])
    table["skill_type"] = table["skill_uri"].map(skill_info["skillType"])
    table["reuse_level"] = table["skill_uri"].map(skill_info["reuseLevel"])
    table["searchable"] = table["skill_uri"].isin(searchable)

    # A few different ESCO skills share the same Norwegian label. Keep one row per
    # label: the one in the profile if any, otherwise the most mentioned.
    table["_rank"] = table["esco_relation"].map(RELATION_ORDER)
    table = (
        table.sort_values(["_rank", "n_postings"], ascending=[True, False], kind="stable")
        .drop_duplicates(["programme", "skill"])
        .drop(columns="_rank")
    )

    n_prog = health.groupby("programme").size()
    table["share_postings"] = (table["n_postings"] / table["programme"].map(n_prog)).round(3)
    table = table.sort_values(["programme", "n_postings", "skill"], ascending=[True, False, True])
    table = table[[
        "programme", "skill", "skill_type", "reuse_level", "esco_relation",
        "searchable", "n_postings", "share_postings", "skill_uri",
    ]]
    table.to_csv(SKILLS_PATH, index=False)

    # How much of each ESCO profile do employers mention? Only skills with a
    # short enough name to be found by exact matching count.
    in_profile = table[table["esco_relation"] != "none"]
    in_search = in_profile[in_profile["searchable"]]
    coverage = pd.DataFrame({
        "profile_skills": in_profile.groupby("programme").size(),
        "profile_skills_searchable": in_search.groupby("programme").size(),
        "profile_skills_mentioned": in_search.groupby("programme")["n_postings"].apply(
            lambda s: (s > 0).sum()
        ),
    })
    coverage["share_searchable_mentioned"] = (
        coverage["profile_skills_mentioned"] / coverage["profile_skills_searchable"]
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
    coverage = coverage.fillna(0)
    count_cols = [
        "profile_skills", "profile_skills_searchable", "profile_skills_mentioned",
        "other_skills_mentioned", "postings", "postings_with_any_skill",
    ]
    coverage[count_cols] = coverage[count_cols].astype(int)
    coverage = coverage.rename_axis("programme").reset_index()
    coverage = coverage.sort_values("postings", ascending=False)
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
