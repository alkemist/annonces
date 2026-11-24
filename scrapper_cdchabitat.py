import re
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from scrapper import PageBlockedError, Scrapper
from utils import (
    TIMEOUT_MAX, clean_city, clean_float, clean_int, clean_str,
    extract_balcony_info, extract_elevator_info, extract_energy_class_info,
    extract_furnished_info, extract_heating_type, extract_parking_info,
    extract_property_type_info, extract_rental_amounts, extract_rooms_info,
    extract_surface_info, filter_keywords, filter_property_type, validate_base_location,
)


class ScrapperCdcHabitat(Scrapper):
    """Parcourt les locations CDC Habitat dans le département de l'Ain."""

    uses_link_pagination = True
    search_location = "Ain"

    def __init__(self, annonce_type: str):
        if annonce_type != "location":
            raise ValueError("CDC Habitat est configuré uniquement pour la location.")
        super().__init__(
            "cdchabitat",
            "https://www.cdc-habitat.fr/",
            "recherche/{annonce_type}?lbLieu=Ain",
            {"annonce_type": {"location": ["vivelli", "social", "libre"]}},
            annonce_type,
        )

    def detect_captcha(self) -> bool:
        soup = BeautifulSoup(self.page.content(), "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        return bool(
            soup.select_one("#challenge-form, #challenge-running")
            or re.search(r"just a moment|attention required|vérification de sécurité", title, re.I)
        )

    def ensure_page_ready(self):
        super().ensure_page_ready()
        if self.detect_captcha():
            raise PageBlockedError(f"[{self.type}] Page bloquée : {self.page.url}")

    def get_next_page_url(self) -> str | None:
        soup = BeautifulSoup(self.page.content(), "html.parser")
        current = soup.select_one(".pagination a[aria-current='true']")
        if current is None:
            if soup.select_one(".pagination a[data-page]"):
                raise ValueError(f"Page courante CDC Habitat introuvable : {self.page.url}")
            return None
        number = clean_int(current.get("data-page"))
        if number is None:
            raise ValueError(f"Numéro de page CDC Habitat invalide : {self.page.url}")
        link = soup.select_one(f".pagination a[data-page='{number + 1}']")
        if link is None:
            return None
        current_url = urlsplit(self.page.url)
        link_url = urlsplit(urljoin(self.page.url, link.get("href", "")))
        expected_path = current_url.path.rstrip("/")
        if (link_url.netloc != urlsplit(self.base_url).netloc
                or link_url.path.rstrip("/") not in {expected_path, f"{expected_path}/page-{number + 1}"}):
            raise ValueError(f"Lien de pagination CDC Habitat invalide : {link_url.geturl()}")
        parameters = [(key, value) for key, value in parse_qsl(current_url.query)
                      if key != "pagerGo"]
        parameters.append(("pagerGo", str(number + 1)))
        return urlunsplit((current_url.scheme, current_url.netloc, current_url.path,
                           urlencode(parameters), ""))

    def _extract_annonce(self, card, soup, url: str) -> dict | None:
        def text(root, selector):
            element = root.select_one(selector)
            return clean_str(element.get_text(" ", strip=True)) if element else None

        title = text(card, ".description h3")
        if not title or soup.select_one("h1") is None:
            raise ValueError(f"Annonce CDC Habitat incomplète : {url}")
        city = text(card, ".description .location")
        property_type = extract_property_type_info(text(card, ".description .type"))
        description_el = soup.select_one("#description")
        description = None
        if description_el:
            heading = description_el.find(["h1", "h2", "h3"])
            if heading:
                heading.extract()
            description = clean_str(description_el.get_text(" ", strip=True)) or None
        if not filter_property_type(property_type) or not filter_keywords(title, description):
            return None

        notes = text(card, ".description .notes")
        values = [title, notes, description]
        price_heading = soup.find("h2", string=re.compile(r"^\s*A propos du prix\s*$", re.I))
        price_fields = {}
        if price_heading:
            for row in price_heading.parent.select(".flex"):
                parts = row.find_all("div", recursive=False)
                if len(parts) == 2:
                    price_fields[clean_str(parts[0].get_text(" ", strip=True)).casefold()] = clean_float(
                        parts[1].get_text(" ", strip=True)
                    )
        total = price_fields.get("loyer charges comprises")
        if total is None:
            total = clean_float(text(card, ".description .price"))
        charges = price_fields.get("charges générales")
        amounts = extract_rental_amounts([], [charges], [total])
        floor = re.search(r"\b(\d+)(?:er|ème|eme|e)?\s+étage\b", title, re.I)
        heating = extract_heating_type(description or "")
        bedroom = re.search(r"\b(\d+)\s+chambres?\b", description or "", re.I)
        return {
            "site": self.type, "titre": title, "ville": clean_city(city),
            "type": property_type, "meuble": extract_furnished_info(values),
            "surface": extract_surface_info([title]), "pieces": extract_rooms_info([title]),
            **amounts, "depot": price_fields.get("dépôt de garantie"),
            "frais_agence": 0 if re.search(r"\b(?:sans|aucun)\s+honoraires?\b", description or "", re.I) else None,
            "étage": 0 if re.search(r"\bRDC\b", title, re.I) else clean_int(floor.group(1)) if floor else None,
            "ascenseur": extract_elevator_info(values),
            "chambres": clean_int(bedroom.group(1)) if bedroom else None,
            "salles_de_bain": None, "balcon": extract_balcony_info(values),
            "chauffage_type": heating["type"], "chauffage_mode": heating["mode"],
            "parking": extract_parking_info(values),
            "classe": extract_energy_class_info([description]), "étages_total": None,
            "année": None, "caracteristiques": [notes] if notes else [],
            "description": description, "annonceur": "CDC Habitat",
            "type_annonceur": "Professionnel", "url": url, "date_annonce": None,
            "date_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def scrap(self, el):
        soup = BeautifulSoup(el.content(), "html.parser")
        results = soup.select_one("#listeResultats")
        if results is None:
            raise ValueError(f"Résultats CDC Habitat introuvables : {el.url}")
        selected = soup.select_one("#lbLieu option[selected]")
        if selected is None or selected.get("value") != self.search_location:
            raise ValueError(f"Filtre géographique CDC Habitat absent : {el.url}")
        cards = results.select("article.residenceCard")
        if not cards and results.select_one(".cards.noResults") is None:
            raise ValueError(f"Structure des résultats CDC Habitat inconnue : {el.url}")
        annonces = []
        seen = set()
        for card in cards:
            link = card.select_one(".description h3")
            link = link.find_parent("a", href=True) if link else None
            url = urljoin(self.base_url, link["href"]) if link else ""
            parsed = urlsplit(url)
            if (parsed.netloc != urlsplit(self.base_url).netloc
                    or not re.fullmatch(r"/annonces-immobilieres/location/(?:[^/]+/){3}\d+/?", parsed.path)):
                raise ValueError(f"Lien d'annonce CDC Habitat invalide : {url!r}")
            if url in seen:
                continue
            seen.add(url)
            type_element = card.select_one(".description .type")
            property_type = extract_property_type_info(
                clean_str(type_element.get_text(" ", strip=True)) if type_element else None
            )
            if not filter_property_type(property_type):
                continue
            el.wait_for_timeout(1000)
            response = self.navigate(el, url, wait_until="domcontentloaded", timeout=TIMEOUT_MAX)
            self.ensure_page_ready()
            if response is None or response.status >= 400:
                raise RuntimeError(f"[{self.type}] Impossible de charger l'annonce : {url}")
            annonce = self._extract_annonce(card, BeautifulSoup(el.content(), "html.parser"), url)
            if annonce is not None:
                annonces.append(annonce)
        return annonces

    def start(
            self, code_postaux: dict, searches: list = [], *,
            base_location: tuple[float, float],
    ) -> list[dict]:
        base_location = validate_base_location(base_location)
        if searches and searches != [""]:
            raise ValueError("La recherche textuelle CDC Habitat n'est pas prise en charge.")
        annonces = []
        coordinates, routes = {}, {}
        for code_postal in code_postaux:
            seen = set()
            for category in self.annonce_types:
                url = self.build_page_url({}, category, code_postal)
                pages_seen = set()
                while url:
                    if url in pages_seen:
                        raise RuntimeError(f"[{self.type}] Boucle de pagination détectée : {url}")
                    pages_seen.add(url)
                    self.print_log(f"Récupération des annonces {category} : {url}")
                    self.page.wait_for_timeout(1000)
                    response = self.navigate(self.page, url, wait_until="domcontentloaded", timeout=TIMEOUT_MAX)
                    self.ensure_page_ready()
                    if response is None or response.status >= 400:
                        raise RuntimeError(f"[{self.type}] Impossible de charger les résultats : {url}")
                    next_url = self.get_next_page_url()
                    new = [item for item in self.scrap(self.page) if self._annonce_key(item) not in seen]
                    self._add_distances(new, base_location, coordinates, routes)
                    annonces.extend(new)
                    seen.update(self._annonce_key(item) for item in new)
                    url = next_url
        self.print_log(f"{len(annonces)} annonces récupérées.")
        return annonces
