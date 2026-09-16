"""Lidl Plus country table (offline fallback + locale helpers)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

DEFAULT_COUNTRY = "PL"
SKIP_COUNTRY_CODES = {"US"}


@dataclass(frozen=True)
class Country:
    """A Lidl Plus country supported by the European mobile backend."""

    code: str
    name: str
    languages: tuple[str, ...]
    currency: str
    time_zone: str

    @property
    def default_language(self) -> str:
        return self.languages[0] if self.languages else "en"

    def locale(self, language: str | None = None) -> str:
        """Return OAuth/API language tag, e.g. pl-PL."""
        lang = (language or self.default_language).strip()
        if not lang:
            lang = "en"
        if "-" in lang:
            return lang
        return f"{lang}-{self.code}"


# First language in the tuple is the default used when the country has only one.
_COUNTRIES: tuple[Country, ...] = (
    Country("AT", "Austria", ("de",), "EUR", "Europe/Vienna"),
    Country("BE", "Belgium", ("nl", "fr", "de"), "EUR", "Europe/Brussels"),
    Country("BG", "Bulgaria", ("bg",), "BGN", "Europe/Sofia"),
    Country("CH", "Switzerland", ("de", "fr", "it"), "CHF", "Europe/Zurich"),
    Country("CY", "Cyprus", ("el", "en"), "EUR", "Asia/Nicosia"),
    Country("CZ", "Czechia", ("cs",), "CZK", "Europe/Prague"),
    Country("DE", "Germany", ("de",), "EUR", "Europe/Berlin"),
    Country("DK", "Denmark", ("da",), "DKK", "Europe/Copenhagen"),
    Country("EE", "Estonia", ("et",), "EUR", "Europe/Tallinn"),
    Country("ES", "Spain", ("es",), "EUR", "Europe/Madrid"),
    Country("FI", "Finland", ("fi",), "EUR", "Europe/Helsinki"),
    Country("FR", "France", ("fr",), "EUR", "Europe/Paris"),
    Country("GB", "United Kingdom", ("en",), "GBP", "Europe/London"),
    Country("GR", "Greece", ("el",), "EUR", "Europe/Athens"),
    Country("HR", "Croatia", ("hr",), "EUR", "Europe/Zagreb"),
    Country("HU", "Hungary", ("hu",), "HUF", "Europe/Budapest"),
    Country("IE", "Ireland", ("en",), "EUR", "Europe/Dublin"),
    Country("IT", "Italy", ("it",), "EUR", "Europe/Rome"),
    Country("LT", "Lithuania", ("lt",), "EUR", "Europe/Vilnius"),
    Country("LU", "Luxembourg", ("fr", "de"), "EUR", "Europe/Luxembourg"),
    Country("LV", "Latvia", ("lv",), "EUR", "Europe/Riga"),
    Country("MT", "Malta", ("en", "mt"), "EUR", "Europe/Malta"),
    Country("NL", "Netherlands", ("nl",), "EUR", "Europe/Amsterdam"),
    Country("PL", "Poland", ("pl",), "PLN", "Europe/Warsaw"),
    Country("PT", "Portugal", ("pt",), "EUR", "Europe/Lisbon"),
    Country("RO", "Romania", ("ro",), "RON", "Europe/Bucharest"),
    Country("RS", "Serbia", ("sr",), "RSD", "Europe/Belgrade"),
    Country("SE", "Sweden", ("sv",), "SEK", "Europe/Stockholm"),
    Country("SI", "Slovenia", ("sl",), "EUR", "Europe/Ljubljana"),
    Country("SK", "Slovakia", ("sk",), "EUR", "Europe/Bratislava"),
)

COUNTRIES: dict[str, Country] = {country.code: country for country in _COUNTRIES}


def get_country(code: str | None) -> Country:
    """Look up a country, falling back to Poland."""
    if code:
        found = COUNTRIES.get(code.upper())
        if found:
            return found
    return COUNTRIES[DEFAULT_COUNTRY]


def default_country_code(hass_country: str | None) -> str:
    """Prefer the Home Assistant country when it is a Lidl Plus market."""
    if hass_country:
        code = hass_country.upper()
        if code in COUNTRIES:
            return code
    return DEFAULT_COUNTRY


def country_options(
    countries: dict[str, Country] | None = None,
) -> list[dict[str, str]]:
    """Selector options sorted by English name."""
    mapping = countries if countries is not None else COUNTRIES
    return [
        {"value": country.code, "label": f"{country.name} ({country.code})"}
        for country in sorted(mapping.values(), key=lambda item: item.name)
    ]


def language_options(country: Country) -> list[dict[str, str]]:
    return [
        {"value": country.locale(language), "label": language}
        for language in country.languages
    ]


def needs_language_step(country: Country) -> bool:
    return len(country.languages) > 1


def merge_gateway_countries(payload: Any) -> dict[str, Country]:
    """Overlay live gateway names/languages onto the static table.

    Unknown ISO codes from the gateway are ignored so we do not invent
    currency or timezone. The US app (myLidl) is skipped.
    """
    merged = dict(COUNTRIES)
    items: list[Any]
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = list(payload.get("countries") or payload.get("items") or [])
    else:
        return merged
    for item in items:
        if not isinstance(item, dict):
            continue
        code = str(item.get("id") or "").upper()
        if not code or code in SKIP_COUNTRY_CODES:
            continue
        if item.get("isActive") is False:
            continue
        base = merged.get(code)
        if base is None:
            continue
        languages = _languages_from_gateway(item.get("languages"), base.languages)
        name = str(item.get("enDefaultName") or item.get("defaultName") or base.name)
        merged[code] = replace(base, name=name, languages=languages)
    return merged


def _languages_from_gateway(raw: Any, fallback: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        return fallback
    default_id: str | None = None
    langs: list[str] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if item.get("active") is False:
            continue
        lang_id = str(item.get("id") or "").strip()
        if not lang_id:
            continue
        if lang_id not in langs:
            langs.append(lang_id)
        if item.get("default") is True:
            default_id = lang_id
    if not langs:
        return fallback
    if default_id and default_id in langs:
        langs.remove(default_id)
        langs.insert(0, default_id)
    return tuple(langs)
