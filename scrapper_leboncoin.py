import json
from datetime import datetime
from playwright.sync_api import sync_playwright

from scrapper import Scrapper, ANNONCES_MAX, TIMEOUT_MAX, clean_int, clean_bool, clean_float, clean_str, \
    extract_heating_info, filter_keywords, extract_balcony_info, extract_parking_info, extract_elevator_info, \
    extract_energy_class_info, clean_city, extract_surface_info, extract_rooms_info, extract_rental_amounts, \
    extract_property_type_info, filter_property_type, extract_heating_type, detect_advertiser_type, \
    extract_advertiser_from_text, extract_furnished_info


class ScrapperLeboncoin(Scrapper):
    def __init__(self, annonce_type: str):
        super().__init__(
            "leboncoin",
            base_url="https://www.leboncoin.fr/",
            url_template="recherche?locations={code_postal}&page={page_num}&category={annonce_type}",
            parameters={
                "annonce_type": {
                    "location": ["10"],
                    "achat": ["9"]
                },
                "type_property": {
                    "appartement": [],
                }
            },
            annonce_type=annonce_type,
        )

    def detect_captcha(self, page) -> bool:
        content = page.content()
        print(f"[{self.type}] Vérification de la présence d'un CAPTCHA...")
        return "captcha-delivery.com" in content

    def scrap(self, page):
        annonces = []

        script_elem = page.query_selector("script#__NEXT_DATA__")
        if script_elem:
            try:
                data = json.loads(script_elem.inner_text())
                page_props = data.get("props", {}).get("pageProps", {})
                list_annonces = page_props.get("searchData", {}).get("ads", []) or page_props.get("ads", [])

                for annonce in list_annonces:
                    annonce = dict(annonce)

                    try:
                        attributes = list(annonce["attributes"])

                        titre = annonce.get("subject")
                        bien_type = extract_property_type_info(
                            next((attr.get("value_label") for attr in attributes if attr.get("key") == "real_estate_type"), None)
                        )

                        if (
                            annonce.get('status') == 'active'
                            and annonce.get('category_id') in self.annonce_types
                            and filter_property_type(bien_type)
                            and filter_keywords(titre, annonce.get("body"))
                        ):
                            location = dict(annonce.get("location"))

                            caracteristiques = next((attr.get("values_label") for attr in attributes if attr.get("key") == "specificities"), []) or []
                            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            meuble_attr = next((attr.get("value_label") for attr in attributes if attr.get("key") == "furnished"), None)
                            meuble = extract_furnished_info([meuble_attr, titre, annonce.get("body")] + caracteristiques)
                            numeric_values = caracteristiques + [annonce.get("body"), titre]
                            price = annonce.get("price")
                            price_values = price if isinstance(price, list) else [price]
                            rental_amounts = extract_rental_amounts(
                                [next((attr.get("value") for attr in attributes if attr.get("key") == "rent_excluding_charges"), None)] + numeric_values,
                                [next((attr.get("value") for attr in attributes if attr.get("key") == "monthly_charges"), None)] + numeric_values,
                                price_values + numeric_values,
                            )

                            data = {
                                "site": self.type,
                                "titre": titre,
                                "ville": clean_city(location.get("city_label")),
                                "meuble": meuble,
                                "type": bien_type,
                                "surface": extract_surface_info(
                                    [next((attr.get("value") for attr in attributes if attr.get("key") == "square"), None)] + numeric_values
                                ),
                                "pieces": extract_rooms_info(
                                    [next((attr.get("value") for attr in attributes if attr.get("key") == "rooms"), None)] + numeric_values
                                ),
                                **rental_amounts,
                                "depot": clean_int(
                                    next((attr.get("value") for attr in attributes if attr.get("key") == "security_deposit"), None)
                                ),
                                "frais_agence": clean_int(
                                    next((attr.get("value") for attr in attributes if attr.get("key") == "rental_fees"), None)
                                ),
                                "étage": clean_int(
                                    next((attr.get("value") for attr in attributes if attr.get("key") == "floor_number"), None)
                                ),
                                "chambres": clean_int(
                                    next((attr.get("value") for attr in attributes if attr.get("key") == "bedrooms"), None)
                                ),
                                "salles_de_bain": clean_int(
                                    next((attr.get("value") for attr in attributes if attr.get("key") == "nb_bathrooms"), None)
                                ),
                                "classe": extract_energy_class_info([
                                    next((attr.get("value_label") for attr in attributes if attr.get("key") == "energy_rate"), None),
                                    annonce.get("body"), titre,
                                ] + caracteristiques),
                                "étages_total": clean_int(
                                    next((attr.get("value") for attr in attributes if attr.get("key") == "nb_floors_building"), None)
                                ),
                                "année": clean_int(
                                    next((attr.get("value") for attr in attributes if attr.get("key") == "building_year"), None)
                                ),
                                "caracteristiques": caracteristiques,
                                "description": clean_str(annonce.get("body")),
                                "annonceur": None,
                                "type_annonceur": None,
                                "url": annonce.get("url"),
                                "date_annonce": annonce.get("index_date"),
                                "date_update": now_str,
                            }

                            owner = annonce.get("owner") or {}
                            if not isinstance(owner, dict):
                                owner = {}
                            annonceur = clean_str(owner.get("name"))
                            if not annonceur and data["description"]:
                                annonceur = extract_advertiser_from_text(data["description"])

                            owner_type = owner.get("type") or annonce.get("sellerType")
                            type_annonceur = detect_advertiser_type(
                                advertiser_type=owner_type,
                                frais_agence=data.get("frais_agence"),
                                advertiser_name=annonceur,
                            )
                            if not type_annonceur:
                                if owner.get("siren") or (data.get("frais_agence") and data["frais_agence"] > 0):
                                    type_annonceur = "Professionnel"
                                elif owner_type in ("private", "particulier"):
                                    type_annonceur = "Particulier"

                            data["annonceur"] = annonceur
                            data["type_annonceur"] = type_annonceur

                            chauffage_type = next((attr.get("value_label") for attr in attributes if attr.get("key") == "heating_mode"), None)
                            if chauffage_type == 'Autre':
                                chauffage_type = None
                            data['chauffage_type'] = chauffage_type
                            heating_mode = next((attr.get("value_label") for attr in attributes if attr.get("key") == "heating_type"), None)
                            data['chauffage_mode'] = extract_heating_type(heating_mode or "").get("mode")

                            data['parking'] = extract_parking_info(
                                [
                                    next((attr.get("value") for attr in attributes if attr.get("key") == "nb_parkings"), None),
                                    titre,
                                ] + caracteristiques
                            )

                            data["balcon"] = extract_balcony_info([titre] + caracteristiques)

                            data["ascenseur"] = extract_elevator_info(
                                [
                                   clean_bool(
                                       next((attr.get("value_label") for attr in attributes if attr.get("key") == "elevator"), None),
                                       'Oui'
                                   ),
                                   titre,
                               ] + caracteristiques),

                            if caracteristiques is not None and len(caracteristiques) > 0:
                                extract_heating = extract_heating_info(" ".join(caracteristiques))
                                if data.get("chauffage_mode") is None:
                                    data["chauffage_mode"] = extract_heating.get("mode")
                                if data.get("chauffage_type") is None:
                                    data["chauffage_type"] = extract_heating.get("type")

                            annonces.append(data)

                    except Exception as e:
                        print(f"[{self.type}] ⚠ Erreur lors de l'extraction des données d'une annonce : {e}")
                        print(annonce)

            except Exception as e:
                print(f"[{self.type}] ⚠ Erreur lors de l'extraction des données des annonces : {e}")

        else:
            print(f"[{self.type}] ⚠ Script __NEXT_DATA__ introuvable sur la page. Impossible de récupérer les annonces.")

        return annonces
