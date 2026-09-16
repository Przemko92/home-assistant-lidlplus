"""Country table, locales and gateway overlay."""

from custom_components.lidl_plus.countries import (
    COUNTRIES,
    default_country_code,
    get_country,
    merge_gateway_countries,
    needs_language_step,
)


def test_poland_locale_and_currency():
    country = get_country("PL")
    assert country.locale() == "pl-PL"
    assert country.currency == "PLN"
    assert country.time_zone == "Europe/Warsaw"
    assert not needs_language_step(country)


def test_belgium_needs_language_step():
    country = get_country("BE")
    assert needs_language_step(country)
    assert "fr" in country.languages
    assert country.locale("fr") == "fr-BE"


def test_unknown_country_falls_back_to_poland():
    assert get_country("US").code == "PL"
    assert get_country(None).code == "PL"


def test_hass_country_is_preferred_when_supported():
    assert default_country_code("DE") == "DE"
    assert default_country_code("US") == "PL"
    assert default_country_code(None) == "PL"


def test_gateway_overlay_skips_us_and_unknown():
    merged = merge_gateway_countries(
        [
            {
                "id": "PL",
                "enDefaultName": "Poland (live)",
                "isActive": True,
                "languages": [
                    {"id": "pl", "default": True, "active": True},
                ],
            },
            {"id": "US", "enDefaultName": "United States", "isActive": True},
            {"id": "XX", "enDefaultName": "Nowhere", "isActive": True},
        ]
    )
    assert merged["PL"].name == "Poland (live)"
    assert "US" not in merged
    assert "XX" not in merged
    assert "DE" in merged


def test_every_static_country_has_a_language():
    for country in COUNTRIES.values():
        assert country.languages
        assert "-" in country.locale()
