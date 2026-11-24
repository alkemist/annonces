import re
from datetime import datetime
from urllib.parse import urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from scrapper import PageBlockedError, Scrapper
from utils import (
    TIMEOUT_MAX,
    clean_bool,
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


class ScrapperDynacite(Scrapper):
    """Recherche les offres Dynacite dans un rayon de 10 km du code postal."""

    uses_link_pagination = True

    def __init__(self, annonce_type: str):
        super().__init__(
            "dynacite",
            base_url="https://www.dynacite.fr/",
            url_template="{annonce_type}/?{search_parameters}",
            parameters={"annonce_type": {"location": ["louer"], "achat": ["acheter"]}},
            annonce_type=annonce_type,
        )

    def get_parameters(self, p, code_postal: str, text: str) -> dict:
        if not re.fullmatch(r"\d{5}", code_postal):
            raise ValueError(f"Code postal invalide : {code_postal!r}")
        return {
            "search_parameters": urlencode({
                "address[]": code_postal,
                "distance": 10,
                # Le formulaire du site charge toutes les offres sans pagination.
                "per_page": 9999,
                "form": 1 if self.annonce_types == ["louer"] else 2,
                "action": "fs",
            }),
        }

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

    def _extract_annonce(self, soup, url: str) -> dict | None:
        def text(selector):
            element = soup.select_one(selector)
            return clean_str(element.get_text(" ", strip=True)) if element else None

        titre = text("main.offres h1")
        if not titre:
            raise ValueError(f"Titre de l'annonce introuvable : {url}")
        description_heading = soup.find(
            "h3", string=re.compile(r"^\s*Description\s*$", re.IGNORECASE),
        )
        description_el = description_heading.find_next_sibling("p") if description_heading else None
        description = clean_str(description_el.get_text(" ", strip=True)) if description_el else None

        type_match = re.search(
            r"\b(appartement|studio|maison|villa|pavillon|terrain|parking|garage|"
            r"stationnement|local|loft|chambre)\b",
            titre, re.IGNORECASE,
        )
        raw_type = type_match.group(1).casefold() if type_match else None
        bien_type = extract_property_type_info({
            "studio": "Appartement", "villa": "Maison", "pavillon": "Maison",
            "stationnement": "Parking", "local": "Autre",
        }.get(raw_type, raw_type))
        if not filter_property_type(bien_type) or not filter_keywords(titre, description):
            return None

        fields = {}
        caracteristiques = []
        for block in soup.select(".type-caract"):
            label, value = block.find("h4"), block.find("span")
            if label and value:
                label_text = clean_str(label.get_text(" ", strip=True))
                value_text = clean_str(value.get_text(" ", strip=True))
                fields[label_text.casefold()] = value_text
                caracteristiques.append(f"{label_text} : {value_text}")

        icon_values = {}
        for icon in soup.select("main.offres img[alt]"):
            if icon.get("alt") in {"Type", "Surface", "Étage"}:
                value = icon.find_next("span")
                if value:
                    icon_values[icon["alt"]] = clean_str(value.get_text(" ", strip=True))
        caracteristiques.extend(icon_values.values())
        values = caracteristiques + [titre, description]

        price_el = next(
            (heading for heading in soup.select("main.offres h4")
             if re.search(r"loyer mensuel|prix.*vente", heading.get_text(), re.IGNORECASE)),
            None,
        )
        price = price_el.find("span") if price_el else None
        total = clean_float(price.get_text(" ", strip=True)) if price else None
        amounts = extract_rental_amounts(
            [description], [fields.get("charges"), description], [total, description],
        )
        heating = extract_heating_type(fields.get("mode de chauffage") or description or "")

        def amenity(label, extractor):
            value = fields.get(label)
            if value and value.casefold() in {"oui", "non"}:
                return clean_bool(value, "oui")
            return extractor([value, description])

        balcony = amenity("balcon", extract_balcony_info)
        terrace = amenity("terrasse", extract_balcony_info)
        balcony_values = [value for value in (balcony, terrace) if value is not None]
        floor_text = icon_values.get("Étage") or ""
        floor = clean_int(floor_text)
        if re.search(r"\b(?:rdc|rez[- ]de[- ]chaussée)\b", floor_text, re.IGNORECASE):
            floor = 0
        bedrooms = re.search(r"\b(\d+)\s+chambres?\b", description or "", re.IGNORECASE)
        city = text(".title-cp-ville")
        city = re.sub(r"\s+-\s+(?=\d{5}\b)", " ", city) if city else None

        return {
            "site": self.type,
            "titre": titre,
            "ville": clean_city(city),
            "type": bien_type,
            "meuble": extract_furnished_info([titre, description]),
            "surface": extract_surface_info([icon_values.get("Surface")] + values),
            "pieces": extract_rooms_info([icon_values.get("Type")] + values),
            **amounts,
            "depot": None,
            "frais_agence": None,
            "étage": floor,
            "ascenseur": amenity("ascenseur", extract_elevator_info),
            "chambres": clean_int(bedrooms.group(1)) if bedrooms else None,
            "salles_de_bain": None,
            "balcon": any(balcony_values) if balcony_values else None,
            "chauffage_type": heating["type"],
            "chauffage_mode": heating["mode"],
            "parking": amenity("garage / parking", extract_parking_info),
            "classe": extract_energy_class_info([text(".dpe .lettre"), description]),
            "étages_total": None,
            "année": None,
            "description": description,
            "caracteristiques": caracteristiques,
            "annonceur": "Dynacité",
            "type_annonceur": "Professionnel",
            "url": url,
            "date_annonce": None,
            "date_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def scrap(self, el):
        soup = BeautifulSoup(el.content(), "html.parser")
        results = soup.select_one(".gmw-results-wrapper")
        if results is None:
            raise ValueError(f"Résultats Dynacite introuvables : {el.url}")
        cards = results.select(".gmw-single-item")
        if not cards and results.select_one(".gmw-no-results") is None:
            raise ValueError(f"Structure des résultats Dynacite inconnue : {el.url}")

        links = []
        for card in cards:
            link = card.select_one("a[href]")
            url = urljoin(self.base_url, link["href"]) if link else ""
            if (
                urlsplit(url).netloc != urlsplit(self.base_url).netloc
                or not urlsplit(url).path.startswith("/les-offres/")
            ):
                raise ValueError(f"Lien d'annonce Dynacite invalide : {url!r}")
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
