import json
import re
from datetime import datetime
from playwright.sync_api import sync_playwright

from scrapper import Scrapper, clean_str, filter_keywords, extract_heating_info, clean_int, clean_float, \
    extract_balcony_info, extract_parking_info, extract_elevator_info, extract_energy_class_info, clean_city, \
    extract_surface_info, extract_rooms_info, extract_rental_amounts, extract_property_type_info, filter_property_type, \
    TIMEOUT_MAX


class ScrapperSeloger(Scrapper):
    def __init__(self, annonce_type: str, max_loyer: int = None ):
        super().__init__(
            "seloger",
            base_url="https://www.seloger.com/",
            url_template="classified-search?initialQuery={code_postal}&page={page_num}&distributionTypes={annonce_type}&locations={locations}&estateTypes=House,Apartment&method=text&m=homepage_new_search_classified_search_result",
            parameters={
                "annonce_type": {
                    "location": ['Rent'],
                    "achat": ['Buy']
                }
            },
            annonce_type=annonce_type,
            max_loyer=max_loyer,
        )

    def get_parameters(self, p, code_postal: str) -> dict:
        url = f"https://www.seloger.com/search-mfe-bff/autocomplete/suggestion?query={code_postal}&limit=5&distributionType=Buy"

        print(f"[{self.type}] Récupération des paramètres pour le code postal '{code_postal}' via l'API : {url}...")

        p.wait_for_timeout(1000)
        response = p.goto(url)
        data = response.json()

        if not data or "items" not in data or not data['items']:
            raise ValueError(f"[{self.type}] Code postal introuvable dans l'API.")

        return {
            'locations': data['items'][0]['criteria']['location']['placeIds'][0]
        }

    def detect_captcha(self, page) -> bool:
        print(f"[{self.type}] Vérification de la présence d'un CAPTCHA...")
        return page.title() == "seloger.com"

    def scrap(self, page):
        annonces = []

        list_annonces = page.query_selector_all('[data-testid="serp-core-classified-card-testid"]')

        cards = []
        for annonce in list_annonces:

            link_elem = annonce.query_selector('a[data-testid="card-mfe-covering-link-testid"]')
            url = link_elem.get_attribute('href') if link_elem is not None else None

            if url:
                price_elem = annonce.query_selector('[data-testid="cardmfe-price-testid"]')
                price = price_elem.inner_text() if price_elem is not None else None

                dpe_elem = annonce.query_selector('[data-testid="card-mfe-energy-performance-class"]')
                classe_dpe = dpe_elem.inner_text() if dpe_elem is not None else None

                desc_elem = annonce.query_selector('[data-testid="cardmfe-description-text-test-id"]')
                desc = desc_elem.inner_text() if desc_elem is not None else None

                if url:
                    cards.append((url, price, classe_dpe, desc))

        for url, price, classe_dpe, desc in cards:
            page.wait_for_timeout(1000)
            page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MAX)
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            data = {
                "site": self.type,
                "meuble": None,
                "classe": extract_energy_class_info([classe_dpe]),
                "url": url,
                "date_update": now_str,
            }

            json_ld_scripts = page.query_selector_all('script[type="application/ld+json"]')
            surface_value = None
            rooms_value = None
            total_value = None
            listing_description = None

            for script in json_ld_scripts:
                content = script.inner_text()
                ld_data = json.loads(content)

                if ld_data.get('@type') == 'RealEstateListing':
                    data['date_annonce'] = ld_data.get('datePosted')
                    data['description'] = ld_data.get('description')
                    listing_description = ld_data.get('description')
                    data['titre'] = ld_data.get('name')

                    item = ld_data.get('itemOffered', {})
                    data['type'] = extract_property_type_info(item.get('@type'))
                    rooms_value = item.get('numberOfRooms')
                    surface_value = item.get('floorSize', {}).get('value')

                    offers = ld_data.get('offers', {})
                    total_value = offers.get('price')

            if (
                'titre' in data
                and filter_property_type(data.get("type"))
                and filter_keywords(data['titre'], listing_description, desc)
            ):
                address_elem = page.query_selector('[data-testid="cdp-location-address"]')
                if address_elem:
                    data["ville"] = clean_city(address_elem.inner_text())

                charges_elem = page.locator('text="Charges forfaitaires"').locator('..').locator('..')
                charges_text = None
                if charges_elem.count() > 0:
                    charges_text = charges_elem.first.inner_text()

                depot_elem = page.locator('text="Dépôt de garantie"').locator('..').locator('..')
                if depot_elem.count() > 0:
                    depot_text = depot_elem.first.inner_text()
                    match = re.search(r'([\d\s]+)\s*€', depot_text)
                    if match:
                        data["depot"] = float(match.group(1).replace(' ', ''))

                frais_elem = page.locator('text="Honoraires"').locator('..').locator('..')
                if frais_elem.count() > 0:
                    frais_text = frais_elem.first.inner_text()
                    match = re.search(r'([\d\s]+)\s*€', frais_text)
                    if match:
                        data["frais_agence"] = float(match.group(1).replace(' ', ''))

                features = page.query_selector_all('ul.FeaturesPreview li span.css-qjxbdv')
                feature_texts = []
                for feat in features:
                    text = feat.inner_text().strip()
                    feature_texts.append(text)

                    etage_match = re.search(r'(\d+)(?:ème|er)?\s*étage(?:\/(\d+)\s*étages)?', text, re.IGNORECASE)
                    if etage_match:
                        data["étage"] = int(etage_match.group(1))
                        if etage_match.group(2):
                            data["étages_total"] = int(etage_match.group(2))

                    chambre_match = re.search(r'(\d+)\s*chambre', text, re.IGNORECASE)
                    if chambre_match:
                        data["chambres"] = int(chambre_match.group(1))

                description_elem = page.query_selector('[data-testid="cdp-main-description-expandable-text"]')
                desc_text = ''

                if description_elem:
                    desc_text = description_elem.inner_text()
                    if not filter_keywords(data["titre"], desc_text):
                        continue

                    # Année de rénovation ou de construction
                    renov_match = re.search(r'rénové\s+en\s+(\d{4})|transformé\s+en\s+(\d{4})|année\s+(\d{4})', desc_text, re.IGNORECASE)
                    if renov_match:
                        data["année"] = int(next(g for g in renov_match.groups() if g is not None))

                    # Étage depuis le texte de description si non trouvé précédemment
                    if "étage" not in data or data["étage"] is None:
                        etage_desc = re.search(r'(\d+)(?:ème|er)?\s*étage', desc_text, re.IGNORECASE)
                        if etage_desc:
                            data["étage"] = int(etage_desc.group(1))

                    # Salles de bain / Salles d'eau
                    sdb_match = re.search(r'salle\s+de\s+bain|salle\s+d\'eau', desc_text, re.IGNORECASE)
                    if sdb_match:
                        # Compte le nombre de mentions ou vérifie la présence
                        sdb_count = len(re.findall(r'salle\s+de\s+bain|salle\s+d\'eau', desc_text, re.IGNORECASE))
                        data["salles_de_bain"] = sdb_count

                    extract_heating = extract_heating_info(desc_text)
                    if data.get("chauffage_mode") is None:
                        data["chauffage_mode"] = extract_heating.get("mode")
                    if data.get("chauffage_type") is None:
                        data["chauffage_type"] = extract_heating.get("type")

                data["caracteristiques"] = feature_texts
                data["description"] = desc_text
                amenity_values = feature_texts + [desc_text, data.get("description"), data["titre"]]
                data["balcon"] = extract_balcony_info(amenity_values)
                data["parking"] = extract_parking_info(amenity_values)
                data["ascenseur"] = extract_elevator_info(amenity_values)
                data["classe"] = extract_energy_class_info([classe_dpe, desc] + amenity_values)
                numeric_values = feature_texts + [desc_text, listing_description, desc, data["titre"]]
                data["surface"] = extract_surface_info([surface_value] + numeric_values)
                data["pieces"] = extract_rooms_info([rooms_value] + numeric_values)
                data.update(extract_rental_amounts(
                    numeric_values,
                    [charges_text] + numeric_values,
                    [total_value, price] + numeric_values,
                ))


                if self.max_loyer is not None and data["loyer_total"] is not None and (int(data["loyer_total"]) > self.max_loyer):
                    continue

                annonces.append(data)

        return annonces
