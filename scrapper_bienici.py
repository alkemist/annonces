import json
import os
import urllib.parse
from datetime import datetime

from scrapper import Scrapper, clean_float, clean_int, clean_str, filter_keywords, extract_heating_info, \
    extract_heating_type, extract_balcony_info, extract_parking_info, extract_elevator_info, extract_energy_class_info, \
    clean_city, extract_surface_info, extract_rooms_info, extract_rental_amounts, \
    extract_property_type_info, filter_property_type, detect_advertiser_type, extract_advertiser_from_text, \
    extract_furnished_info


class ScrapperBienici(Scrapper):
    def __init__(self, annonce_type: str):
        json_template = (
            '{"size":__PAGE_STEP__,"from":__PAGE_FROM__,"filterType":"__ANNONCE_TYPE__",'
            '"propertyType":["house","flat","loft","castle","townhouse"],"page":__PAGE_NUM__,'
            '"sortBy":"relevance","sortOrder":"desc","onTheMarket":[true],'
            '"zoneIdsByTypes":{"zoneIds":__ZONE_IDS__}}'
        )

        encoded_filters = urllib.parse.quote(json_template, safe="{}[]")

        encoded_filters = (
            encoded_filters.replace("__PAGE_STEP__", "{page_step}")
            .replace("__PAGE_FROM__", "{page_from}")
            .replace("__PAGE_NUM__", "{page_num}")
            .replace("__ANNONCE_TYPE__", "{annonce_type}")
            .replace("__ZONE_IDS__", "{zone_ids}")
        )

        url_template = (
            f"realEstateAds.json?filters={encoded_filters}"
            "&extensionType=extendedIfNoResult"
            "&enableGoogleStructuredDataAggregates=true"
            "&leadingCount=2"
        )

        super().__init__(
            "bienici",
            base_url="https://www.bienici.com/",
            url_template=url_template,
            parameters={
                "annonce_type": {
                    "location": ["rent"],
                    "achat": ["buy"]
                },
                "type_property": {
                    "appartement": ["flat", "house"],
                    "parking": ["parking"],
                    "terrain": ["land"],
                },
            },
            annonce_type=annonce_type,
        )

    def get_parameters(self, p, code_postal: str) -> dict:
        url = f"https://res.bienici.com/suggest.json?q={code_postal}++(toutes+les+villes)&type=city%2Cdelegated-city%2Cdepartment%2CpostalCode%2Cregion"

        print(f"[{self.type}] Récupération des paramètres pour le code postal '{code_postal}' via l'API : {url}...")

        p.wait_for_timeout(1000)
        response = p.goto(url)
        data = response.json()

        if not data or not isinstance(data, list) or len(data) == 0:
            raise ValueError(f"[{self.type}] Code postal introuvable dans l'API.")

        zoneIds = sorted({
            zone_id
            for el in data
            if code_postal in el.get('postalCodes', [])
            for zone_id in el.get('zoneIds', [])
        })

        return {
            'zone_ids': urllib.parse.quote(
                json.dumps(zoneIds, separators=(",", ":")),
                safe="[]",
            ),
        }

    def detect_captcha(self, page) -> bool:
        return False

    def scrap(self, page):
        annonces = []

        list_annonces = page.get("realEstateAds", [])

        for annonce in list_annonces:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            titre = annonce.get("title")
            bien_type = extract_property_type_info(annonce.get("propertyType"))

            if (
                filter_property_type(bien_type)
                and filter_keywords(titre, annonce.get("description"))
            ):
                meuble_flag = "Meublé" if annonce.get("isFurnished") is True else ("Non meublé" if annonce.get("isFurnished") is False else None)
                description = annonce.get('description')
                meuble = extract_furnished_info([meuble_flag, titre, description])
                city = annonce.get("city")
                cp = annonce.get("postalCode")
                heating = clean_str(annonce.get("heating", "").lower())

                extract_heating = extract_heating_type(heating)
                chauffage_type = extract_heating.get("type") if extract_heating.get("type") else None
                chauffage_mode = extract_heating.get("mode")

                numeric_values = [description, titre]
                rental_amounts = extract_rental_amounts(
                    [annonce.get("price")] + numeric_values,
                    [annonce.get("charges")] + numeric_values,
                    [annonce.get("price")] + numeric_values,
                )

                data = {
                    "site": self.type,
                    "titre": titre,
                    "ville": clean_city(" ".join(str(value) for value in (city, cp) if value is not None)),
                    "meuble": meuble,
                    "type": bien_type,
                    "surface": extract_surface_info([annonce.get('surfaceArea')] + numeric_values),
                    "pieces": extract_rooms_info([annonce.get('roomsQuantity')] + numeric_values),
                    **rental_amounts,
                    "depot": annonce.get("safetyDeposit"),
                    "frais_agence": annonce.get("agencyRentalFee"),
                    "étage": annonce.get('floor'),
                    "chambres": clean_int(annonce.get("bedroomsQuantity")),
                    "salles_de_bain": clean_int(annonce.get("bathroomsQuantity")),
                    "chauffage_type": chauffage_type,
                    "chauffage_mode": chauffage_mode,
                    "ascenseur": extract_elevator_info([
                        annonce.get("hasElevator"), description, titre,
                    ]),
                    "balcon": extract_balcony_info([
                        annonce.get("balconyQuantity"), annonce.get("terracesQuantity"),
                        description, titre,
                    ]),
                    "parking": extract_parking_info([
                        annonce.get("parkingPlacesQuantity"), description, titre,
                    ]),
                    "classe": extract_energy_class_info([
                        annonce.get("energyClassification"), description, titre,
                    ]),
                    "étages_total": None,
                    "année": None,
                    "description": description,
                    "annonceur": None,
                    "type_annonceur": None,
                    "url": f"https://www.bienici.com/annonce/{annonce.get('id')}",
                    "date_annonce": annonce.get("modificationDate"),
                    "date_update": now_str,
                }

                annonceur = clean_str(annonce.get("accountDisplayName"))
                if not annonceur and description:
                    annonceur = extract_advertiser_from_text(description)

                ad_by_pro = annonce.get("adCreatedByPro")
                account_type = annonce.get("accountType")
                type_hint = "Professionnel" if ad_by_pro is True else ("Particulier" if ad_by_pro is False else account_type)
                type_annonceur = detect_advertiser_type(
                    advertiser_type=type_hint,
                    frais_agence=data.get("frais_agence"),
                    advertiser_name=annonceur,
                )
                if not type_annonceur:
                    type_annonceur = "Professionnel" if (ad_by_pro or annonceur) else None

                data["annonceur"] = annonceur
                data["type_annonceur"] = type_annonceur

                if "description" in data and data["description"] is not None:
                    extract_heating = extract_heating_info(data['description'])
                    if data.get("chauffage_mode") is None:
                        data["chauffage_mode"] = extract_heating.get("mode")
                    if data.get("chauffage_type") is None:
                        data["chauffage_type"] = extract_heating.get("type")

                annonces.append(data)



        return annonces
