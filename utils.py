import math
import re
import threading
import time
import unicodedata

import pandas as pd
from curl_cffi import requests

ANNONCES_MAX = 100
TIMEOUT_MAX = 30000
_route_lock = threading.Lock()
_last_route_request = None

class PageBlockedError(RuntimeError):
  pass

class RouteNotFoundError(ValueError):
  pass

def clean_bool(val, expected_val) -> bool | None:
  if isinstance(val, str):
    return val.strip().casefold() == expected_val.strip().casefold()
  return None

def bool_or_none(value):
  if pd.isna(value):
    return None
  if isinstance(value, str):
    normalized_value = value.strip().casefold()
    if normalized_value in {"true", "oui", "yes", "vrai"}:
      return True
    if normalized_value in {"false", "non", "no", "faux"}:
      return False

  numeric_value = pd.to_numeric(value, errors="coerce")
  return bool(numeric_value > 0) if pd.notna(numeric_value) else None

def clean_str(val) -> str | None:
  if val is None:
    return None
  if not isinstance(val, str):
    val = str(val)

  return re.sub(r"\s+", " ", val.strip())

def clean_city(val: str | None) -> str | None:
  """Uniformise la casse et retire les arrondissements avant le code postal."""
  if val is None or pd.isna(val):
    return None
  value = clean_str(val)
  if not value or value.casefold() in {"nan", "<na>", "none", "null"}:
    return None
  if "," in value:
    value = value.split(",", 1)[1]
  value = clean_str(
    value.replace("(", "") \
      .replace(")", "")
  )
  value = re.sub(r"\s+rive gauche\b", "", value, flags=re.IGNORECASE)
  value = re.sub(
    r"^(paris|lyon|marseille)\s+0*\d{1,2}(?:er|e|eme|ème)?"
    r"(?:\s+arrondissement)?(?=\s+\d{5}$)",
    r"\1", value, flags=re.IGNORECASE,
  )
  value = value.replace("’", "'").lower()
  particles = {"de", "du", "des", "en", "et", "la", "le", "les", "l", "d", "sur", "sous", "au", "aux"}

  def capitalize_word(match):
    word = match.group()
    return word if match.start() > 0 and word in particles else word.capitalize()

  return re.sub(r"[^\W\d_]+", capitalize_word, value) or None

def extract_property_type_info(value: str | None) -> str | None:
  value = clean_str(value)
  if not value:
    return None
  property_types = {
    "flat": "Appartement", "apartment": "Appartement", "appartement": "Appartement", "duplex/triplex": "Appartement", "duplex": "Appartement", "triplex": "Appartement",
    "house": "Maison", "maison": "Maison", "townhouse": "Maison",
    "land": "Terrain", "terrain": "Terrain",
    "parking": "Parking", "garage": "Parking",
    "other": "Autre", "autre": "Autre",
    "loft": "Loft", "castle": "Château", "château": "Château",
    "chambre": "Chambre"
  }
  return property_types.get(value.casefold(), value)

def filter_property_type(value: str | None) -> bool:
  return extract_property_type_info(value) not in {"Terrain", "Parking", "Autre", "Loft", "Chambre", ""}

def clean_int(val) -> int | None:
  """Convertit n'importe quelle valeur textuelle ou brute en int."""
  res = clean_float(val)
  return int(res) if res is not None else None

def normalize_municipality(value: str) -> str:
  value = unicodedata.normalize("NFKD", value.casefold())
  value = "".join(char for char in value if not unicodedata.combining(char))
  words = re.sub(r"[^a-z0-9]+", " ", value).split()
  return " ".join({"st": "saint", "ste": "sainte"}.get(word, word) for word in words)

def _geocoding_coordinates(feature: dict, ville: str, code_postal: str) -> tuple[float, float]:
  geometry = feature.get("geometry")
  point = geometry.get("coordinates") if isinstance(geometry, dict) and geometry.get("type") == "Point" else None
  if (not isinstance(point, (list, tuple)) or len(point) != 2
      or any(isinstance(v, bool) or not isinstance(v, (int, float))
             or not math.isfinite(v) for v in point)
      or not -180 <= point[0] <= 180 or not -90 <= point[1] <= 90):
    raise ValueError(f"Coordonnées IGN invalides pour {ville!r} {code_postal}.")
  return float(point[1]), float(point[0])

def get_geocoded_location(code_postal: str, ville: str) -> tuple[tuple[float, float], str | None]:
  """Renvoie les coordonnées et le nom du lieu approximatif, s'il y en a un."""
  if not re.fullmatch(r"\d{5}", code_postal) or not normalize_municipality(ville):
    raise ValueError(f"Ville ou code postal invalide : {ville!r} {code_postal!r}")
  normalized_city = normalize_municipality(ville)
  res = requests.get(
    "https://data.geopf.fr/geocodage/search",
    params={"q": f"{normalized_city} {code_postal}", "postcode": code_postal, "type": "municipality", "limit": 20},
    timeout=10,
  )
  res.raise_for_status()
  data = res.json()

  if not isinstance(data, dict) or not isinstance(data.get("features"), list):
    raise ValueError("Réponse de géocodage IGN invalide.")
  matches = []
  arrondissement_matches = []
  partial_matches = []
  for feature in data["features"]:
    if not isinstance(feature, dict):
      raise ValueError("Réponse de géocodage IGN invalide.")
    properties = feature.get("properties")
    if not isinstance(properties, dict):
      raise ValueError("Réponse de géocodage IGN invalide.")
    name = properties.get("name")
    if (properties.get("type") == "municipality" and properties.get("postcode") == code_postal
        and isinstance(name, str)):
      normalized_name = normalize_municipality(name)
      if normalized_name == normalized_city:
        matches.append(feature)
      elif normalized_city in {"paris", "lyon", "marseille"} and re.fullmatch(
          rf"{normalized_city} \d+(?:er|e|eme)? arrondissement", normalized_name):
        arrondissement_matches.append(feature)
      elif normalized_name and (
          normalized_city.startswith(normalized_name + " ")
          or normalized_city.endswith(" " + normalized_name)):
        partial_matches.append(feature)
  if not matches:
    matches = arrondissement_matches
  if matches:
    return _geocoding_coordinates(matches[0], ville, code_postal), None
  if partial_matches:
    feature = partial_matches[0]
    return _geocoding_coordinates(feature, ville, code_postal), feature["properties"]["name"]

  res = requests.get(
    "https://data.geopf.fr/geocodage/search",
    params={"q": f"{ville} {code_postal}", "postcode": code_postal, "limit": 20},
    timeout=10,
  )
  res.raise_for_status()
  data = res.json()
  if not isinstance(data, dict) or not isinstance(data.get("features"), list):
    raise ValueError("Réponse de géocodage IGN invalide.")
  for feature in data["features"]:
    if not isinstance(feature, dict) or not isinstance(feature.get("properties"), dict):
      raise ValueError("Réponse de géocodage IGN invalide.")
    properties = feature["properties"]
    name = properties.get("name")
    if (properties.get("postcode") != code_postal
        or properties.get("type") not in {"street", "locality"} or not isinstance(name, str)):
      continue
    places = re.findall(r"\(([^()]*)\)", name)
    places.extend(name.split(" - "))
    if normalized_city in {normalize_municipality(place) for place in places}:
      return _geocoding_coordinates(feature, ville, code_postal), name
  raise ValueError(f"Commune introuvable ou ambiguë : {ville!r} {code_postal}.")

def get_coordinates(code_postal: str, ville: str) -> tuple[float, float]:
  """Renvoie (latitude, longitude), approximatives pour une commune déléguée."""
  return get_geocoded_location(code_postal, ville)[0]

def validate_base_location(value: tuple[float, float]) -> tuple[float, float]:
  """Valide et normalise un point au format (longitude, latitude)."""
  if (
      not isinstance(value, (tuple, list)) or len(value) != 2
      or any(isinstance(v, bool) or not isinstance(v, (int, float))
             or not math.isfinite(v) for v in value)
      or not -180 <= value[0] <= 180 or not -90 <= value[1] <= 90
  ):
    raise ValueError("base_location doit contenir une longitude et une latitude valides.")
  return float(value[0]), float(value[1])

def get_route_distance(origin: tuple[float, float], destination: tuple[float, float]) -> float:
  """Renvoie la distance routière IGN en kilomètres, avec une requête par seconde."""
  global _last_route_request
  with _route_lock:
    if _last_route_request is not None:
      delay = 1.0 - (time.monotonic() - _last_route_request)
      if delay > 0:
        time.sleep(delay)
    _last_route_request = time.monotonic()
    response = requests.get(
      "https://data.geopf.fr/navigation/itineraire",
      params={
        "resource": "bdtopo-osrm",
        "start": f"{origin[1]},{origin[0]}",
        "end": f"{destination[1]},{destination[0]}",
        "profile": "car",
        "optimization": "shortest",
        "distanceUnit": "kilometer",
        "getSteps": "false",
      },
      timeout=30,
    )
  if response.status_code == 404:
    try:
      error_message = response.json().get("error", {}).get("message", "")
    except (ValueError, AttributeError):
      error_message = ""
    if isinstance(error_message, str) and error_message.strip().casefold() == "no path found":
      raise RouteNotFoundError("Aucun itinéraire routier trouvé par l'IGN.")
  response.raise_for_status()
  try:
    data = response.json()
    distance = data["distance"]
    if data["distanceUnit"] != "kilometer":
      raise ValueError("Unité de distance inattendue")
    if (
        isinstance(distance, bool) or not isinstance(distance, (int, float))
        or not math.isfinite(distance) or distance < 0
    ):
      raise ValueError("Distance d'itinéraire invalide")
    return round(distance, 1)
  except (KeyError, TypeError, ValueError) as error:
    raise ValueError("Réponse IGN invalide ou itinéraire introuvable.") from error

def clean_float(val) -> float | None:
  if val is None:
    return None
  if isinstance(val, (int, float)):
    return float(val)

  clean_str = str(val).replace("\xa0", "").replace(" ", "").replace(",", ".")
  match = re.search(r"\d+(?:\.\d+)?", clean_str)
  return float(match.group(0)) if match else None

def filter_keywords(title: str | None, *descriptions: str | None) -> bool:
  def normalize(value: str | None) -> str:
    value = unicodedata.normalize("NFKD", clean_str(value) or "").casefold()
    return "".join(char for char in value if not unicodedata.combining(char))

  title = normalize(title)
  if re.search(r"\bcolocations?\b", title) or re.match(r"chambres?\b", title):
    return False

  room = r"chambres?"
  qualifiers = r"(?:(?:une?|la|les|des|plusieurs|\d+|deux|trois|quatre|cinq|six)\s+)?"
  patterns = [
    rf"\b(?:loue|louons|louer|location(?:\s+(?:de|d['’]une))?|sous[- ]location\s+de)\s+{qualifiers}{room}\b",
    rf"\b{room}(?:\s+(?:meublee?s?|individuelle?s?|privee?s?|etudiante?s?|disponibles?))*"
    r"\s+(?:a\s+louer|en\s+(?:location|colocation)|chez\s+l['’]habitant)\b",
    rf"\b(?:location|loyer|prix|tarif|bail)\s+(?:par|a\s+la|pour\s+(?:une|chaque))\s+chambre\b",
    r"\b(?:chambres?\s+d['’]hotes?|coliving)\b",
  ]
  return not any(
    re.search(pattern, text)
    for text in [title] + [normalize(value) for value in descriptions]
    for pattern in patterns
  )

def extract_heating_info(description: str) -> dict:
  match = re.search(r"([^.\n]*chauffage[^.\n]*)", description, re.IGNORECASE)

  if not match:
    return {
      "mode": None,
      "type": None
    }
  sentence = match.group(1).strip()
  return extract_heating_type(sentence)

def extract_heating_type(sentence: str) -> dict:
  sentence_lower = sentence.lower()

  if "individuel" in sentence_lower:
    chauffage_mode = "Individuel"
  elif "collectif" in sentence_lower:
    chauffage_mode = "Collectif"
  else:
    chauffage_mode = None

  energies = {
    "Électrique": [r"électrique", r"électriques"],
    "Gaz": [r"\bgaz\b"],
    "Fioul": [r"fioul", r"masout"],
    "Pompe à chaleur": [r"pompe à chaleur", r"\bpac\b", r"aerothermie", r"geothermie"],
    "Bois / Pellets": [r"bois", r"pellet", r"granulé"],
    "Réseau de chaleur": [r"urbain", r"réseau de chaleur"],
    "Sol": [r"au sol"],
  }

  chauffage_type = None
  for energy_label, patterns in energies.items():
    if any(re.search(pattern, sentence_lower) for pattern in patterns):
      chauffage_type = energy_label
      break

  return {
    "mode": chauffage_mode,
    "type": chauffage_type
  }

def extract_balcony_info(values: list[str | int | float | None]) -> bool | None:
  return _extract_amenity_info(values, r"balcons?|terrasses?", "balcons ou terrasses")

def extract_parking_info(values: list[str | int | float | None]) -> bool | None:
  return _extract_amenity_info(values, r"parkings?|garages?|stationnements?", "parkings")

def extract_elevator_info(values: list[str | int | float | None]) -> bool | None:
  return _extract_amenity_info(values, r"ascenseurs?", "ascenseurs")

_NUMBER_PATTERN = r"\d+(?:[ \u00a0\u202f]\d{3})*(?:[.,]\d+)?"

def _extract_number_info(
    values: list[str | int | float | None], patterns: list[str],
    allow_zero: bool = False, integer: bool = False,
) -> int | float | None:
  for value in values:
    if value is None or isinstance(value, bool):
      continue
    if isinstance(value, (int, float)):
      number = float(value)
    else:
      text = clean_str(value)
      match = re.fullmatch(rf"({_NUMBER_PATTERN})", text)
      if match is None:
        match = next(
          (match for pattern in patterns
           if (match := re.search(pattern, text, re.IGNORECASE))),
          None,
        )
      if match is None:
        continue
      number = clean_float(match.group(1))
    if number is None or not math.isfinite(number) or number < 0:
      continue
    if number == 0 and not allow_zero:
      continue
    if integer and not number.is_integer():
      continue
    return int(number) if number.is_integer() else number
  return None

def extract_surface_info(values: list[str | int | float | None]) -> int | float | None:
  return _extract_number_info(values, [
    rf"\b({_NUMBER_PATTERN})\s*(?:m²|m2|m\s*\^2|mètres?\s+carrés?)\b",
    rf"\bsurface(?:\s+habitable)?\s*[:=]?\s*({_NUMBER_PATTERN})\b",
  ])

def extract_rooms_info(values: list[str | int | float | None]) -> int | None:
  number = _extract_number_info(values, [
    rf"\b({_NUMBER_PATTERN})\s*pièces?\b",
    r"\b(?:T|F)\s*([1-9]\d*)\b",
  ], integer=True)
  return int(number) if number is not None else None

def extract_rent_info(values: list[str | int | float | None]) -> int | float | None:
  return _extract_number_info(values, [
    rf"^\s*({_NUMBER_PATTERN})\s*€(?:\s*/\s*mois)?\s*$",
    rf"\b({_NUMBER_PATTERN})\s*€?\s*(?:HC\b|hors\s+charges\b)",
    rf"\bloyer(?:\s+(?:mensuel|hors\s+charges|HC))?\s*[:=]?\s*"
    rf"({_NUMBER_PATTERN})(?![\d.,])(?!\s*€?\s*(?:CC\b|charges\s+comprises\b))",
  ])

def extract_charges_info(values: list[str | int | float | None]) -> int | float | None:
  return _extract_number_info(values, [
    rf"^\s*({_NUMBER_PATTERN})\s*€(?:\s*/\s*mois)?\s*$",
    rf"\b(?:charges(?:\s+(?:mensuelles|forfaitaires))?|provision\s+sur\s+charges)"
    rf"\s*[:=]?\s*({_NUMBER_PATTERN})\s*€",
    rf"\b({_NUMBER_PATTERN})\s*€\s*(?:de\s+|pour\s+(?:les\s+)?)charges\b",
  ], allow_zero=True)

def extract_total_rent_info(values: list[str | int | float | None]) -> int | float | None:
  return _extract_number_info(values, [
    rf"^\s*({_NUMBER_PATTERN})\s*€(?:\s*/\s*mois)?\s*$",
    rf"\b({_NUMBER_PATTERN})\s*€?\s*(?:CC\b|charges\s+comprises\b)",
    rf"\bloyer\s+(?:total|charges\s+comprises|CC)\s*[:=]?\s*({_NUMBER_PATTERN})\b",
  ])

def extract_rental_amounts(
    rent_values: list[str | int | float | None],
    charges_values: list[str | int | float | None],
    total_values: list[str | int | float | None],
) -> dict:
  rent = extract_rent_info(rent_values)
  charges = extract_charges_info(charges_values)
  total = extract_total_rent_info(total_values)
  if rent is not None and charges is not None:
    total = round(rent + charges, 2)
  elif rent is None and total is not None and charges is not None:
    if total >= charges:
      rent = round(total - charges, 2)
  elif total is None and rent is not None:
    total = rent
  return {"loyer": rent, "charges": charges, "loyer_total": total}

def extract_energy_class_info(values: list[str | None]) -> str | None:
  for value in values:
    value = clean_str(value)
    if not value:
      continue

    normalized_value = value.upper()
    if normalized_value in {"NS", "NON SOUMIS AU DPE", "VIERGE"}:
      continue
    if re.fullmatch(r"[A-G]", normalized_value):
      return normalized_value

    match = re.search(
      r"(?i:\b(?:DPE|classe(?:\s+(?:énergétique|énergie))?)\s*[:=-]?\s*)"
      r"([A-G])\b",
      value,
    )
    if match:
      return match.group(1)

  return None

def extract_furnished_info(values: list[str | None]) -> str | None:
  def normalize(val: str | None) -> str:
    text = unicodedata.normalize("NFKD", clean_str(val) or "").casefold()
    return "".join(char for char in text if not unicodedata.combining(char))

  for val in values:
    if not val:
      continue
    val_norm = normalize(val).strip()
    if val_norm in {"non meuble", "unfurnished", "false", "non"}:
      return "Non meublé"
    if val_norm in {"meuble", "furnished", "true", "oui"}:
      return "Meublé"

  full_text = " ".join(clean_str(v) for v in values if v)
  if not full_text:
    return None

  norm_text = normalize(full_text)

  # 1. Non meublé / vide
  non_meuble_pattern = (
    r"\b(?:non[\s-]meubl(?:e|es|ee|ees)?|pas\s+meubl(?:e|es|ee|ees)?|"
    r"(?:bail|location|appartement|logement|maison)\s+vide|lou(?:e|es|ee|ees)?\s+vide)\b"
  )
  if re.search(non_meuble_pattern, norm_text):
    return "Non meublé"

  # 2. Supprimer les mentions relatives uniquement au mobilier de cuisine / salle de bain
  cleaned_norm_text = re.sub(
    r"\bcuisine\s+(?:entierement\s+|completement\s+)?(?:equipee\s+(?:et\s+)?|amenagee\s+(?:et\s+)?)?meubl(?:e|ee|es|ees)?\b",
    "", norm_text
  )
  cleaned_norm_text = re.sub(
    r"\bcuisine\s+meubl(?:e|ee|es|ees)?\s+(?:et\s+)?(?:equipee?|amenagee?)\b",
    "", cleaned_norm_text
  )
  cleaned_norm_text = re.sub(
    r"\b(?:avec\s+)?meubles?\s+hauts?\s+et\s+bas\b",
    "", cleaned_norm_text
  )
  cleaned_norm_text = re.sub(
    r"\bcuisine\s+amenagee\s*\([^)]*meubles?[^)]*\)",
    "", cleaned_norm_text
  )
  cleaned_norm_text = re.sub(
    r"\bcuisine\s+meubl(?:e|ee)?\b",
    "", cleaned_norm_text
  )
  cleaned_norm_text = re.sub(
    r"\bmeubles?\s+de\s+(?:cuisine|salle\s+d['\s]?eau|salle\s+de\s+bains?)\b",
    "", cleaned_norm_text
  )

  # 3. Meublé
  meuble_pattern = (
    r"\b(?:entierement\s+|tout\s+|pleinement\s+)?meubl(?:e|es|ee|ees)?\b|"
    r"\b(?:bail|location)\s+meubl(?:e|es|ee|ees)?\b|"
    r"\blou(?:e|es|ee|ees)?\s+meubl(?:e|es|ee|ees)?\b"
  )
  if re.search(meuble_pattern, cleaned_norm_text):
    return "Meublé"

  return None

def _extract_amenity_info(
    values: list[str | int | float | None], terms: str, label: str,
) -> bool | None:
  amenity_pattern = rf"\b(?:{terms})\b"
  absence_found = False
  for value in values:
    if value is None:
      continue

    if isinstance(value, bool):
      if value:
        return True
      absence_found = True
      continue

    if isinstance(value, (int, float)) or re.fullmatch(r"\s*\d+(?:[.,]\d+)?\s*", value):
      count = clean_int(value)
      if count is None:
        raise ValueError(f"Quantité de {label} invalide : {value!r}")
      if count > 0:
        return True
      absence_found = True
      continue

    if not re.search(amenity_pattern, value, re.IGNORECASE):
      continue

    description_without_absences = re.sub(
      rf"\b(?:sans\s+|pas\s+d['\u2019]\s*)(?:{terms})(?:\s+(?:ni|et|ou)\s+(?:{terms}))*\b",
      "",
      value,
      flags=re.IGNORECASE,
    )
    if re.search(amenity_pattern, description_without_absences, re.IGNORECASE):
      return True
    absence_found = True

  return False if absence_found else None

def extract_advertiser_from_text(text: str | None) -> str | None:
  if not text:
    return None
  patterns = [
    r"(?:contactez|contacter)\s+(?:votre\s+)?(?:agence\s+)?([A-ZÀ-ÖØ-ß][A-Za-zÀ-ÖØ-öø-ÿ0-9\s'-]{2,35}?(?:Immobilier|Immo|Conseil|Habitat|Transactions?|Gestion))",
    r"\b([A-ZÀ-ÖØ-ß][A-Za-zÀ-ÖØ-öø-ÿ0-9\s'-]{2,35}?\s+Immobilier)\b",
    r"\b(Agence\s+[A-ZÀ-ÖØ-ß][A-Za-zÀ-ÖØ-öø-ÿ0-9\s'-]{2,35})\b",
    r"\b(Cabinet\s+[A-ZÀ-ÖØ-ß][A-Za-zÀ-ÖØ-öø-ÿ0-9\s'-]{2,35})\b",
  ]
  for pattern in patterns:
    m = re.search(pattern, text)
    if m:
      candidate = clean_str(m.group(1))
      if candidate and len(candidate) > 3:
        cand_lower = candidate.lower()
        if cand_lower not in {
          "ce bien immobilier", "un bien immobilier", "le bien immobilier",
          "tout bien immobilier", "projet immobilier", "secteur immobilier",
          "marché immobilier", "complexe immobilier", "ensemble immobilier"
        }:
          return candidate
  return None

def detect_advertiser_type(
    advertiser_type: str | None = None,
    frais_agence: int | float | None = None,
    advertiser_name: str | None = None,
) -> str | None:
  if advertiser_type:
    norm = str(advertiser_type).strip().lower()
    if norm in {"pro", "professionnel", "agency", "agence", "societe", "société", "company"}:
      return "Professionnel"
    if norm in {"private", "particulier", "particular", "part", "proprietaire", "propriétaire"}:
      return "Particulier"

  if frais_agence is not None and not pd.isna(frais_agence) and float(frais_agence) > 0:
    return "Professionnel"

  if advertiser_name:
    name_lower = advertiser_name.lower()
    if any(term in name_lower for term in ["immo", "agence", "cabinet", "notaire", "gestion", "sarl", "sas", "sci"]):
      return "Professionnel"

  return None
