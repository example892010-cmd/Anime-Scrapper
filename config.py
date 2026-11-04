"""Configuration for the Hindi Anime Zone metadata scraper."""

BASE_URL = "https://hindianimeszone.com"
HOME_URL = f"{BASE_URL}/"
CATALOGUE_URL = f"{BASE_URL}/anime-catalogue/"

REQUEST_TIMEOUT_SECONDS = 15
REQUEST_INTERVAL_SECONDS = 1.25  # Be polite: at most about one request per second.
MAX_CATALOGUE_PAGES = 25
DEFAULT_MAX_RESULTS = 5

USER_AGENT = "AnimeMetadataLookup/1.0 (polite metadata-only client)"

# Adult-labelled catalogue entries are intentionally excluded.
ADULT_CONTENT_TERMS = (
    "18+",
    "adult",
    "uncensored",
    "ecchi",
    "hentai",
    "nsfw",
)

# Known non-anime routes which can appear in the site's navigation/footer.
EXCLUDED_PATH_PREFIXES = (
    "/anime-catalogue",
    "/genre",
    "/genres",
    "/series",
    "/movie",
    "/movies",
    "/tag",
    "/category",
    "/dmca",
    "/about",
    "/faq",
    "/privacy-policy",
    "/contact",
    "/watchlist",
    "/wp-admin",
    "/wp-content",
    "/wp-json",
)

QUALITY_LABELS = ("480p", "720p", "1080p")
