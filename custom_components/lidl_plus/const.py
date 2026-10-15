"""Constants for the unofficial Lidl Plus integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "lidl_plus"

AUTH_BASE: Final = "https://accounts.lidl.com"
AUTH_URL: Final = f"{AUTH_BASE}/connect/authorize"
TOKEN_URL: Final = f"{AUTH_BASE}/connect/token"
CLIENT_ID: Final = "LidlPlusNativeClient"
CLIENT_SECRET: Final = "secret"
REDIRECT_URI: Final = "com.lidlplus.app://callback"
AUTH_SCOPES: Final = "openid profile offline_access lpprofile lpapis"
COMPANION_WAIT: Final = {
    "event": "http_redirect",
    "status_codes": [302],
    "location_prefixes": [REDIRECT_URI],
}

APP_VERSION: Final = "17.11.6"
APP_PACKAGE: Final = "com.lidl.eci.lidlplus"
OPERATING_SYSTEM: Final = "Android"
OS_VERSION: Final = "14"
API_USER_AGENT: Final = (
    "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
)

PROFILE_BASE: Final = "https://profile.lidlplus.com/api"
TICKETS_BASE: Final = "https://tickets.lidlplus.com/api"
COUPONS_BASE: Final = "https://coupons.lidlplus.com/app/api"
SEGMENTS_BASE: Final = "https://segments.lidlplus.com/api"
COUNTRIES_URL: Final = "https://appgateway.lidlplus.com/configurationapp/v3/countries"
HOME_BASE: Final = "https://home.lidlplus.com"
DEPOSITS_BASE: Final = "https://deposits.lidlplus.com/api"
ALERTS_BASE: Final = "https://alerts.lidlplus.com/api"
STORES_BASE: Final = "https://stores.lidlplus.com/api"
LOTTERY_BASE: Final = "https://purchaselottery.lidlplus.com/api"
SHOPPING_LIST_BASE: Final = "https://shopping-list.lidlplus.com/api"

ACTION_LOCATION: Final = "COUPON_LIST"

CONF_ACCESS_TOKEN: Final = "access_token"
CONF_REFRESH_TOKEN: Final = "refresh_token"
CONF_COUNTRY: Final = "country"
CONF_LANGUAGE: Final = "language"
CONF_CARD_NUMBER: Final = "card_number"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_AUTO_COUPONS: Final = "auto_coupons"
CONF_AUTO_SCRATCH: Final = "auto_scratch"
CONF_SHOPPING_LISTS: Final = "shopping_lists"
CONF_REDIRECT_URL: Final = "redirect_url"

DEFAULT_SCAN_INTERVAL = timedelta(minutes=15)
DEFAULT_AUTO_COUPONS: Final = False
DEFAULT_AUTO_SCRATCH: Final = False
DEFAULT_SHOPPING_LISTS: Final = True

PLATFORMS: Final = ["sensor", "binary_sensor", "button", "switch", "event", "todo"]

MAX_REWARD_HISTORY: Final = 20
MAX_RECEIPTS: Final = 5
STORAGE_VERSION: Final = 1
ATTRIBUTION: Final = "Data from the unofficial Lidl Plus API"
