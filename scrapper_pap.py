import re
import urllib.parse
from datetime import datetime

from bs4 import BeautifulSoup
from scrapper import (
    Scrapper, TIMEOUT_MAX, PageBlockedError, clean_str, clean_city, clean_int, clean_float,
    extract_property_type_info, filter_property_type, filter_keywords,
    extract_heating_info, extract_balcony_info, extract_parking_info,
    extract_elevator_info, extract_energy_class_info, extract_surface_info,
    extract_rooms_info, extract_rental_amounts,
)


class ScrapperPap(Scrapper):
    uses_link_pagination = True

    def __init__(self, annonce_type: str, max_loyer: int = None ):
        super().__init__(
            "pap",
            base_url="https://www.pap.fr/",
            url_template="annonce/{annonce_type}-{code_postal}-{page_num}",
            parameters={
                "annonce_type": {
                    "location": ['location-appartement-location-accession-maison'],
                    "achat": ['vente-immobiliere']
                }
            },
            annonce_type=annonce_type,
            max_loyer=max_loyer,
        )

    def detect_captcha(self, page) -> bool:
        soup = BeautifulSoup(page.content(), "html.parser")
        return (
            soup.select_one("#challenge-running, #challenge-form, .g-recaptcha, iframe[src*='captcha']") is not None
            or bool(re.search(
                r"just a moment|attention required|vérification de sécurité",
                soup.title.get_text() if soup.title else "", re.IGNORECASE,
            ))
        )

    def get_next_page_url(self, page) -> str | None:
        soup = BeautifulSoup(page.content(), "html.parser")
        current_match = re.search(r"-(\d+)$", urllib.parse.urlsplit(page.url).path)
        current_page = int(current_match.group(1)) if current_match else 1
        for link in soup.find_all("a", href=True):
            href = urllib.parse.urljoin(page.url, link["href"])
            parsed = urllib.parse.urlsplit(href)
            if parsed.netloc != urllib.parse.urlsplit(self.base_url).netloc:
                continue
            if not parsed.path.startswith(("/annonce/", "/proximite/")):
                continue
            rel = link.get("rel", [])
            text = link.get_text(" ", strip=True)
            if "next" in rel or re.fullmatch(r"suivant(?:e)?(?:\s*[»›>])?", text, re.IGNORECASE):
                return href
            match = re.search(r"-(\d+)$", parsed.path)
            if text == str(current_page + 1) and match and int(match.group(1)) == current_page + 1:
                return href
        return None

    def scrap(self, page):
        annonces = []

        html = page.content()
        soup = BeautifulSoup(html, "html.parser")

        links = []
        for a in soup.find_all("a", class_="item-thumb-link"):
            href = a.get("href")
            if href:
                full_link = urllib.parse.urljoin(self.base_url, href)
                if full_link not in links:
                    links.append(full_link)

        for url in links:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            try:
                page.wait_for_timeout(1000)
                page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MAX)
                self.ensure_page_ready(page)

                html = page.content()
                soup = BeautifulSoup(html, "html.parser")

                title_el = soup.find("h1", class_="item-title")
                titre = clean_str(title_el.get_text(" ", strip=True)) if title_el else None
                desc_el = soup.find("div", class_="margin-bottom-30")
                description = clean_str(desc_el.get_text(" ", strip=True)) if desc_el else None
                items = soup.find_all("li", class_="item-summary-spec")
                caracteristiques = [item.get_text(" ", strip=True) for item in items]
                specs_text = " ".join(caracteristiques)
                values = caracteristiques + [description, titre]

                type_pattern = r"\b(appartement|studio|maison|terrain|parking|garage|autre|loft|château)\b"
                type_match = (
                    re.search(type_pattern, titre or "", re.IGNORECASE)
                    or re.search(type_pattern, specs_text, re.IGNORECASE)
                )
                raw_type = type_match.group(1) if type_match else None
                bien_type = extract_property_type_info(
                    "Appartement" if raw_type and raw_type.casefold() == "studio" else raw_type
                )

                if not filter_property_type(bien_type) or not filter_keywords(titre, description):
                    # print(f"[{self.type}] Annonce ignorée pour {url} : type '{bien_type}' ou mots-clés non conformes.")
                    continue

                city_match = re.search(
                    r"([^,]*?)\s*\((\d{5})\)\s*$", titre or "",
                )
                ville = None
                if city_match:
                    city_text = re.sub(
                        r"^.*(?:m²|m2|pièces?)\s*", "", city_match.group(1),
                        flags=re.IGNORECASE,
                    )
                    if type_match:
                        city_text = re.sub(
                            rf"^.*?\b{re.escape(type_match.group(1))}\b\s*",
                            "", city_text, flags=re.IGNORECASE,
                        )
                    ville = clean_city(f"{city_text} ({city_match.group(2)})")

                price_el = soup.find("span", class_="item-price")
                price_text = clean_str(price_el.get_text(" ", strip=True)) if price_el else None
                # Un prix CC ne doit pas être additionné une seconde fois aux charges.
                price_is_total = bool(re.search(
                    r"\b(?:CC|charges\s+comprises)\b", price_text or "", re.IGNORECASE,
                ))
                rental_amounts = extract_rental_amounts(
                    ([] if price_is_total else [price_text]) + values,
                    values,
                    [price_text] + values,
                )
                chauffage = extract_heating_info(" ".join(value for value in values if value))
                dpe_el = soup.find("span", class_=re.compile(r"dpe-grade-"))
                dpe_value = dpe_el.get_text(" ", strip=True) if dpe_el else None

                data = {
                    "site": self.type,
                    "titre": titre,
                    "ville": ville,
                    "type": bien_type,
                    "surface": extract_surface_info(values),
                    "pieces": extract_rooms_info(values),
                    **rental_amounts,
                    "depot": None,
                    "frais_agence": 0,
                    "étage": None,
                    "ascenseur": extract_elevator_info(values),
                    "chambres": None,
                    "salles_de_bain": None,
                    "balcon": extract_balcony_info(values),
                    "chauffage_type": chauffage.get("type"),
                    "chauffage_mode": chauffage.get("mode"),
                    "parking": extract_parking_info(values),
                    "classe": extract_energy_class_info([dpe_value] + values),
                    "étages_total": None,
                    "année": None,
                    "description": description,
                    "caracteristiques": caracteristiques,
                    "url": url,
                    "date_annonce": None,
                    "date_update": now_str,
                }

                deposit_labels = soup.find_all(
                    string=re.compile(r"^\s*dépôt de garantie\s*:?\s*$", re.IGNORECASE),
                )
                deposit_values = [
                    label.parent.parent.get_text(" ", strip=True)
                    for label in deposit_labels
                ] + values
                for deposit_value in deposit_values:
                    depot_match = re.search(
                        r"\bdépôt de garantie\s*:?\s*(\d+(?:[\s.]\d{3})*(?:,\d+|\.\d{1,2})?)\s*€",
                        deposit_value or "", re.IGNORECASE,
                    )
                    if depot_match:
                        amount = re.sub(r"(?<=\d)\.(?=\d{3}(?:\D|$))", "", depot_match.group(1))
                        data["depot"] = clean_float(clean_str(amount))
                        break

                # Date de publication / mise à jour
                date_el = soup.find("p", class_="date")
                if date_el:
                    data["date_annonce"] = clean_str(date_el.get_text(" ", strip=True))

                ch_match = re.search(r"(\d+)\s*chambre", specs_text, re.IGNORECASE)
                if ch_match:
                    data["chambres"] = clean_int(ch_match.group(1))

                etage_match = re.search(
                    r"(\d+)(er|ème)?\s*étage", specs_text, re.IGNORECASE
                )
                if etage_match:
                    data["étage"] = clean_int(etage_match.group(1))

                if self.max_loyer is not None and data["loyer_total"] is not None and data["loyer_total"] > self.max_loyer:
                    continue

                annonces.append(data)
            except PageBlockedError:
                raise
            except Exception as e:
                print(f"[{self.type}] Erreur sur {url}: {e}")




        return annonces
