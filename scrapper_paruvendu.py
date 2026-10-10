import re
import urllib.parse
from datetime import datetime

from bs4 import BeautifulSoup
from scrapper import (
    Scrapper, TIMEOUT_MAX, PageBlockedError, clean_str, clean_city, clean_int,
    clean_float, extract_property_type_info, filter_property_type, filter_keywords,
    extract_heating_info, extract_balcony_info, extract_parking_info,
    extract_elevator_info, extract_energy_class_info, extract_surface_info,
    extract_rooms_info, extract_rental_amounts,
)


class ScrapperParuvendu(Scrapper):
    def __init__(self, annonce_type: str, max_loyer: int = None ):
        super().__init__(
            "paruvendu",
            base_url="https://www.paruvendu.fr/",
            url_template="immobilier/{annonce_type}/test-test/?codeINSEE={code_postal},&p={page_num}&rechpv=1&tt=5&tbApp=1&tbDup=1&tbChb=1&tbLof=1&tbAtl=1&tbPla=1&tbMai=1&tbVil=1&tbCha=1&tbPro=1&tbHot=1&tbMou=1&ddlFiltres=nofilter&prestige=0",
            parameters={
                "annonce_type": {
                    "location": ['recherche/location/appartement'],
                    "achat": ['vente']
                }
            },
            annonce_type=annonce_type,
            max_loyer=max_loyer,
        )

    def detect_captcha(self, page) -> bool:
        soup = BeautifulSoup(page.content(), "html.parser")
        return (
            soup.select_one("#challenge-form, #challenge-running, iframe[src*='captcha']") is not None
            or bool(re.search(
                r"just a moment|attention required",
                soup.title.get_text() if soup.title else "", re.IGNORECASE,
            ))
        )

    def ensure_page_ready(self, page):
        super().ensure_page_ready(page)
        if self.detect_captcha(page):
            raise PageBlockedError(f"[{self.type}] Page bloquée : {page.url}")

    def _extract_annonce(self, soup, url: str) -> dict | None:
        title_el = soup.select_one("#detail_h1")
        if title_el is None:
            raise ValueError(f"Titre de l'annonce introuvable : {url}")
        titre = clean_str(title_el.get_text(" ", strip=True))
        description_el = soup.select_one("#txtAnnonceTrunc")
        description = clean_str(description_el.get_text(" ", strip=True)) if description_el else None
        characteristics = [
            item.get_text(" ", strip=True)
            for item in soup.select("li") if item.select_one("span.uppercase")
        ]
        values = characteristics + [description, titre]
        type_match = re.search(
            r"\b(appartement|studio|maison|villa|terrain|parking|garage|autre|loft|château)\b",
            titre or "", re.IGNORECASE,
        )
        raw_type = type_match.group(1) if type_match else None
        bien_type = extract_property_type_info(
            {"studio": "Appartement", "villa": "Maison"}.get(
                raw_type.casefold(), raw_type,
            ) if raw_type else None
        )
        if not filter_property_type(bien_type) or not filter_keywords(titre, description):
            return None

        financial_fields = {}
        for block in soup.select(".opt19_hd_det"):
            label = block.find("span")
            amount = block.find("strong")
            if label and amount:
                financial_fields[label.get_text(" ", strip=True).casefold()] = clean_str(
                    amount.get_text(" ", strip=True),
                )

        def financial_value(pattern):
            return next(
                (value for label, value in financial_fields.items()
                 if re.search(pattern, label, re.IGNORECASE)),
                None,
            )

        def financial_amount(pattern):
            value = financial_value(pattern)
            if value is None:
                return None
            value = re.sub(r"(?<=\d)\.(?=\d{3}(?:\D|$))", "", value)
            return clean_float(value)

        price_el = soup.select_one("#autoprix .prixactionalerte-box span")
        price_text = clean_str(price_el.get_text(" ", strip=True)) if price_el else None
        # Le prix affiché est le total lorsque le bloc indique « Dont charges ».
        price_is_total = any("dont charges" in label for label in financial_fields) or bool(
            re.search(r"\b(?:CC|charges\s+comprises)\b", price_text or "", re.IGNORECASE)
        )
        amounts = extract_rental_amounts(
            ([] if price_is_total else [price_text]) + values,
            [financial_amount(r"charges")] + values,
            [price_text] + values,
        )
        heating = extract_heating_info(" ".join(value for value in values if value))
        city_el = soup.select_one("#detail_loc")
        dpe_el = soup.select_one(".DPE_consEnerNote")
        specs_text = " ".join(characteristics)

        def integer_field(pattern):
            match = re.search(pattern, specs_text, re.IGNORECASE)
            return clean_int(match.group(1)) if match else None

        date_match = re.search(
            r"mise\s+à\s+jour\s*[:\-]?\s*(\d{2}/\d{2}/\d{4}(?:\s+à\s+\d{2}:\d{2})?)",
            specs_text, re.IGNORECASE,
        )
        data = {
            "site": self.type, "titre": titre,
            "ville": clean_city(city_el.get_text(" ", strip=True)) if city_el else None,
            "type": bien_type, "meuble": None,
            "surface": extract_surface_info(values), "pieces": extract_rooms_info(values),
            **amounts,
            "depot": financial_amount(r"d[ée]p[oô]t.*garantie"),
            "frais_agence": financial_amount(r"honoraires"),
            "étage": integer_field(r"\b[ée]tage\s*:\s*(\d+)"),
            "chambres": integer_field(r"\b(\d+)\s*chambres?\b"),
            "salles_de_bain": integer_field(r"\b(\d+)\s*salles?\s+de\s+bains?\b"),
            "ascenseur": extract_elevator_info(values),
            "balcon": extract_balcony_info(values), "parking": extract_parking_info(values),
            "chauffage_mode": heating["mode"], "chauffage_type": heating["type"],
            "classe": extract_energy_class_info([
                dpe_el.get_text(" ", strip=True) if dpe_el else None,
            ] + values),
            "étages_total": integer_field(r"\b(?:sur|nombre\s+d['’]étages\s*:?)\s*(\d+)\s*étages?\b"),
            "année": integer_field(r"\b(?:construction|construit\s+en)\s*:?\s*(\d{4})"),
            "caracteristiques": characteristics, "description": description, "url": url,
            "date_annonce": date_match.group(1) if date_match else None,
            "date_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        if re.search(r"\bnon\s+meublé", " ".join(value for value in values if value), re.IGNORECASE):
            data["meuble"] = "Non meublé"
        elif re.search(r"\bmeublé", " ".join(value for value in values if value), re.IGNORECASE):
            data["meuble"] = "Meublé"
        if self.max_loyer is not None and data["loyer_total"] is not None and data["loyer_total"] > self.max_loyer:
            return None
        return data

    def scrap(self, page):
        annonces = []

        html = page.content()
        soup = BeautifulSoup(html, "html.parser")

        links = list(dict.fromkeys(
            urllib.parse.urljoin(self.base_url, link["href"])
            for link in soup.select("div.blocAnnonce > div.flex > a[href*='/immobilier/']")
        ))

        print(f"[{self.type}] {len(links)} annonces trouvées sur la page {page.url}")

        for url in links:
            try:
                page.wait_for_timeout(1000)
                page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MAX)
                self.ensure_page_ready(page)

                html = page.content()
                soup = BeautifulSoup(html, "html.parser")
                data = self._extract_annonce(soup, url)
                if data is not None:
                    annonces.append(data)

            except PageBlockedError:
                raise
            except Exception as e:
                print(f"[{self.type}] Erreur sur {url}: {e}")

        return annonces
