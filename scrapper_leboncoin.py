import json
import re
from datetime import datetime
from typing import TypedDict
from urllib.parse import unquote, urljoin, urlsplit

from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError

from utils import clean_int, clean_bool, clean_float, clean_str, \
    extract_heating_info, filter_keywords, extract_balcony_info, extract_parking_info, extract_elevator_info, \
    extract_energy_class_info, clean_city, extract_surface_info, extract_rooms_info, extract_rental_amounts, \
    extract_property_type_info, filter_property_type, extract_heating_type, detect_advertiser_type, \
    extract_advertiser_from_text, extract_furnished_info, TIMEOUT_MAX

from scrapper import PageBlockedError, Scrapper
from conversation_analysis import ConversationMessage, analyze_messages, answer_status


class ConversationData(TypedDict):
    annonce_id: str | None
    author: str
    is_free: bool | None
    has_certificat: bool | None
    need_answer: bool | None
    wait_answer: bool | None
    messages: list[ConversationMessage]


class Conversation(ConversationData):
    id: str


class ScrapperLeboncoin(Scrapper):
    _MESSAGERIE = '#scrolling-list-items, div[aria-label="Messagerie"]'
    _CONVERSATIONS_ITEMS = (
        '#mainContent .relative ul > li'
    )
    _CONVERSATION_LINK = (
        'a[href^="/messages/id/"], a[href^="https://www.leboncoin.fr/messages/id/"]'
    )
    _MESSAGES_ITEMS = (
        '#mainContent ol > li [data-qa-id="message-bubble"]'
    )

    def __init__(self, annonce_type: str):
        super().__init__(
            "leboncoin",
            base_url="https://www.leboncoin.fr/",
            url_template="recherche?text={text}&locations={code_postal}&page={page_num}&category={annonce_type}",
            parameters={
                "annonce_type": {
                    "location": ["10"],
                    "achat": ["9"],
                    "animal": ["28"],
                }
            },
            annonce_type=annonce_type,
        )
        self.annonce_type = annonce_type

    def detect_captcha(self) -> bool:
        content = self.page.content()
        # print(f"[{self.type}] Vérification de la présence d'un CAPTCHA...")
        return "captcha-delivery.com" in content

    def is_on_messages(self) -> bool:
        return self.page.locator(self._MESSAGERIE).count() > 0

    def check_auth(self, url: str) -> None:
        adresse = urlsplit(url)
        self.print_log(f"Vérification de l'URL d'authentification : {adresse}")
        if adresse.scheme != "https" or adresse.netloc != "auth.leboncoin.fr":
            raise RuntimeError("Connexion Leboncoin : redirection vers un site inattendu.")

    def ignore_cookies(self) -> None:
        refus = self.page.locator("#didomi-notice-disagree-button").or_(
            self.page.get_by_role(
                "button", name=re.compile(r"continuer sans accept", re.I)
            )
        ).or_(
            self.page.get_by_role(
                "link", name=re.compile(r"continuer sans accept", re.I)
            )
        ).first
        try:
            refus.wait_for(state="visible", timeout=5_000)
        except PlaywrightTimeoutError:
            # La bannière est absente si le choix a déjà été enregistré.
            return
        refus.click()
        refus.wait_for(state="hidden")

    def go_to_messages(self) -> None:
        response = self.navigate(self.page, self.base_url + "messages", wait_until="domcontentloaded")
        if (response and response.status in (403, 429)) or self.detect_captcha():
            raise PageBlockedError("Leboncoin a bloqué l’accès à la page dans ce navigateur.")
        if response and not response.ok:
            raise RuntimeError(f"Page Leboncoin inaccessible (HTTP {response.status}).")

        self.ensure_page_ready()
        self.ignore_cookies()

    def login(self, email: str, mot_de_passe: str, *, timeout_ms: int = 120_000) -> None:
        """Connecte le compte via la messagerie, sans enregistrer ses identifiants.

        Si Leboncoin demande un code, saisissez-le dans le navigateur visible
        pendant que cette méthode attend la fin de la connexion.
        """
        email = email.strip()
        if not email or not mot_de_passe:
            raise ValueError("L’adresse e-mail et le mot de passe sont obligatoires.")

        try:
            # self.print_log(f"On essaye d'aller sur la messagerie...")
            self.go_to_messages()
            self.page.locator(f"{self._MESSAGERIE}, #email").first.wait_for(state="attached")
            if self.is_on_messages():
                return
            # self.print_log(f"    Besoin de se connecter...")

            champ_email = self.page.locator("#email")
            champ_email.wait_for(state="visible")
            champ_email.fill(email)
            # self.print_log(f"    Connexion avec '{email}'...")
            self.page.get_by_test_id("login-continue-button").click()
            # self.print_log(f"    Click sur le bouton de continuer...")

            champ_mot_de_passe = self.page.locator("#password")
            champ_mot_de_passe.wait_for(state="visible")

            champ_mot_de_passe.fill(mot_de_passe)
            # self.print_log(f"    Ajout du mot de passe...")

            self.page.get_by_test_id("submitButton").click()
            # self.print_log(f"    Click sur le bouton de connexion...")
            self.page.locator(self._MESSAGERIE).first.wait_for(state="attached", timeout=timeout_ms)
            self.print_log(f"✓ Connexion réussie !")
            if self.detect_captcha():
                raise PageBlockedError("Leboncoin demande un CAPTCHA.")

        except PlaywrightTimeoutError as e:
            if self.detect_captcha():
                raise PageBlockedError("Leboncoin demande un CAPTCHA.") from None
            print(e)
            raise RuntimeError(
                "Connexion Leboncoin non confirmée. Vérifiez le formulaire dans le navigateur : "
                "identifiants refusés, code de vérification ou formulaire modifié."
            ) from None
        except PlaywrightError:
            raise RuntimeError("La connexion Leboncoin a échoué dans le navigateur.") from None

    def get_conversations(self) -> list[Conversation]:
        self.go_to_messages()
        self.print_log("Lecture de la liste des messages...")

        conversations: dict[str, Conversation] = {}

        if self.page.locator(self._CONVERSATIONS_ITEMS).count() == 0:
            self.print_log("Aucun message trouvé dans la messagerie.")
            return []

        elements = self.page.locator(self._CONVERSATIONS_ITEMS)

        urls: dict[str, str] = {}
        for element in elements.all():
            link_elem = element.locator(self._CONVERSATION_LINK).first
            if link_elem.count() == 0:
                continue
            href = link_elem.get_attribute("href")
            if not href:
                continue
            url = unquote(href)
            message_id_match = re.search(r"/messages/id/([\w\-]+)", url)
            if not message_id_match:
                continue
            message_id = message_id_match.group(1)
            if message_id.isalpha():
                continue

            urls[message_id] = urljoin(self.base_url, url)

        for message_id, url in urls.items():
            conversations[message_id] = {
                "id": message_id,
                **self.get_conversation(url),
            }

        return list(conversations.values())

    def get_conversation(self, url: str) -> ConversationData:
        self.page.wait_for_timeout(500)
        self.print_log(f"Lecture de la conversation : {url}")
        self.navigate(self.page, url, wait_until="domcontentloaded", timeout=TIMEOUT_MAX)
        self.ensure_page_ready()

        author_element = self.page.locator("a.text-body-1-highlight.truncate").first
        author_element.wait_for(state="attached", timeout=TIMEOUT_MAX)
        author = author_element.inner_text().strip()
        if not author:
            raise RuntimeError("Impossible de lire le nom de l'auteur de la conversation.")

        annonce_id = None
        annonce = self.page.locator('a[href^="/ad/"]').first
        annonce.wait_for(state="attached", timeout=TIMEOUT_MAX)

        if annonce.count():
            href = annonce.get_attribute("href") or ""
            match = re.fullmatch(r"/ad/[^/]+/(\d+)/?", urlsplit(href).path)
            if not match:
                raise RuntimeError("Impossible de lire l’identifiant de l’annonce de la conversation.")
            annonce_id = match.group(1)

        messages = self.page.locator(self._MESSAGES_ITEMS)
        messages.first.wait_for(state="attached", timeout=TIMEOUT_MAX)

        conversation_messages = self._read_conversation_messages()
        is_free, has_certificat = analyze_messages(conversation_messages)
        need_answer, wait_answer = answer_status(conversation_messages)
        return {
            "annonce_id": annonce_id,
            "author": author,
            "is_free": is_free,
            "has_certificat": has_certificat,
            "need_answer": need_answer,
            "wait_answer": wait_answer,
            "messages": conversation_messages,
        }

    def _read_conversation_messages(self) -> list[ConversationMessage]:
        messages: list[ConversationMessage] = []
        for order, bubble in enumerate(self.page.locator(self._MESSAGES_ITEMS).all(), start=1):
            container = bubble.locator("xpath=..")
            classes = (container.get_attribute("class") or "").split()
            if "justify-end" in classes and "justify-start" not in classes:
                sender = "me"
            elif "justify-start" in classes and "justify-end" not in classes:
                sender = "author"
            else:
                raise RuntimeError(f"Impossible d'identifier l'expéditeur du message {order}.")

            messages.append({
                "sender": sender,
                "content": bubble.inner_text(),
                "order": order,
                "type": "politeness",
                "subjects": [],
            })
        return messages

    def scrap(self, el ) -> list[dict]:
        parsers = {
            "location": self.scrap_place,
            "achat": self.scrap_place,
            "animal": self.scrap_animal,
        }
        parser = parsers[self.annonce_type]
        script_elem = el.query_selector("script#__NEXT_DATA__")
        if not script_elem:
            print(f"[{self.type}] ⚠ Script __NEXT_DATA__ introuvable sur la page. Impossible de récupérer les annonces.")
            return []

        try:
            data = json.loads(script_elem.inner_text())
            page_props = data.get("props", {}).get("pageProps", {})
            list_annonces = page_props.get("searchData", {}).get("ads", []) or page_props.get("ads", [])
            if not isinstance(list_annonces, list):
                raise TypeError("Liste d'annonces invalide")
        except (AttributeError, TypeError, ValueError, PlaywrightError) as e:
            print(f"[{self.type}] ⚠ Erreur lors de l'extraction des données des annonces : {e}")
            return []

        annonces = []
        for annonce in list_annonces:
            try:
                if annonce.get("status") != "active":
                    continue
                if self.annonce_types and str(annonce.get("category_id")) not in self.annonce_types:
                    continue
                data = parser(annonce)
                if data is not None:
                    annonces.append(data)
            except (AttributeError, KeyError, TypeError, ValueError) as e:
                print(f"[{self.type}] ⚠ Erreur lors de l'extraction des données d'une annonce : {e}")
                print(annonce)
                print()
        return annonces

    def scrap_common(self, annonce: dict) -> dict:
        url = annonce.get("url")
        annonce_id_match = re.search(r"https://www.leboncoin.fr/ad/\w+/([\d]+)", url)

        if not annonce_id_match:
            raise ValueError(f"Impossible d'extraire l'identifiant de l'annonce depuis l'URL : {url}")

        location = annonce.get("location") or {}
        city = clean_city(annonce.get("city_label")) or clean_city(location.get("city"))
        postcode = clean_str(location.get("zipcode"))
        if city and postcode and re.fullmatch(r"\d{5}", postcode) and not re.search(r"\b\d{5}\b", city):
            city = clean_city(f"{city} {postcode}")
        if not city:
            self.print_log(f"Ville introuvable pour l'annonce : {url}")

        return {
            "id": annonce_id_match.group(1),
            "site": self.type,
            "titre": annonce.get("subject"),
            "ville": city,
            "description": clean_str(annonce.get("body")),
            "url": url,
            "date_annonce": annonce.get("index_date"),
            "date_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def scrap_place(self, annonce: dict) -> dict | None:
        attributes = annonce["attributes"]

        def attribute(key: str, field: str = "value"):
            return next((attr.get(field) for attr in attributes if attr.get("key") == key), None)

        titre = annonce.get("subject")
        description = annonce.get("body")
        bien_type = extract_property_type_info(attribute("real_estate_type", "value_label"))
        if not filter_property_type(bien_type) or not filter_keywords(titre, description):
            return None

        caracteristiques = attribute("specificities", "values_label") or []
        meuble = extract_furnished_info([attribute("furnished", "value_label"), titre, description] + caracteristiques)
        numeric_values = caracteristiques + [description, titre]
        price = annonce.get("price")
        price_values = price if isinstance(price, list) else [price]
        rental_amounts = extract_rental_amounts(
            [attribute("rent_excluding_charges")] + numeric_values,
            [attribute("monthly_charges")] + numeric_values,
            price_values + numeric_values,
        )

        data = {
            **self.scrap_common(annonce),
            "meuble": meuble,
            "type": bien_type,
            "surface": extract_surface_info([attribute("square")] + numeric_values),
            "pieces": extract_rooms_info([attribute("rooms")] + numeric_values),
            **rental_amounts,
            "depot": clean_int(attribute("security_deposit")),
            "frais_agence": clean_int(attribute("rental_fees")),
            "étage": clean_int(attribute("floor_number")),
            "chambres": clean_int(attribute("bedrooms")),
            "salles_de_bain": clean_int(attribute("nb_bathrooms")),
            "classe": extract_energy_class_info(
                [attribute("energy_rate", "value_label"), description, titre] + caracteristiques
            ),
            "étages_total": clean_int(attribute("nb_floors_building")),
            "année": clean_int(attribute("building_year")),
            "caracteristiques": caracteristiques,
        }

        owner = annonce.get("owner")
        if not isinstance(owner, dict):
            owner = {}
        annonceur = clean_str(owner.get("name"))
        if not annonceur and data["description"]:
            annonceur = extract_advertiser_from_text(data["description"])

        owner_type = owner.get("type") or annonce.get("sellerType")
        type_annonceur = detect_advertiser_type(
            advertiser_type=owner_type,
            frais_agence=data["frais_agence"],
            advertiser_name=annonceur,
        )
        if not type_annonceur:
            if owner.get("siren") or (data["frais_agence"] and data["frais_agence"] > 0):
                type_annonceur = "Professionnel"
            elif owner_type in ("private", "particulier"):
                type_annonceur = "Particulier"
        data["annonceur"] = annonceur
        data["type_annonceur"] = type_annonceur

        chauffage_type = attribute("heating_mode", "value_label")
        data["chauffage_type"] = None if chauffage_type == "Autre" else chauffage_type
        heating_mode = attribute("heating_type", "value_label")
        data["chauffage_mode"] = extract_heating_type(heating_mode or "").get("mode")
        data["parking"] = extract_parking_info([attribute("nb_parkings"), titre] + caracteristiques)
        data["balcon"] = extract_balcony_info([titre] + caracteristiques)
        data["ascenseur"] = (extract_elevator_info(
            [clean_bool(attribute("elevator", "value_label"), "Oui"), titre] + caracteristiques
        ),)

        if caracteristiques:
            heating = extract_heating_info(" ".join(caracteristiques))
            if data["chauffage_mode"] is None:
                data["chauffage_mode"] = heating.get("mode")
            if data["chauffage_type"] is None:
                data["chauffage_type"] = heating.get("type")

        return data

    def scrap_animal(self, annonce: dict) -> dict:
        price = annonce.get("price")
        if isinstance(price, list):
            price = price[0] if price else None

        # https://www.leboncoin.fr/reply/3306289939

        return {
            **self.scrap_common(annonce),
            "prix": clean_float(price),
            "caracteristiques": annonce.get("attributes") or [],
        }
