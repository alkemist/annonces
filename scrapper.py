import json
import math
import os
import re
import sys
import unicodedata
import urllib.parse
from pathlib import Path

import pandas as pd
from curl_cffi import requests
from playwright.sync_api import sync_playwright

ANNONCES_MAX = 100
TIMEOUT_MAX = 30000

class PageBlockedError(RuntimeError):
    pass

def clean_bool(val, expected_val) -> bool | None:
    if isinstance(val, str):
        return val.strip().casefold() == expected_val.strip().casefold()
    return None

def bool_or_none(value):
    if pd.isna(value):
        return None
    if isinstance(value, str):
        normalized_value = value.strip().casefold()
        if normalized_value in {"true", "oui", "yes", "vrai"}:
            return True
        if normalized_value in {"false", "non", "no", "faux"}:
            return False

    numeric_value = pd.to_numeric(value, errors="coerce")
    return bool(numeric_value > 0) if pd.notna(numeric_value) else None

def clean_str(val) -> str | None:
    if val is None:
        return None
    if not isinstance(val, str):
        val = str(val)

    return re.sub(r"\s+", " ", val.strip())

def clean_city(val: str | None) -> str | None:
    value = clean_str(val)
    if value is None:
        return None
    if "," in value:
        value = value.split(",", 1)[1]
    return clean_str(
        value.replace("(", "")\
            .replace(")", "")\
            .replace(" Rive Gauche", "")
    ) or None

def extract_property_type_info(value: str | None) -> str | None:
    value = clean_str(value)
    if not value:
        return None
    property_types = {
        "flat": "Appartement", "apartment": "Appartement", "appartement": "Appartement", "duplex/triplex": "Appartement", "duplex": "Appartement", "triplex": "Appartement",
        "house": "Maison", "maison": "Maison", "townhouse": "Maison",
        "land": "Terrain", "terrain": "Terrain",
        "parking": "Parking", "garage": "Parking",
        "other": "Autre", "autre": "Autre",
        "loft": "Loft", "castle": "Château", "château": "Château",
        "chambre": "Chambre"
    }
    return property_types.get(value.casefold(), value)

def filter_property_type(value: str | None) -> bool:
    return extract_property_type_info(value) not in {"Terrain", "Parking", "Autre", "Loft", "Chambre", ""}

def clean_int(val) -> int | None:
    """Convertit n'importe quelle valeur textuelle ou brute en int."""
    res = clean_float(val)
    return int(res) if res is not None else None

def get_coordinates(code_postal: str) -> tuple[float, float]:
    url = f"https://geo.api.gouv.fr/communes?codePostal={code_postal}&fields=centre"
    res = requests.get(url, timeout=10)
    print(f"[Geo API] GET {res.url}")
    res.raise_for_status()
    data = res.json()

    if not data or "centre" not in data[0]:
        raise ValueError(f"Code postal {code_postal} introuvable.")

    lon, lat = data[0]["centre"]["coordinates"]
    return lat, lon

def clean_float(val) -> float | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)

    clean_str = str(val).replace("\xa0", "").replace(" ", "").replace(",", ".")
    match = re.search(r"\d+(?:\.\d+)?", clean_str)
    return float(match.group(0)) if match else None

def filter_keywords(title: str | None, *descriptions: str | None) -> bool:
    def normalize(value: str | None) -> str:
        value = unicodedata.normalize("NFKD", clean_str(value) or "").casefold()
        return "".join(char for char in value if not unicodedata.combining(char))

    title = normalize(title)
    if re.search(r"\bcolocations?\b", title) or re.match(r"chambres?\b", title):
        return False

    room = r"chambres?"
    qualifiers = r"(?:(?:une?|la|les|des|plusieurs|\d+|deux|trois|quatre|cinq|six)\s+)?"
    patterns = [
        rf"\b(?:loue|louons|louer|location(?:\s+(?:de|d['’]une))?|sous[- ]location\s+de)\s+{qualifiers}{room}\b",
        rf"\b{room}(?:\s+(?:meublee?s?|individuelle?s?|privee?s?|etudiante?s?|disponibles?))*"
        r"\s+(?:a\s+louer|en\s+(?:location|colocation)|chez\s+l['’]habitant)\b",
        rf"\b(?:location|loyer|prix|tarif|bail)\s+(?:par|a\s+la|pour\s+(?:une|chaque))\s+chambre\b",
        r"\b(?:chambres?\s+d['’]hotes?|coliving)\b",
    ]
    return not any(
        re.search(pattern, text)
        for text in [title] + [normalize(value) for value in descriptions]
        for pattern in patterns
    )

def extract_heating_info(description: str) -> dict:
    match = re.search(r"([^.\n]*chauffage[^.\n]*)", description, re.IGNORECASE)

    if not match:
        return {
            "mode": None,
            "type": None
        }
    sentence = match.group(1).strip()
    return extract_heating_type(sentence)

def extract_heating_type(sentence: str) -> dict:
    sentence_lower = sentence.lower()

    if "individuel" in sentence_lower:
        chauffage_mode = "Individuel"
    elif "collectif" in sentence_lower:
        chauffage_mode = "Collectif"
    else:
        chauffage_mode = None

    energies = {
        "Électrique": [r"électrique", r"électriques"],
        "Gaz": [r"\bgaz\b"],
        "Fioul": [r"fioul", r"masout"],
        "Pompe à chaleur": [r"pompe à chaleur", r"\bpac\b", r"aerothermie", r"geothermie"],
        "Bois / Pellets": [r"bois", r"pellet", r"granulé"],
        "Réseau de chaleur": [r"urbain", r"réseau de chaleur"],
        "Sol": [r"au sol"],
    }

    chauffage_type = None
    for energy_label, patterns in energies.items():
        if any(re.search(pattern, sentence_lower) for pattern in patterns):
            chauffage_type = energy_label
            break

    return {
        "mode": chauffage_mode,
        "type": chauffage_type
    }

def extract_balcony_info(values: list[str | int | float | None]) -> bool | None:
    return _extract_amenity_info(values, r"balcons?|terrasses?", "balcons ou terrasses")

def extract_parking_info(values: list[str | int | float | None]) -> bool | None:
    return _extract_amenity_info(values, r"parkings?|garages?|stationnements?", "parkings")

def extract_elevator_info(values: list[str | int | float | None]) -> bool | None:
    return _extract_amenity_info(values, r"ascenseurs?", "ascenseurs")

_NUMBER_PATTERN = r"\d+(?:[ \u00a0\u202f]\d{3})*(?:[.,]\d+)?"

def _extract_number_info(
    values: list[str | int | float | None], patterns: list[str],
    allow_zero: bool = False, integer: bool = False,
) -> int | float | None:
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            number = float(value)
        else:
            text = clean_str(value)
            match = re.fullmatch(rf"({_NUMBER_PATTERN})", text)
            if match is None:
                match = next(
                    (match for pattern in patterns
                     if (match := re.search(pattern, text, re.IGNORECASE))),
                    None,
                )
            if match is None:
                continue
            number = clean_float(match.group(1))
        if number is None or not math.isfinite(number) or number < 0:
            continue
        if number == 0 and not allow_zero:
            continue
        if integer and not number.is_integer():
            continue
        return int(number) if number.is_integer() else number
    return None

def extract_surface_info(values: list[str | int | float | None]) -> int | float | None:
    return _extract_number_info(values, [
        rf"\b({_NUMBER_PATTERN})\s*(?:m²|m2|m\s*\^2|mètres?\s+carrés?)\b",
        rf"\bsurface(?:\s+habitable)?\s*[:=]?\s*({_NUMBER_PATTERN})\b",
    ])

def extract_rooms_info(values: list[str | int | float | None]) -> int | None:
    number = _extract_number_info(values, [
        rf"\b({_NUMBER_PATTERN})\s*pièces?\b",
        r"\b(?:T|F)\s*([1-9]\d*)\b",
    ], integer=True)
    return int(number) if number is not None else None

def extract_rent_info(values: list[str | int | float | None]) -> int | float | None:
    return _extract_number_info(values, [
        rf"^\s*({_NUMBER_PATTERN})\s*€(?:\s*/\s*mois)?\s*$",
        rf"\b({_NUMBER_PATTERN})\s*€?\s*(?:HC\b|hors\s+charges\b)",
        rf"\bloyer(?:\s+(?:mensuel|hors\s+charges|HC))?\s*[:=]?\s*"
        rf"({_NUMBER_PATTERN})(?![\d.,])(?!\s*€?\s*(?:CC\b|charges\s+comprises\b))",
    ])

def extract_charges_info(values: list[str | int | float | None]) -> int | float | None:
    return _extract_number_info(values, [
        rf"^\s*({_NUMBER_PATTERN})\s*€(?:\s*/\s*mois)?\s*$",
        rf"\b(?:charges(?:\s+(?:mensuelles|forfaitaires))?|provision\s+sur\s+charges)"
        rf"\s*[:=]?\s*({_NUMBER_PATTERN})\s*€",
        rf"\b({_NUMBER_PATTERN})\s*€\s*(?:de\s+|pour\s+(?:les\s+)?)charges\b",
    ], allow_zero=True)

def extract_total_rent_info(values: list[str | int | float | None]) -> int | float | None:
    return _extract_number_info(values, [
        rf"^\s*({_NUMBER_PATTERN})\s*€(?:\s*/\s*mois)?\s*$",
        rf"\b({_NUMBER_PATTERN})\s*€?\s*(?:CC\b|charges\s+comprises\b)",
        rf"\bloyer\s+(?:total|charges\s+comprises|CC)\s*[:=]?\s*({_NUMBER_PATTERN})\b",
    ])

def extract_rental_amounts(
    rent_values: list[str | int | float | None],
    charges_values: list[str | int | float | None],
    total_values: list[str | int | float | None],
) -> dict:
    rent = extract_rent_info(rent_values)
    charges = extract_charges_info(charges_values)
    total = extract_total_rent_info(total_values)
    if rent is not None and charges is not None:
        total = round(rent + charges, 2)
    elif rent is None and total is not None and charges is not None:
        if total >= charges:
            rent = round(total - charges, 2)
    elif total is None and rent is not None:
        total = rent
    return {"loyer": rent, "charges": charges, "loyer_total": total}

def extract_energy_class_info(values: list[str | None]) -> str | None:
    for value in values:
        value = clean_str(value)
        if not value:
            continue

        normalized_value = value.upper()
        if normalized_value in {"NS", "NON SOUMIS AU DPE", "VIERGE"}:
            continue
        if re.fullmatch(r"[A-G]", normalized_value):
            return normalized_value

        match = re.search(
            r"(?i:\b(?:DPE|classe(?:\s+(?:énergétique|énergie))?)\s*[:=-]?\s*)"
            r"([A-G])\b",
            value,
        )
        if match:
            return match.group(1)

    return None

def _extract_amenity_info(
    values: list[str | int | float | None], terms: str, label: str,
) -> bool | None:
    amenity_pattern = rf"\b(?:{terms})\b"
    absence_found = False
    for value in values:
        if value is None:
            continue

        if isinstance(value, bool):
            if value:
                return True
            absence_found = True
            continue

        if isinstance(value, (int, float)) or re.fullmatch(r"\s*\d+(?:[.,]\d+)?\s*", value):
            count = clean_int(value)
            if count is None:
                raise ValueError(f"Quantité de {label} invalide : {value!r}")
            if count > 0:
                return True
            absence_found = True
            continue

        if not re.search(amenity_pattern, value, re.IGNORECASE):
            continue

        description_without_absences = re.sub(
            rf"\b(?:sans\s+|pas\s+d['\u2019]\s*)(?:{terms})(?:\s+(?:ni|et|ou)\s+(?:{terms}))*\b",
            "",
            value,
            flags=re.IGNORECASE,
        )
        if re.search(amenity_pattern, description_without_absences, re.IGNORECASE):
            return True
        absence_found = True

    return False if absence_found else None

class Scrapper:
    uses_link_pagination = False

    def __init__(self, type: str, base_url: str, url_template: str, parameters: dict, annonce_type: str, max_loyer: int = None):
        self.type = type
        self.base_url = base_url
        self.url_template = url_template
        self.parameters = parameters
        self.annonce_types = list(self.parameters["annonce_type"][annonce_type])
        self.page_step = 100
        self.max_loyer = max_loyer
        self.headless = (
            sys.platform.startswith("linux")
            and not os.environ.get("DISPLAY")
            and not os.environ.get("WAYLAND_DISPLAY")
        )

        path = Path(f"profiles/{self.type}").resolve()
        path.mkdir(parents=True, exist_ok=True)
        self.profil_dir = str(path)

        self.session_state_file = f"profiles/{self.type}_state.json"

    def get_parameters(self, p, code_postal: str) -> dict:
        return {}

    def build_page_url(
            self,
            base_parameters: dict,
            annonce_type: str,
            code_postal: str,
            page_num: int = 1,
    ) -> str:
        url = self.url_template
        parameters = base_parameters

        if "{code_postal}" in self.url_template:
            parameters = {**parameters, "code_postal": code_postal}
        if "{annonce_type}" in self.url_template:
            parameters = {**parameters, "annonce_type": annonce_type}

        if "{page_num}" in self.url_template:
            parameters = {**parameters, "page_num": page_num}
        if "{page_from}" in self.url_template:
            parameters = {**parameters, "page_from": (page_num-1)*self.page_step}
            if "{page_step}" in self.url_template:
                parameters = {**parameters, "page_step": self.page_step}

        for name, value in parameters.items():
            if isinstance(value, list):
                value = json.dumps(value, separators=(",", ":"))
                value = urllib.parse.quote(value, safe="")
            url = url.replace("{" + name + "}", str(value))

        return self.base_url + url

    def detect_captcha(self, page) -> bool:
        return False

    def ensure_page_ready(self, page):
        if self.detect_captcha(page):
            page.wait_for_timeout(5000)
            if self.headless and self.detect_captcha(page):
                raise PageBlockedError(
                    f"[{self.type}] CAPTCHA sur {page.url} : "
                    "une intervention manuelle est impossible en mode headless."
                )

    def get_next_page_url(self, page) -> str | None:
        return None

    def build_page(self, p):
            print(f"[{self.type}] Lancement du navigateur...")

            context = p.chromium.launch_persistent_context(
                user_data_dir=self.profil_dir,
                channel="chromium",
                headless=self.headless,
                no_viewport=True,
                locale="fr-FR",
                ignore_default_args=["--enable-automation"],
                args=["--disable-blink-features=AutomationControlled"],
            )

            page = context.pages[0] if context.pages else context.new_page()
            page.wait_for_timeout(1000)
            page.goto(self.base_url)

            if self.detect_captcha(page):
                if self.headless:
                    context.close()
                    raise PageBlockedError(
                        f"[{self.type}] CAPTCHA sur {self.base_url} : "
                        "une intervention manuelle est impossible en mode headless."
                    )
                print(f"[{self.type}] Résolvez le CAPTCHA...")
                input(f"[{self.type}] ---> Appuyez sur Entrée une fois la page affichée normalement...")

            context.storage_state(path=self.session_state_file)
            context.close()

            print(f"[{self.type}] ✓ Session enregistrée dans le profil.")

            browser = p.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled"]
            )

            context = browser.new_context(
                storage_state=self.session_state_file,
                locale="fr-FR",
            )

            return context.new_page()

    def scrap(self, page):
        raise NotImplementedError("La méthode scrap() doit être implémentée dans la classe dérivée.")

    @staticmethod
    def _annonce_key(annonce: dict) -> str:
        if annonce.get("url"):
            return str(annonce["url"])

        stable_data = {key: value for key, value in annonce.items() if key != "date_update"}
        return json.dumps(stable_data, sort_keys=True, default=str)

    def start(self, code_postaux: dict) -> list[dict]:
        code_postaux = dict(code_postaux)

        annonces = []

        with sync_playwright() as p:
            page = self.build_page(p)

            for cp_key, cp_val in code_postaux.items():
                base_parameters = self.get_parameters(page, cp_key)

                for annonce_type in self.annonce_types:
                    page_num = 1
                    search_continue = True
                    annonces_seen = set()
                    pages_seen = set()
                    url = self.build_page_url(base_parameters, annonce_type, cp_val, page_num)

                    while len(annonces) < ANNONCES_MAX and search_continue:
                        if url in pages_seen:
                            raise RuntimeError(f"[{self.type}] Boucle de pagination détectée : {url}")
                        pages_seen.add(url)
                        print(f"[{self.type}] Récupération des annonces {cp_val} page {page_num} : {url}")

                        try:
                            page.wait_for_timeout(1000)
                            response = page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MAX)
                            self.ensure_page_ready(page)
                            # Les scrapers peuvent naviguer vers les annonces individuelles.
                            next_page_url = self.get_next_page_url(page) if self.uses_link_pagination else None

                            if ".json" in url:
                                if response is None:
                                    raise RuntimeError(f"Aucune réponse reçue pour l'URL JSON : {url}")
                                scrap_input = response.json()
                            else:
                                scrap_input = page

                            annonces_new = self.scrap(scrap_input)
                            print(f"[{self.type}] {len(annonces_new)} annonces récupérées pour le code postal {cp_val} à la page {page_num}")

                            if self.uses_link_pagination:
                                annonces_added = [
                                    annonce for annonce in annonces_new
                                    if self._annonce_key(annonce) not in annonces_seen
                                ]
                                annonces.extend(annonces_added)
                                annonces_seen.update(self._annonce_key(annonce) for annonce in annonces_added)
                                if next_page_url is None:
                                    search_continue = False
                                else:
                                    url = next_page_url
                                    page_num += 1
                                continue

                            if len(annonces_new) == 0:
                                print(f"[{self.type}] ⚠ Plus d'annonce trouvée pour le code postal {cp_val} à la page {page_num}")
                                search_continue = False
                                if page_num == 1:
                                    page.wait_for_timeout(1000)
                            else:
                                annonces_added = [
                                    annonce for annonce in annonces_new
                                    if self._annonce_key(annonce) not in annonces_seen
                                ]

                                if not annonces_added:
                                    print(f"[{self.type}] ⚠ Aucune nouvelle annonce par rapport aux pages précédentes pour le code postal {cp_val} à la page {page_num}")
                                    search_continue = False
                                else:
                                    annonces.extend(annonces_added)
                                    annonces_seen.update(self._annonce_key(annonce) for annonce in annonces_added)
                                    page_num += 1
                                    url = self.build_page_url(base_parameters, annonce_type, cp_val, page_num)

                        except Exception as e:
                            print(f"[{self.type}] ⚠ Erreur lors de la récupération des annonces pour le code postal {cp_val}: {e}")
                            raise e


        print(f"[{self.type}] ✓ {len(annonces)}/{ANNONCES_MAX} annonces récupérées.\n")
        return annonces
