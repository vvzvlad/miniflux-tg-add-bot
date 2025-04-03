import os
import logging
import miniflux

# Configure logging *before* any logging calls
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logging.getLogger("telegram.utils.request").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

def load_config():
    """Loads configuration from environment variables and validates essential ones."""
    config = {
        "MINIFLUX_BASE_URL": os.environ.get("MINIFLUX_BASE_URL"),
        "MINIFLUX_USERNAME": os.environ.get("MINIFLUX_USERNAME"),
        "MINIFLUX_PASSWORD": os.getenv("MINIFLUX_PASSWORD"),
        "MINIFLUX_API_KEY": os.environ.get("MINIFLUX_API_KEY"),
        "TELEGRAM_TOKEN": os.getenv("TELEGRAM_TOKEN"),
        "RSS_BRIDGE_URL": os.getenv("RSS_BRIDGE_URL"),
        "ADMIN_USERNAME": os.getenv("ADMIN"),
        "RSS_BRIDGE_TOKEN": os.getenv("RSS_BRIDGE_TOKEN"),
        "ACCEPT_CHANNELS_WITHOUT_USERNAME_STR": os.getenv("ACCEPT_CHANNELS_WITHOUT_USERNAME", "false")
    }

    # Validate essential configuration
    if not config["MINIFLUX_BASE_URL"]:
        logging.critical("MINIFLUX_BASE_URL environment variable not set.")
        raise ValueError("Missing MINIFLUX_BASE_URL")
    if not (config["MINIFLUX_USERNAME"] and config["MINIFLUX_PASSWORD"]) and not config["MINIFLUX_API_KEY"]:
        logging.critical("Miniflux credentials not set. Need MINIFLUX_USERNAME/PASSWORD or MINIFLUX_API_KEY.")
        raise ValueError("Missing Miniflux credentials")
    if not config["TELEGRAM_TOKEN"]:
        logging.critical("TELEGRAM_TOKEN environment variable not set.")
        raise ValueError("Missing TELEGRAM_TOKEN")
    if not config["ADMIN_USERNAME"]:
        # Changed from critical/exit to warning
        logging.warning("ADMIN environment variable not set. No user will be authorized.")
    if not config["RSS_BRIDGE_URL"]:
        # Changed from critical/exit to warning
        logging.warning("RSS_BRIDGE_URL environment variable not set. Telegram channel subscription will likely fail.")
    
    return config

def initialize_miniflux_client(config):
    """Initializes and returns the Miniflux client based on the loaded config."""
    base_url = config["MINIFLUX_BASE_URL"]
    api_key = config["MINIFLUX_API_KEY"]
    username = config["MINIFLUX_USERNAME"]
    password = config["MINIFLUX_PASSWORD"]
    
    try:
        # Prefer API Key if provided
        if api_key:
            logging.info(f"Initializing Miniflux client for {base_url} using API Key.")
            client = miniflux.Client(base_url, api_key=api_key)
        else:
            logging.info(f"Initializing Miniflux client for {base_url} using username/password.")
            client = miniflux.Client(base_url, username=username, password=password)
        # Optionally, test connection here if needed
        # client.ping() # Or client.me() etc.
        logging.info("Miniflux client initialized successfully.")
        return client
    except Exception as e:
        logging.critical(f"Failed to initialize Miniflux client: {e}", exc_info=True)
        # Re-raise the exception or a custom one if specific handling is needed upstream
        raise ConnectionError(f"Miniflux client initialization failed: {e}") from e

# --- Load Config and Initialize Client --- 
# This part runs when the module is imported.
# We wrap it to handle potential errors during load or initialization.
try:
    loaded_config = load_config()
    miniflux_client = initialize_miniflux_client(loaded_config)
    # Make loaded config values easily accessible if needed (optional)
    MINIFLUX_BASE_URL = loaded_config["MINIFLUX_BASE_URL"]
    TELEGRAM_TOKEN = loaded_config["TELEGRAM_TOKEN"]
    RSS_BRIDGE_URL = loaded_config["RSS_BRIDGE_URL"]
    ADMIN_USERNAME = loaded_config["ADMIN_USERNAME"]
    ACCEPT_CHANNELS_WITHOUT_USERNAME_STR = loaded_config["ACCEPT_CHANNELS_WITHOUT_USERNAME_STR"]
    # Add others if they are directly used elsewhere

except (ValueError, ConnectionError) as e:
    # Log the critical error, but don't exit here.
    # The main application (bot.py) should handle this state.
    logging.critical(f"Failed to load configuration or initialize client: {e}")
    # Set client to None to indicate failure
    miniflux_client = None 
    # Set other essential vars to None or default that signals an error state
    MINIFLUX_BASE_URL = None
    TELEGRAM_TOKEN = None
    RSS_BRIDGE_URL = None
    ADMIN_USERNAME = None
    ACCEPT_CHANNELS_WITHOUT_USERNAME_STR = "false"

# Helper functions related to config
def is_admin(username: str | None) -> bool:
    """Check if the provided username matches the admin username."""
    # Ensure ADMIN_USERNAME was loaded successfully
    return ADMIN_USERNAME is not None and username == ADMIN_USERNAME

def should_accept_channels_without_username() -> bool:
    """Check if the bot should accept channels without a public username."""
    return ACCEPT_CHANNELS_WITHOUT_USERNAME_STR.lower() == "true" 