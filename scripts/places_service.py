# -*- coding: utf-8 -*-
"""
Servico Google Places para Prospeccao B2B.
Portado de /opt/ia-callcenter/backend/src/services/google-places.ts (TS).
- geocode(address) -> {lat, lng, formatted_address}
- nearby_search(lat, lng, radius, type, max_results) -> [places]
- place_details(place_id) -> dict completo
- extract_instagram_from_website(url) -> '@user' ou None
"""
from __future__ import annotations

import os
import re
import time
import urllib.parse
import urllib.request
import urllib.error
import json
from typing import Optional

GOOGLE_BASE = "https://maps.googleapis.com/maps/api"

# Custos aproximados Google Places (USD), abril/2026
COST_PER_GEOCODE = 0.005
COST_PER_NEARBY = 0.032
COST_PER_DETAILS = 0.017


def _key() -> str:
    k = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    return k


def _http_get(url: str, timeout: int = 20) -> dict:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "naia-b2b/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
            return json.loads(data)
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="ignore")
        except Exception:
            body = ""
        raise RuntimeError(f"HTTP {e.code} fetching Google API: {body[:300]}")
    except Exception as e:
        raise RuntimeError(f"erro ao chamar Google API: {e}")


def geocode(address: str) -> dict:
    """Retorna {lat, lng, formatted_address}."""
    url = (
        f"{GOOGLE_BASE}/geocode/json?"
        f"address={urllib.parse.quote(address)}"
        f"&key={_key()}&language=pt-BR"
    )
    data = _http_get(url)
    if data.get("status") != "OK" or not data.get("results"):
        raise RuntimeError(f"geocode falhou para '{address}': {data.get('status')}")
    r = data["results"][0]
    return {
        "lat": r["geometry"]["location"]["lat"],
        "lng": r["geometry"]["location"]["lng"],
        "formatted_address": r.get("formatted_address", address),
    }


def nearby_search(lat: float, lng: float, radius: int, search_type: str, max_results: int = 60) -> dict:
    """Busca empresas via Google Places Nearby Search.
    Pagina automaticamente com 2s entre paginas. Retorna {results, api_calls}."""
    api_calls = 0
    base_params = {
        "location": f"{lat},{lng}",
        "radius": str(radius),
        "keyword": search_type,
        "key": _key(),
        "language": "pt-BR",
    }
    url = f"{GOOGLE_BASE}/place/nearbysearch/json?" + urllib.parse.urlencode(base_params)
    data = _http_get(url)
    api_calls += 1
    if data.get("status") not in ("OK", "ZERO_RESULTS"):
        raise RuntimeError(
            f"nearby search falhou ({search_type}): {data.get('status')} {data.get('error_message','')}"
        )
    results = list(data.get("results", []))
    next_token = data.get("next_page_token")

    while next_token and len(results) < max_results:
        time.sleep(2)  # Google exige ~2s entre paginas
        page_url = (
            f"{GOOGLE_BASE}/place/nearbysearch/json?"
            f"pagetoken={next_token}&key={_key()}"
        )
        page = _http_get(page_url)
        api_calls += 1
        if page.get("status") == "OK":
            results.extend(page.get("results", []))
            next_token = page.get("next_page_token")
        else:
            break

    return {
        "results": results[:max_results],
        "api_calls": api_calls,
    }


def place_details(place_id: str) -> dict:
    """Busca detalhes completos (telefone, site, horarios)."""
    fields = "place_id,name,formatted_address,international_phone_number,formatted_phone_number,website,opening_hours,rating,user_ratings_total,types,geometry"
    url = (
        f"{GOOGLE_BASE}/place/details/json?"
        f"place_id={place_id}&fields={fields}&key={_key()}&language=pt-BR"
    )
    data = _http_get(url)
    if data.get("status") != "OK":
        return {}
    r = data.get("result", {})
    return {
        "place_id": r.get("place_id"),
        "name": r.get("name"),
        "formatted_address": r.get("formatted_address"),
        "phone": r.get("international_phone_number") or r.get("formatted_phone_number"),
        "website": r.get("website"),
        "opening_hours": (
            {
                "weekday_text": r.get("opening_hours", {}).get("weekday_text"),
                "open_now": r.get("opening_hours", {}).get("open_now"),
            }
            if r.get("opening_hours")
            else None
        ),
        "rating": r.get("rating"),
        "user_ratings_total": r.get("user_ratings_total"),
        "types": r.get("types") or [],
        "lat": r.get("geometry", {}).get("location", {}).get("lat"),
        "lng": r.get("geometry", {}).get("location", {}).get("lng"),
    }


_INSTAGRAM_RE = re.compile(
    r"(?:https?://)?(?:www\.)?instagram\.com/([A-Za-z0-9_][A-Za-z0-9_.]{1,29})",
    re.IGNORECASE,
)
_IG_BLACKLIST = {
    "share", "explore", "p", "reels", "stories", "directory", "accounts",
    "about", "developer", "press", "api", "jobs", "privacy", "safety",
    "help", "terms", "legal", "rsrc", "keyframes", "static", "assets",
    "embed", "tv", "ajax", "graphql", "i", "sandbox", "font", "fonts",
    "media", "img", "image", "images", "video", "videos", "css", "js",
    "javascript", "html", "favicon", "whatsapp", "facebook", "twitter",
    "youtube", "linkedin", "tiktok", "telegram", "wa", "fb", "yt",
    "logo", "icon", "icons", "ads", "ad", "banner", "banners",
}


def extract_instagram_from_website(url: str) -> Optional[str]:
    """Tenta extrair handle Instagram do site da empresa.
    Procura SO por links instagram.com/USER. Ignora @handles soltos
    porque CSS/JS pode disparar falsos positivos (rsrc.php, keyframes)."""
    if not url:
        return None
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 naia-b2b"}
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read(200_000).decode("utf-8", errors="ignore")
    except Exception:
        return None
    for m in _INSTAGRAM_RE.finditer(html):
        handle = m.group(1).strip(".").rstrip("/")
        if not handle:
            continue
        if handle.lower() in _IG_BLACKLIST:
            continue
        if "." in handle and handle.endswith((".com", ".css", ".js", ".php", ".html")):
            continue
        if len(handle) < 3:
            continue
        return handle
    return None


def normalize_phone_to_whatsapp(phone: str) -> Optional[str]:
    """Converte fone formatado (ex '+55 11 99999-9999') para whatsapp '5511999999999'."""
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if not digits:
        return None
    if not digits.startswith("55") and len(digits) >= 10:
        digits = "55" + digits
    return digits if 12 <= len(digits) <= 14 else None


def search_businesses(
    business_type: str,
    location_input: str,
    radius_meters: int = 5000,
    max_results: int = 60,
    include_details: bool = True,
) -> dict:
    """Pipeline completo: geocode -> nearby -> details. Retorna companies + meta."""
    api_calls = 0
    origin = geocode(location_input)
    api_calls += 1

    nearby = nearby_search(
        origin["lat"], origin["lng"], radius_meters, business_type, max_results
    )
    api_calls += nearby["api_calls"]

    companies = []
    for place in nearby["results"]:
        c = {
            "place_id": place.get("place_id"),
            "name": place.get("name"),
            "formatted_address": place.get("vicinity") or place.get("formatted_address") or "",
            "phone": None,
            "website": None,
            "rating": place.get("rating"),
            "user_ratings_total": place.get("user_ratings_total"),
            "opening_hours": (
                {"open_now": place.get("opening_hours", {}).get("open_now")}
                if place.get("opening_hours")
                else None
            ),
            "types": place.get("types") or [],
            "lat": place.get("geometry", {}).get("location", {}).get("lat", 0),
            "lng": place.get("geometry", {}).get("location", {}).get("lng", 0),
            "raw": place,
        }
        if include_details and c["place_id"]:
            try:
                detail = place_details(c["place_id"])
                api_calls += 1
                if detail:
                    c.update(
                        {
                            "phone": detail.get("phone"),
                            "website": detail.get("website"),
                            "opening_hours": detail.get("opening_hours") or c.get("opening_hours"),
                            "rating": detail.get("rating") or c.get("rating"),
                            "user_ratings_total": detail.get("user_ratings_total") or c.get("user_ratings_total"),
                            "types": detail.get("types") or c.get("types"),
                        }
                    )
                    time.sleep(0.1)
            except Exception:
                pass
        companies.append(c)

    estimated_cost = (
        COST_PER_GEOCODE
        + COST_PER_NEARBY * nearby["api_calls"]
        + COST_PER_DETAILS * (len(companies) if include_details else 0)
    )
    return {
        "origin": origin,
        "companies": companies,
        "api_calls": api_calls,
        "estimated_cost_usd": round(estimated_cost, 4),
    }
