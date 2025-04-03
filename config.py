import os
import logging
import miniflux

# Configure logging *before* any logging calls
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logging.getLogger("telegram.utils.request").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

# Load environment variables
MINIFLUX_BASE_URL = os.environ.get("MINIFLUX_BASE_URL")
MINIFLUX_USERNAME = os.environ.get("MINIFLUX_USERNAME")
MINIFLUX_PASSWORD = os.getenv("MINIFLUX_PASSWORD")
MINIFLUX_API_KEY = os.environ.get("MINIFLUX_API_KEY") # Note: API Key is loaded but not used if username/password are provided
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
RSS_BRIDGE_URL = os.getenv("RSS_BRIDGE_URL")
ADMIN_USERNAME = os.getenv("ADMIN")
RSS_BRIDGE_TOKEN = os.getenv("RSS_BRIDGE_TOKEN") # Can be None if not set
ACCEPT_CHANNELS_WITHOUT_USERNAME_STR = os.getenv("ACCEPT_CHANNELS_WITHOUT_USERNAME", "false")

# Validate essential configuration
if not MINIFLUX_BASE_URL:
    logging.critical("MINIFLUX_BASE_URL environment variable not set.")
    exit("Missing MINIFLUX_BASE_URL")
if not (MINIFLUX_USERNAME and MINIFLUX_PASSWORD) and not MINIFLUX_API_KEY:
    logging.critical("Miniflux credentials not set. Need MINIFLUX_USERNAME/PASSWORD or MINIFLUX_API_KEY.")
    exit("Missing Miniflux credentials")
if not TELEGRAM_TOKEN:
    logging.critical("TELEGRAM_TOKEN environment variable not set.")
    exit("Missing TELEGRAM_TOKEN")
if not ADMIN_USERNAME:
    logging.warning("ADMIN environment variable not set. No user will be authorized.")
if not RSS_BRIDGE_URL:
    logging.warning("RSS_BRIDGE_URL environment variable not set. Telegram channel subscription will likely fail.")

# Initialize Miniflux client
try:
    # Prefer API Key if provided
    if MINIFLUX_API_KEY:
        logging.info(f"Initializing Miniflux client for {MINIFLUX_BASE_URL} using API Key.")
        miniflux_client = miniflux.Client(MINIFLUX_BASE_URL, api_key=MINIFLUX_API_KEY)
    else:
        logging.info(f"Initializing Miniflux client for {MINIFLUX_BASE_URL} using username/password.")
        miniflux_client = miniflux.Client(MINIFLUX_BASE_URL, username=MINIFLUX_USERNAME, password=MINIFLUX_PASSWORD)
    # Optionally, test connection (e.g., by fetching categories or checking health)
    # miniflux_client.get_categories() # Example check
except Exception as e:
    logging.critical(f"Failed to initialize Miniflux client: {e}", exc_info=True)
    exit(f"Miniflux client initialization failed: {e}")

# Helper functions related to config
def is_admin(username: str | None) -> bool:
    """Check if the provided username matches the admin username."""
    return username == ADMIN_USERNAME

def should_accept_channels_without_username() -> bool:
    """Check if the bot should accept channels without a public username."""
    return ACCEPT_CHANNELS_WITHOUT_USERNAME_STR.lower() == "true"

logging.info("Configuration loaded and Miniflux client initialized.") 