"""
Build the supply side: study places and applicants per health programme
in Samordna opptak 2026.

Input:
    data/raw/hkdir.xlsx                     (downloaded from HK-dir)
Output:
    data/processed/supply_by_programme.csv  one row per programme

Run from the repo root:
    python src/build_supply.py
"""

import os

import pandas as pd

# --- Settings ---------------------------------------------------------------

YEAR = "2026"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HKDIR_PATH = os.path.join(ROOT, "data", "raw", "hkdir.xlsx")
SUPPLY_PATH = os.path.join(ROOT, "data", "processed", "supply_by_programme.csv")

# Key figures to keep, renamed to English.
KEY_FIGURES = {
    "Studieplasser": "study_places",
    "Alle søkere": "applicants",
    "Førstevalgssøkere": "first_choice",
    "Antall kvalifiserte": "qualified",
}

# HK-dir education type -> programme (same names as on the demand side).
EDUCATION_TYPE_TO_PROGRAMME = {
    "Helsefag - sykepleie": "Sykepleie",
    "Helsefag - medisin": "Medisin",
    "Helsefag - vernepleie": "Vernepleie",
    "Helsefag - fysioterapi": "Fysioterapi",
    "Helsefag - bioingeniør": "Bioingeniør",
    "Helsefag - ergoterapi": "Ergoterapi",
    "Helsefag - radiografi": "Radiografi / audiografi",
}

# Programmes that share an education type with other studies are matched
# on study name instead.
STUDY_NAME_TO_PROGRAMME = {
    "Odontologi": "Odontologi",
    "Tannpleie": "Tannpleie",
    "Klinisk ernæring": "Klinisk ernæring",  # UiO, integrated master
    "Ernæring": "Klinisk ernæring",          # UiB/UiT, bachelor in the 3+2 route
}

# Psychology is listed under "Samfunnsfag" together with bachelor and
# one-year programmes. The professional degree is identified by study code.
PSYCHOLOGY_CODES = {
    184941,  # UiB
    185941,  # UiO, autumn
    185942,  # UiO, spring
    186941,  # UiT
    194941,  # NTNU
    201941,  # UiA
    217941,  # UiS
}


# --- Programme mapping ----------------------------------------------------------

def assign_programme(row):
    """Return the programme for a study, or None if it is out of scope."""
    if row["study_code"] in PSYCHOLOGY_CODES:
        return "Psykologi (profesjonsstudium)"
    if row["study_name"] in STUDY_NAME_TO_PROGRAMME:
        return STUDY_NAME_TO_PROGRAMME[row["study_name"]]
    return EDUCATION_TYPE_TO_PROGRAMME.get(row["education_type"])


# --- Main -------------------------------------------------------------------------

def main():
    df = pd.read_excel(HKDIR_PATH)
    df.columns = [str(c).replace("- ", "").strip() for c in df.columns]
    df = df.rename(columns={
        "Institusjon": "institution",
        "Studiekode": "study_code",
        "Studienavn": "study_name",
        "Utdanningstype": "education_type",
        "Nøkkeltall": "key_figure",
    })

    # Long -> wide: one row per study, one column per key figure.
    df = df[df["key_figure"].isin(KEY_FIGURES)]
    df[YEAR] = pd.to_numeric(df[YEAR], errors="coerce")
    studies = (
        df.pivot_table(
            index=["institution", "study_code", "study_name", "education_type"],
            columns="key_figure", values=YEAR, aggfunc="sum",
        )
        .rename(columns=KEY_FIGURES)
        .reset_index()
    )

    studies["programme"] = studies.apply(assign_programme, axis=1)
    studies = studies[studies["programme"].notna() & (studies["study_places"] > 0)]

    supply = (
        studies.groupby("programme")
        .agg(
            n_studies=("study_code", "count"),
            study_places=("study_places", "sum"),
            applicants=("applicants", "sum"),
            first_choice=("first_choice", "sum"),
            qualified=("qualified", "sum"),
        )
        .astype(int)
        .sort_values("study_places", ascending=False)
        .reset_index()
    )
    supply["first_choice_per_place"] = (supply["first_choice"] / supply["study_places"]).round(2)
    supply.to_csv(SUPPLY_PATH, index=False)

    print(f"Health studies in {YEAR}: {len(studies)}\n")
    print(supply.to_string(index=False))
    print(f"\nSaved {SUPPLY_PATH}")


if __name__ == "__main__":
    main()
