import json
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path

from bs4 import BeautifulSoup
from curl_cffi.requests.exceptions import RequestException
from playwright.sync_api import sync_playwright

from utils import PageBlockedError, RouteNotFoundError, ANNONCES_MAX, TIMEOUT_MAX, clean_city, get_geocoded_location, get_route_distance, validate_base_location, normalize_municipality


class Scrapper:
    uses_link_pagination = False
    RATE_LIMIT_PAUSE_SECONDS = 300

    def __init__(self, type: str, base_url: str, url_template: str, parameters: dict, annonce_type: str):
        self.type = type
        self.base_url = base_url
        self.url_template = url_template
        self.parameters = parameters
        self.annonce_types = list(self.parameters["annonce_type"][annonce_type])
        self.page_step = 100
        self.headless = (
            sys.platform.startswith("linux")
            and not os.environ.get("DISPLAY")
            and not os.environ.get("WAYLAND_DISPLAY")
        )

        path = Path(f"profiles/{self.type}").resolve()
        path.mkdir(parents=True, exist_ok=True)
        self.profil_dir = str(path)

        self.context = None
        self.browser = None
        self.session_state_file = f"profiles/{self.type}_state.json"
        self._playwright = sync_playwright().start()
        try:
            self.page = self.build_page()
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        """Ferme le navigateur et la session Playwright du scraper."""
        try:
            if self.context is not None:
                context, self.context = self.context, None
                context.close()
        finally:
            try:
                if self.browser is not None:
                    browser, self.browser = self.browser, None
                    browser.close()
            finally:
                if self._playwright is not None:
                    playwright, self._playwright = self._playwright, None
                    playwright.stop()

    def get_parameters(self, p, code_postal: str, text: str) -> dict:
        return {}

    def build_page_url(
            self,
            base_parameters: dict,
            annonce_type: str,
            code_postal: str,
            text: str = '',
            page_num: int = 1,
    ) -> str:
        url = self.url_template
        parameters = base_parameters

        if "{code_postal}" in self.url_template:
            parameters = {**parameters, "code_postal": code_postal}
        if "{text}" in self.url_template:
            parameters = {**parameters, "text": text}
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

    def detect_captcha(self) -> bool:
        return False

    def ensure_page_ready(self):
        soup = BeautifulSoup(self.page.content(), "html.parser")
        title = soup.title.get_text() if soup.title else ""
        if re.search(r"\b429\b|too many requests", title, re.IGNORECASE):
            raise PageBlockedError(
                f"[{self.type}] Limite de requêtes atteinte : {self.page.url}. "
                "Réessayez plus tard."
            )

        if self.detect_captcha():
            self.page.wait_for_timeout(5000)
            if self.headless:
                raise PageBlockedError(
                    f"[{self.type}] CAPTCHA sur {self.page.url} : "
                    "une intervention manuelle est impossible en mode headless."
                )
            else:
                print(f"[{self.type}] Résolvez le CAPTCHA...")
                input(f"[{self.type}] ---> Appuyez sur Entrée une fois la page affichée normalement...")

    def print_log(self, message: str):
        print(f"[{self.type}] {message}")

    def get_next_page_url(self) -> str | None:
        return None

    def ignore_cookies(self) -> None:
        pass

    def navigate(self, page, url: str, **kwargs):
        for attempt in range(2):
            response = page.goto(url, **kwargs)
            soup = BeautifulSoup(page.content(), "html.parser")
            headings = [element.get_text(" ", strip=True)
                        for element in soup.select("title, h1")]
            if soup.body:
                headings.append(soup.body.get_text(" ", strip=True)[:100])
            blocked = (
                response is not None and response.status in (419, 429)
            ) or any(
                re.match(r"^(?:419\b|429\b|too many requests\b)", text, re.IGNORECASE)
                for text in headings
            )
            if not blocked:
                return response
            if attempt:
                raise PageBlockedError(
                    f"[{self.type}] Limite de requêtes toujours active après 5 minutes : {url}"
                )
            self.print_log(f"Erreur 419/429 sur {url} : pause de 5 minutes avant une nouvelle tentative.")
            time.sleep(self.RATE_LIMIT_PAUSE_SECONDS)

    def build_page(self):
        p = self._playwright
        print(f"[{self.type}] Lancement du navigateur...")

        self.context = p.chromium.launch_persistent_context(
            user_data_dir=self.profil_dir,
            channel="chromium",
            headless=self.headless,
            no_viewport=True,
            locale="fr-FR",
            ignore_default_args=["--enable-automation"],
            args=["--disable-blink-features=AutomationControlled"],
        )

        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.wait_for_timeout(1000)
        self.navigate(self.page, self.base_url)

        if self.detect_captcha():
            if not self.headless:
                print(f"[{self.type}] Résolvez le CAPTCHA...")
                input(f"[{self.type}] ---> Appuyez sur Entrée une fois la page affichée normalement...")

        self.ignore_cookies()
        self.context.storage_state(path=self.session_state_file)
        self.context.close()
        self.context = None

        print(f"[{self.type}] ✓ Session enregistrée dans le profil.")

        self.browser = p.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled"]
        )

        self.context = self.browser.new_context(
            storage_state=self.session_state_file,
            locale="fr-FR",
        )

        return self.context.new_page()

    def scrap(self, el):
        raise NotImplementedError("La méthode scrap() doit être implémentée dans la classe dérivée.")

    @staticmethod
    def _annonce_key(annonce: dict) -> str:
        if annonce.get("url"):
            return str(annonce["url"])

        stable_data = {key: value for key, value in annonce.items() if key != "date_update"}
        return json.dumps(stable_data, sort_keys=True, default=str)

    def _add_distances(
            self, annonces: list[dict], base_location: tuple[float, float], coordinates: dict,
            routes: dict | None = None,
    ) -> None:
        """Ajoute la distance routière en km depuis le point de référence."""
        origin = validate_base_location(base_location)
        if routes is None:
            routes = {}

        def locate(postcode, city):
            key = (postcode, normalize_municipality(city))
            if key not in coordinates:
                try:
                    coordinates[key] = get_geocoded_location(postcode, city)
                except (RequestException, ValueError) as error:
                    self.print_log(f"Distance indisponible pour {city} {postcode} : {error}")
                    coordinates[key] = None
            return coordinates[key]

        for annonce in annonces:
            annonce["distance"] = None
            normalized_city = clean_city(annonce.get("ville"))
            match = re.search(r"\b\d{5}\b", normalized_city or "")
            if match is None:
                self.print_log(
                    f"Distance indisponible : code postal absent de {annonce.get('ville')!r} "
                    f"({annonce.get('url')})."
                )
                continue
            postcode = match.group()
            city = normalized_city[:match.start()] + normalized_city[match.end():]
            city = city.strip(" ()-,")
            if not normalize_municipality(city):
                self.print_log(f"Distance indisponible : ville absente pour {postcode} ({annonce.get('url')}).")
                continue
            location = locate(postcode, city)
            if location is None:
                continue
            destination, approximate_name = location
            route_key = (origin, destination)
            if route_key not in routes:
                try:
                    routes[route_key] = get_route_distance(origin, destination)
                except RouteNotFoundError as error:
                    self.print_log(f"Distance indisponible depuis {origin} vers {city} {postcode} : {error}")
                    routes[route_key] = None
                except (RequestException, ValueError) as error:
                    raise RuntimeError(
                        f"[{self.type}] Échec du calcul IGN depuis {origin} vers {city} {postcode} : {error}"
                    ) from error
            annonce["distance"] = routes[route_key]
            if approximate_name:
                distance = annonce["distance"]
                result = f"{distance} km" if distance is not None else "indisponible"
                print(f"[géocodage] {city} {postcode} : distance approximative depuis {approximate_name} : {result}.")

    def start(
            self, code_postaux: dict, searches: list = [], *,
            base_location: tuple[float, float],
    ) -> list[dict]:
        base_location = validate_base_location(base_location)
        code_postaux = dict(code_postaux)
        if len(searches) == 0:
            searches = ['']

        annonces = []
        coordinates = {}
        routes = {}

        for search in searches:
            for cp_key, cp_val in code_postaux.items():
                base_parameters = self.get_parameters(self.page, cp_key, search)

                for annonce_type in self.annonce_types:
                    page_num = 1
                    search_continue = True
                    annonces_seen = set()
                    pages_seen = set()
                    url = self.build_page_url(base_parameters, annonce_type, cp_val, search, page_num)

                    while len(annonces) < ANNONCES_MAX and search_continue:
                        if url in pages_seen:
                            raise RuntimeError(f"[{self.type}] Boucle de pagination détectée : {url}")
                        pages_seen.add(url)
                        print(f"[{self.type}] Récupération des annonces {cp_val} et la recherche '{search}' page {page_num} : {url}")

                        try:
                            self.page.wait_for_timeout(1000)
                            response = self.navigate(self.page, url, wait_until="domcontentloaded", timeout=TIMEOUT_MAX)
                            self.ensure_page_ready()
                            # Les scrapers peuvent naviguer vers les annonces individuelles.
                            next_page_url = self.get_next_page_url() if self.uses_link_pagination else None

                            if ".json" in url:
                                if response is None:
                                    raise RuntimeError(f"Aucune réponse reçue pour l'URL JSON : {url}")
                                scrap_input = response.json()
                            else:
                                scrap_input = self.page

                            annonces_new = self.scrap(scrap_input)
                            self._add_distances(annonces_new, base_location, coordinates, routes)
                            print(f"[{self.type}] {len(annonces_new)} annonces récupérées pour le code postal {cp_val} et la recherche '{search}' à la page {page_num}")

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
                                print(f"[{self.type}] ⚠ Plus d'annonce trouvée pour le code postal {cp_val} et la recherche '{search}' à la page {page_num}")
                                search_continue = False
                                if page_num == 1:
                                    self.page.wait_for_timeout(1000)
                            else:
                                annonces_added = [
                                    annonce for annonce in annonces_new
                                    if self._annonce_key(annonce) not in annonces_seen
                                ]

                                if not annonces_added:
                                    print(f"[{self.type}] ⚠ Aucune nouvelle annonce par rapport aux pages précédentes pour le code postal {cp_val} et la recherche '{search}' à la page {page_num}")
                                    search_continue = False
                                else:
                                    annonces.extend(annonces_added)
                                    annonces_seen.update(self._annonce_key(annonce) for annonce in annonces_added)
                                    page_num += 1
                                    url = self.build_page_url(base_parameters, annonce_type, cp_val, search, page_num)

                        except Exception as e:
                            print(f"[{self.type}] ⚠ Erreur lors de la récupération des annonces pour le code postal {cp_val} et la recherche '{search}': {e}")
                            raise e


        print(f"[{self.type}] ✓ {len(annonces)}/{ANNONCES_MAX} annonces récupérées.\n")
        return annonces
