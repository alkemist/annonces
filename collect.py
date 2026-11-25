import streamlit as st
import math
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

import pandas as pd

from scrapper_leboncoin import ScrapperLeboncoin
from scrapper_pap import ScrapperPap
from scrapper_bienici import ScrapperBienici
from scrapper_seloger import ScrapperSeloger
from scrapper_paruvendu import ScrapperParuvendu
from scrapper_dynacite import ScrapperDynacite
from scrapper_semcoda import ScrapperSemcoda
from scrapper_alfa3a import ScrapperAlfa3a
from scrapper_cdchabitat import ScrapperCdcHabitat
from utils import bool_or_none, clean_city, normalize_municipality, validate_base_location

OUTPUT_DIR = Path(__file__).resolve().parent
SEARCHES = [
    {"file_key": "neuville", "code_postal": "01400", "max_distance": 20, "base_location": (46.1546351,5.0019543)},
    {"file_key": "tournus", "code_postal": "71700", "max_distance": 20, "base_location": (46.5679604,4.8612031)},
]


def build_configs(code_postal: str) -> list[dict]:
    """Configure les scrapers sans ouvrir de session Playwright."""
    pap_codes = {"71700": "71700-g36740", "01400": "01400-g7099"}
    paruvendu_codes = {"71700": "71543", "01400": "01272"}
    return [
        {"scrapper": ScrapperLeboncoin, "code_postaux": {code_postal: code_postal}},
        {"scrapper": ScrapperPap, "code_postaux": {code_postal: pap_codes[code_postal]}},
        {"scrapper": ScrapperBienici, "code_postaux": {code_postal: code_postal}},
        {"scrapper": ScrapperSeloger, "code_postaux": {code_postal: code_postal}},
        {"scrapper": ScrapperParuvendu, "code_postaux": {code_postal: paruvendu_codes[code_postal]}},
        {"scrapper": ScrapperDynacite, "code_postaux": {code_postal: code_postal}},
        {"scrapper": ScrapperSemcoda, "code_postaux": {code_postal: code_postal}},
        {"scrapper": ScrapperAlfa3a, "code_postaux": {code_postal: code_postal}},
        {"scrapper": ScrapperCdcHabitat, "code_postaux": {code_postal: code_postal}},
    ]

TARGET_COLUMNS = [
    "site", "titre", "ville", "distance", "meuble", "type", "surface", "pieces", "loyer_total", "loyer",
    "charges", "depot", "frais_agence", "étage", "ascenseur",
    "chambres", "salles_de_bain", "balcon", "chauffage_type", "chauffage_mode",
    "parking", "classe", "étages_total", "année", "caracteristiques", "description",
    "annonceur", "type_annonceur", "url",
    "date_annonce", "date_update"
]

INTEGER_COLUMNS = ("pieces", "étage", "chambres", "salles_de_bain", "étages_total", "année", "surface", "loyer", "charges", "loyer_total", "depot", "frais_agence")
BOOLEAN_COLUMNS = ("ascenseur", "balcon", "parking")

def unify_city_names(values: pd.Series) -> pd.Series:
    """Unifie les variantes d'une même ville et d'un même code postal."""
    names = values.map(clean_city)
    preferred = {}

    def preference(name: str) -> tuple:
        words = re.findall(r"[^\W\d_]+", name.casefold())
        expanded_saints = sum(word in {"saint", "sainte"} for word in words)
        accents = sum(unicodedata.combining(char) != 0 for char in unicodedata.normalize("NFD", name))
        return expanded_saints, accents, name.count("-"), name

    for name in names.dropna():
        key = normalize_municipality(name)
        if key not in preferred or preference(name) > preference(preferred[key]):
            preferred[key] = name

    return names.map(lambda name: preferred[normalize_municipality(name)], na_action="ignore")

def run_search(
        file_key: str, code_postal: str, max_distance: float | None = None, *,
        base_location: tuple[float, float],
) -> Path:
    """Filtre les distances connues en km ; None desactive la limite."""
    base_location = validate_base_location(base_location)
    if max_distance is not None and (
        isinstance(max_distance, bool)
        or not isinstance(max_distance, (int, float))
        or not math.isfinite(max_distance)
        or max_distance < 0
    ):
        raise ValueError("max_distance doit être un nombre positif ou nul, ou None.")
    output_file = OUTPUT_DIR / f"output/annonces_{file_key}.csv"

    annonces = []
    for config in build_configs(code_postal):
        scrapper = config["scrapper"]("location")
        try:
            annonces.extend(scrapper.start(dict(config["code_postaux"]), base_location=base_location))
        finally:
            scrapper.close()
    df_existant = pd.DataFrame(annonces, columns=TARGET_COLUMNS)
    df_existant["ville"] = unify_city_names(df_existant["ville"])
    df_existant["distance"] = pd.to_numeric(df_existant["distance"], errors="raise")
    if max_distance is not None:
        distances = pd.to_numeric(df_existant["distance"], errors="raise")
        df_existant = df_existant.loc[
            distances.isna() | distances.le(max_distance)
        ]

    df_existant = df_existant.sort_values(by=['site', 'date_update'], ascending=[True, False]) \
        .drop_duplicates(subset=["site", "url"], keep="first") \
        .reset_index(drop=True)

    for column in BOOLEAN_COLUMNS:
        df_existant[column] = df_existant[column].map(bool_or_none)

    for column in INTEGER_COLUMNS:
        numeric_values = pd.to_numeric(df_existant[column], errors="coerce")
        df_existant[column] = numeric_values.map(
            lambda value: int(value) if pd.notna(value) else pd.NA
        ).astype("Int64")

    df_existant = df_existant.sort_values(
        ["distance", "loyer_total"], ascending=True, na_position="last", kind="stable",
    ).reset_index(drop=True)
    df_existant = df_existant.reindex(columns=TARGET_COLUMNS)
    df_existant.to_csv(output_file, index=False)
    return output_file



def run_collection():
    for search in SEARCHES:
        run_search(**search)
    subprocess.run(
        [sys.executable, str(OUTPUT_DIR / "export_html.py")],
        cwd=OUTPUT_DIR,
        check=True,
    )

if __name__ == "__main__":
    run_collection()
