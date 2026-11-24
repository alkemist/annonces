import re
from datetime import datetime
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from scrapper import PageBlockedError, Scrapper
from utils import (
    TIMEOUT_MAX,
    clean_city,
    clean_float,
    clean_int,
    clean_str,
    extract_balcony_info,
    extract_elevator_info,
    extract_energy_class_info,
    extract_furnished_info,
    extract_heating_type,
    extract_parking_info,
    extract_property_type_info,
    extract_rental_amounts,
    extract_rooms_info,
    extract_surface_info,
    filter_keywords,
    filter_property_type,
)


class ScrapperSemcoda(Scrapper):
    """Collecte toutes les locations de l'Ain, sans filtre par code postal."""

    uses_link_pagination = True

    def __init__(self, annonce_type: str):
        if annonce_type != "location":
            raise ValueError("La page Semcoda Ain ne propose que des locations.")
        super().__init__(
            "semcoda",
            base_url="https://www.semcoda.com/",
            url_template="residences-ain-louer.html",
            parameters={"annonce_type": {"location": ["louer"]}},
            annonce_type=annonce_type,
        )

    def detect_captcha(self) -> bool:
        soup = BeautifulSoup(self.page.content(), "html.parser")
        return (
            soup.select_one("#challenge-form, #challenge-running") is not None
            or bool(re.search(
                r"just a moment|attention required|vérification de sécurité",
                soup.title.get_text() if soup.title else "",
                re.IGNORECASE,
            ))
        )

    def ensure_page_ready(self):
        super().ensure_page_ready()
        if self.detect_captcha():
            raise PageBlockedError(f"[{self.type}] Page bloquée : {self.page.url}")

    def get_next_page_url(self) -> str | None:
        soup = BeautifulSoup(self.page.content(), "html.parser")
        link = soup.select_one(".pager__item--next a, .pager-next a, a[rel='next']")
        if link is None:
            return None
        url = urljoin(self.page.url, link.get("href", ""))
        parsed = urlsplit(url)
        if (
            parsed.netloc != urlsplit(self.base_url).netloc
            or parsed.path != "/residences-ain-louer.html"
        ):
            raise ValueError(f"Lien de pagination Semcoda invalide : {url}")
        return url

    def _extract_annonce(self, soup, url: str) -> dict | None:
        presentation = soup.select_one("#presentation")
        if presentation is None or presentation.select_one("h1") is None:
            raise ValueError(f"Présentation de l'annonce Semcoda introuvable : {url}")

        def text(selector):
            element = presentation.select_one(selector)
            return clean_str(element.get_text(" ", strip=True)) if element else None

        titre = text("h1")
        description_el = presentation.select_one(".description")
        paragraphs = description_el.find_all("p") if description_el else []
        description = clean_str(" ".join(
            paragraph.get_text(" ", strip=True) for paragraph in paragraphs
        )) or None
        type_match = re.search(
            r"\b(appartement|studio|maison|villa|pavillon|terrain|parking|garage|"
            r"stationnement|local|loft|chambre)\b", titre, re.IGNORECASE,
        )
        raw_type = type_match.group(1).casefold() if type_match else None
        bien_type = extract_property_type_info({
            "studio": "Appartement", "villa": "Maison", "pavillon": "Maison",
            "stationnement": "Parking", "local": "Autre",
        }.get(raw_type, raw_type))
        if not filter_property_type(bien_type) or not filter_keywords(titre, description):
            return None

        # Conserver m2 d'un seul tenant lorsque le 2 est dans une balise sup.
        caracteristiques = [
            clean_str(cell.get_text("", strip=True))
            for cell in presentation.select("table td")
            if re.search(r"superficie|réf\.", cell.get_text(), re.IGNORECASE)
        ]
        floor_rooms = text(".etage-piece")
        if floor_rooms:
            caracteristiques.append(floor_rooms)
        values = caracteristiques + [titre, description]
        number = r"\d+(?:[ \u00a0\u202f]\d{3})*(?:[.,]\d+)?"

        def amount(pattern):
            match = re.search(pattern, description or "", re.IGNORECASE)
            return clean_float(match.group(1)) if match else None

        rent = amount(rf"\bloyer\s*:\s*({number})\s*€\s*(?:/\s*mois\s*)?hors charges")
        charges = amount(
            rf"\b({number})\s*€\s*(?:de\s+)?provision\s+(?:pour|sur)\s+charges",
        )
        price_text = text(".prix-vert-achat")
        amounts = extract_rental_amounts(
            [rent, description], [charges, description], [price_text, description],
        )
        floor_match = re.search(r"étage\s*:\s*(\d+)", floor_rooms or "", re.IGNORECASE)
        floor = clean_int(floor_match.group(1)) if floor_match else None
        if re.search(r"\b(?:rdc|rez[- ]de[- ]chaussée)\b", floor_rooms or "", re.IGNORECASE):
            floor = 0
        rooms_match = re.search(r"nombre de pièces\s*:\s*(\d+)", floor_rooms or "", re.IGNORECASE)
        rooms = clean_int(rooms_match.group(1)) if rooms_match else extract_rooms_info([titre])
        bedrooms = re.search(r"\b(\d+|une|deux|trois|quatre|cinq)\s+chambres?\b", description or "", re.IGNORECASE)
        bedroom_count = None
        if bedrooms:
            value = bedrooms.group(1).casefold()
            bedroom_count = {
                "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5,
            }.get(value, clean_int(value))
        heating = extract_heating_type(description or "")
        city_text = text(".adresse strong")
        city_match = re.fullmatch(r"(\d{5})\s+(.+)", city_text or "")
        city = f"{city_match.group(2)} {city_match.group(1)}" if city_match else city_text
        free_agency = re.search(
            r"sans frais d['’]agence|pas de frais de dossier ou d['’]agence",
            description or "", re.IGNORECASE,
        )
        diagnostics = [
            element.get_text(" ", strip=True)
            for element in presentation.select(".diagnostic span")
            if "DPE" in element.get_text()
        ]
        return {
            "site": self.type,
            "titre": titre,
            "ville": clean_city(city),
            "type": bien_type,
            "meuble": extract_furnished_info([titre, description]),
            "surface": extract_surface_info(caracteristiques + [description, titre]),
            "pieces": rooms,
            **amounts,
            "depot": amount(rf"dépôt de garantie\s*:?\s*({number})\s*€"),
            "frais_agence": 0 if free_agency else None,
            "étage": floor,
            "ascenseur": extract_elevator_info([description]),
            "chambres": bedroom_count,
            "salles_de_bain": None,
            "balcon": extract_balcony_info([description]),
            "chauffage_type": heating["type"],
            "chauffage_mode": heating["mode"],
            "parking": extract_parking_info([description]),
            "classe": extract_energy_class_info(diagnostics + [description]),
            "étages_total": None,
            "année": None,
            "description": description,
            "caracteristiques": caracteristiques,
            "annonceur": "SEMCODA",
            "type_annonceur": "Professionnel",
            "url": url,
            "date_annonce": None,
            "date_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def scrap(self, el):
        soup = BeautifulSoup(el.content(), "html.parser")
        results = soup.select_one(".offre-detail .liste")
        if results is None:
            raise ValueError(f"Résultats Semcoda introuvables : {el.url}")
        links = []
        for card in results.select(".prog"):
            link = card.select_one("a[href]")
            url = urljoin(self.base_url, link["href"]) if link else ""
            if (
                urlsplit(url).netloc != urlsplit(self.base_url).netloc
                or not re.fullmatch(r"/etablissement-[^/]+\.html", urlsplit(url).path)
            ):
                raise ValueError(f"Lien d'annonce Semcoda invalide : {url!r}")
            if url not in links:
                links.append(url)

        annonces = []
        for url in links:
            el.wait_for_timeout(1000)
            response = self.navigate(el, url, wait_until="domcontentloaded", timeout=TIMEOUT_MAX)
            self.ensure_page_ready()
            if response is None or response.status >= 400:
                raise RuntimeError(f"[{self.type}] Impossible de charger l'annonce : {url}")
            annonce = self._extract_annonce(BeautifulSoup(el.content(), "html.parser"), url)
            if annonce is not None:
                annonces.append(annonce)
        return annonces
