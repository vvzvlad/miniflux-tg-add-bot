"""Miniflux client lifecycle and the synchronous API calls the handlers use.

Every function here performs blocking network I/O: async callers must wrap them
in asyncio.to_thread() so the event loop is never blocked.
"""

import logging

import miniflux
from miniflux import Client, ClientError, ServerError

from src.settings import settings
from src.url_constructor import parse_feed_url

_client: Client | None = None


def get_client() -> Client:
    """Return the lazily-built, cached Miniflux client.

    Handlers must call this at call time instead of importing the client object,
    so the client can be replaced (e.g. in tests) and never goes stale.
    """
    global _client
    if _client is None:
        if settings.miniflux_api_key:
            logging.info(f"Initializing Miniflux client for {settings.miniflux_base_url} using API key.")
            _client = miniflux.Client(settings.miniflux_base_url, api_key=settings.miniflux_api_key)
        else:
            logging.info(f"Initializing Miniflux client for {settings.miniflux_base_url} using username/password.")
            _client = miniflux.Client(
                settings.miniflux_base_url,
                username=settings.miniflux_username,
                password=settings.miniflux_password,
            )
        logging.info("Miniflux client initialized successfully.")
    return _client


def fetch_categories(client):
    """
    Fetch categories from the Miniflux API using the miniflux client.
    This function accesses the API endpoint '/categories' via the client's methods.
    """
    try:
        logging.info("Requesting categories from Miniflux API endpoint '/categories'")
        categories = client.get_categories()
        logging.info(f"Successfully fetched {len(categories)} categories from the API")
        return categories
    except Exception as error:
        # Attempt to get more detailed error info if available
        response_content = getattr(error, "text", "No response content available")
        status_code = getattr(error, "status_code", "N/A")
        logging.error(
            f"Error fetching categories from Miniflux API endpoint '/categories'. Status: {status_code}. Error: {error}. Response: {response_content}",
            exc_info=True
        )
        raise


def check_feed_exists(client, feed_url):
    """
    Check if a feed with the specified URL already exists in subscriptions.
    """
    try:
        logging.debug(f"Checking if feed exists with URL: {feed_url}")
        feeds = client.get_feeds()
        exists = any(feed["feed_url"] == feed_url for feed in feeds)
        logging.info(f"Feed with URL {feed_url} {'exists' if exists else 'does not exist'} in subscriptions.")
        return exists
    except Exception as error:
        status_code = getattr(error, "status_code", "N/A")
        logging.error(
            f"Failed to check existing feeds in Miniflux. Status: {status_code}. Error: {error}",
            exc_info=True
        )
        raise


def find_feed_by_channel(client, channel_name: str) -> dict | None:
    """Find the feed subscribed for a given Telegram channel.

    Scans all feeds and returns the first one whose feed URL parses to a matching
    channel name (case-insensitive), or None if the channel is not subscribed.
    """
    feeds = client.get_feeds()
    for feed in feeds:
        feed_url = feed.get("feed_url", "")
        parsed_data = parse_feed_url(feed_url)
        existing_channel_name = parsed_data.get("channel_name")
        if existing_channel_name and existing_channel_name.lower() == channel_name.lower():
            logging.info(f"Found existing feed for channel '{channel_name}': ID={feed.get('id')}, URL={feed_url}")
            return feed

    logging.info(f"No feed found for channel '{channel_name}'.")
    return None


def update_feed_url(feed_id: int, new_url: str, client) -> tuple[bool, str | None, str | None]:
    """Updates the URL for a specific feed."""
    try:
        client.update_feed(feed_id, feed_url=new_url)
        logging.info(f"Successfully updated feed URL for feed ID {feed_id} to: {new_url}")
        return True, new_url, None
    except (ClientError, ServerError) as error:
        status_code = getattr(error, 'status_code', 'unknown')
        try:
            error_reason = error.get_error_reason()
        except AttributeError:
            error_reason = str(error)
        error_message = f"Status: {status_code}, Error: {error_reason}"
        logging.error(f"Miniflux API error while updating URL for feed {feed_id}: {error_message}")
        return False, None, error_message
    except Exception as e:
        logging.error(f"Unexpected error updating feed URL for feed {feed_id}: {e}", exc_info=True)
        return False, None, str(e)


def delete_feed(client, feed_id: int) -> tuple[bool, str | None]:
    """Delete a feed. Returns (success, error_message)."""
    try:
        client.delete_feed(feed_id)
        logging.info(f"Successfully deleted feed ID {feed_id}")
        return True, None
    except (ClientError, ServerError) as error:
        status_code = getattr(error, 'status_code', 'unknown')
        try:
            error_reason = error.get_error_reason()
        except AttributeError:
            error_reason = str(error)
        error_message = f"Status: {status_code}, Error: {error_reason}"
        logging.error(f"Miniflux API error while deleting feed {feed_id}: {error_message}")
        return False, error_message
    except Exception as e:
        logging.error(f"Unexpected error deleting feed {feed_id}: {e}", exc_info=True)
        return False, str(e)


def get_channels_by_category(client: Client, rss_bridge_url: str | None) -> dict[str, list[dict]]:
    """
    Fetches feeds, filters for RSS Bridge channels, groups by category,
    and returns a structured dictionary.

    Args:
        client: Miniflux client instance.
        rss_bridge_url: The configured RSS bridge URL template.

    Returns:
        A dictionary where keys are category titles (or 'Unknown')
        and values are lists of dictionaries, each containing feed details
        (id, title, flags, excluded_text, merge_seconds).
        Returns an empty dictionary if no bridge feeds are found.
    """
    try:
        logging.info("Fetching all feeds to identify bridge channels.")
        feeds = client.get_feeds()
    except Exception as e:
        logging.error(f"Failed to fetch feeds from Miniflux: {e}", exc_info=True)
        raise  # Re-raise the exception to be handled by the caller

    # Determine the base URL used to filter bridge feeds
    base_bridge_url = None
    if rss_bridge_url and "{channel}" in rss_bridge_url:
        base_bridge_url = rss_bridge_url.split('{channel}')[0]
        logging.info(f"Filtering feeds based on RSS Bridge base URL: {base_bridge_url}")
    else:
        logging.warning(
            f"rss_bridge_url is missing or invalid ('{{channel}}' placeholder not found): {rss_bridge_url}. "
            "Proceeding without base URL filtering."
        )

    bridge_feeds = []
    for feed in feeds:
        feed_url = feed.get("feed_url", "")

        # Skip if base_bridge_url is defined and feed_url doesn't match
        if base_bridge_url and not feed_url.startswith(base_bridge_url):
            continue

        try:
            parsed_data = parse_feed_url(feed_url)
            channel = parsed_data.get("channel_name")

            # A parsed channel name confirms the bridge structure
            if channel:
                bridge_feeds.append({
                    "id": feed.get("id"),
                    "title": feed.get("title", "Unknown"),
                    "channel": channel,
                    "feed_url": feed_url,
                    "flags": parsed_data.get("flags") or [],
                    "excluded_text": parsed_data.get("exclude_text") or "",
                    "merge_seconds": parsed_data.get("merge_seconds"),
                    "category_id": feed.get("category", {}).get("id"),
                    "category_title": feed.get("category", {}).get("title", "Unknown"),
                })

        except Exception as parse_error:
            logging.warning(f"Could not parse feed URL '{feed_url}': {parse_error}", exc_info=False)
            continue  # Skip this feed

    if not bridge_feeds:
        logging.info("No feeds matched the specified RSS Bridge URL pattern and structure.")
        return {}

    # Sort by category title, then feed title
    bridge_feeds.sort(key=lambda x: (x["category_title"].lower(), x["title"].lower()))

    # Group by category title
    grouped_channels: dict[str, list[dict]] = {}
    for feed in bridge_feeds:
        cat_title = feed["category_title"]
        grouped_channels.setdefault(cat_title, []).append({
            "id": feed["id"],
            "title": feed["title"],
            "flags": feed["flags"],
            "excluded_text": feed["excluded_text"],
            "merge_seconds": feed["merge_seconds"],
        })

    logging.info(f"Grouped {len(bridge_feeds)} bridge channels into {len(grouped_channels)} categories.")
    return grouped_channels
