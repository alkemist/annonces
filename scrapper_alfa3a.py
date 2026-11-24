import json
import re
from datetime import datetime
from html import unescape
from urllib.parse import quote

from bs4 import BeautifulSoup

from scrapper import PageBlockedError, Scrapper
from utils import clean_city, clean_str, extract_furnished_info


class ScrapperAlfa3a(Scrapper):
    """Importe l'annuaire des residences, sans disponibilite confirmee."""

    uses_link_pagination = True
    residence_types = {
        "residence": "Résidence",
        "residence-jeunes": "Résidence jeunes",
        "pension-de-famille": "Pension de famille",
    }

    def __init__(self, annonce_type: str):
        if annonce_type != "location":
            raise ValueError("L'annuaire Alfa3a concerne uniquement le logement locatif.")
        super().__init__(
            "alfa3a",
            base_url="https://www.alfa3a.org/",
            url_template="se-loger/",
            parameters={"annonce_type": {"location": ["se-loger"]}},
            annonce_type=annonce_type,
        )

    def detect_captcha(self) -> bool:
        soup = BeautifulSoup(self.page.content(), "html.parser")
        return (
            soup.select_one("#challenge-form, #challenge-running") is not None
            or bool(re.search(
                r"just a moment|attention required|vérification de sécurité",
                soup.title.get_text() if soup.title else "", re.IGNORECASE,
            ))
        )

    def ensure_page_ready(self):
        super().ensure_page_ready()
        if self.detect_captcha():
            raise PageBlockedError(f"[{self.type}] Page bloquée : {self.page.url}")

    def scrap(self, el):
        soup = BeautifulSoup(el.content(), "html.parser")
        assignment = re.compile(r"\bwindow\.EME_POINTS\s*=\s*")
        script = next(
            (script.get_text() for script in soup.select("script:not([src])")
             if assignment.search(script.get_text())),
            None,
        )
        if script is None:
            raise ValueError(f"Annuaire Alfa3a introuvable : {el.url}")
        match = assignment.search(script)
        points, _ = json.JSONDecoder().raw_decode(script[match.end():].lstrip())
        if not isinstance(points, list):
            raise ValueError("Format de l'annuaire Alfa3a invalide : liste attendue.")

        annonces = []
        seen = set()
        for point in points:
            if not isinstance(point, dict):
                raise ValueError("Fiche Alfa3a invalide : objet attendu.")
            residence_type = point.get("type")
            if residence_type not in self.residence_types:
                continue
            titre = clean_str(unescape(point.get("title") or ""))
            address = clean_str(unescape(point.get("adress") or ""))
            if not titre or not address:
                raise ValueError(f"Fiche Alfa3a sans titre ou adresse : {point!r}")
            city_match = re.search(r"\b(\d{5})\s+(.+)$", address)
            if city_match is None:
                raise ValueError(f"Code postal ou ville Alfa3a introuvable : {address}")

            description = clean_str(BeautifulSoup(
                point.get("description") or "", "html.parser",
            ).get_text(" ", strip=True))
            # Un lien texte distingue les residences partageant la meme page annuaire.
            url = self.base_url + "se-loger/#:~:text=" + quote(titre, safe="")
            if url in seen:
                continue
            seen.add(url)
            annonces.append({
                "site": self.type,
                "titre": titre,
                "ville": clean_city(f"{city_match.group(2)} {city_match.group(1)}"),
                "type": self.residence_types[residence_type],
                "meuble": extract_furnished_info([description]),
                "surface": None,
                "pieces": None,
                "loyer": None,
                "charges": None,
                "loyer_total": None,
                "depot": None,
                "frais_agence": None,
                "étage": None,
                "ascenseur": None,
                "chambres": None,
                "salles_de_bain": None,
                "balcon": None,
                "chauffage_type": None,
                "chauffage_mode": None,
                "parking": None,
                "classe": None,
                "étages_total": None,
                "année": None,
                "description": f"{description or ''}".strip(),
                "caracteristiques": [f"Adresse : {address}"],
                "annonceur": "Alfa3a",
                "type_annonceur": "Professionnel",
                "url": url,
                "date_annonce": None,
                "date_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
        return annonces
