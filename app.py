import streamlit as st
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

from scrapper import bool_or_none
from scrapper_leboncoin import ScrapperLeboncoin
from scrapper_pap import ScrapperPap
from scrapper_bienici import ScrapperBienici
from scrapper_seloger import ScrapperSeloger
from scrapper_paruvendu import ScrapperParuvendu

OUTPUT_DIR = Path(__file__).resolve().parent
SEARCHES = [
    {"file_key": "tournus", "code_postal": "71700", "max_loyer": 650},
    {"file_key": "neuville", "code_postal": "01400", "max_loyer": None},
]


def build_configs(code_postal: str, max_loyer: int | None) -> list[dict]:
    pap_codes = {"71700": "71700-g5104", "01400": "01400-g521"}
    paruvendu_codes = {"71700": "71543", "01400": "01272"}
    return [
        {"scrapper": ScrapperLeboncoin("location", max_loyer), "code_postaux": {code_postal: code_postal}},
        {"scrapper": ScrapperPap("location", max_loyer), "code_postaux": {code_postal: pap_codes[code_postal]}},
        {"scrapper": ScrapperBienici("location", max_loyer), "code_postaux": {code_postal: code_postal}},
        {"scrapper": ScrapperSeloger("location", max_loyer), "code_postaux": {code_postal: code_postal}},
        {"scrapper": ScrapperParuvendu("location", max_loyer), "code_postaux": {code_postal: paruvendu_codes[code_postal]}},
    ]

TARGET_COLUMNS = [
    "site", "titre", "ville", "meuble", "type", "surface", "pieces", "loyer",
    "charges", "loyer_total", "depot", "frais_agence", "étage", "ascenseur",
    "chambres", "salles_de_bain", "balcon", "chauffage_type", "chauffage_mode",
    "parking", "classe", "étages_total", "année", "caracteristiques", "description", "url",
    "date_annonce", "date_update"
]

INTEGER_COLUMNS = ("pieces", "étage", "chambres", "salles_de_bain", "étages_total", "année", "surface", "loyer", "charges", "loyer_total", "depot", "frais_agence")
BOOLEAN_COLUMNS = ("ascenseur", "balcon", "parking")

def run_search(file_key: str, code_postal: str, max_loyer: int | None) -> Path:
    output_file = OUTPUT_DIR / f"annonces_{file_key}.csv"
    if output_file.exists():
        df_existant = pd.read_csv(output_file)
    else:
        df_existant = pd.DataFrame(columns=TARGET_COLUMNS)

    for config in build_configs(code_postal, max_loyer):
        annonces = config["scrapper"].start(dict(config["code_postaux"]))
        df_nouveau = pd.DataFrame(annonces, columns=TARGET_COLUMNS)
        df_existant = pd.concat([df_existant, df_nouveau], ignore_index=True)

    df_existant = df_existant.sort_values(by=['site', 'date_update'], ascending=[True, False]) \
        .drop_duplicates(subset=["site", "titre"], keep="first") \
        .reset_index(drop=True)

    for column in BOOLEAN_COLUMNS:
        df_existant[column] = df_existant[column].map(bool_or_none)

    for column in INTEGER_COLUMNS:
        numeric_values = pd.to_numeric(df_existant[column], errors="coerce")
        df_existant[column] = numeric_values.map(
            lambda value: int(value) if pd.notna(value) else pd.NA
        ).astype("Int64")

    df_existant.to_csv(output_file, index=False)
    return output_file


def main():
    for search in SEARCHES:
        run_search(**search)
    subprocess.run(
        [sys.executable, str(OUTPUT_DIR / "export_html.py")],
        cwd=OUTPUT_DIR,
        check=True,
    )

    st.title("Mon Tableau de Bord")

    with open(html_file_path, "r", encoding="utf-8") as f:
        st.components.v1.html(f.read(), scrolling=True)


if __name__ == "__main__":
    main()