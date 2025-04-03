import logging
import time
from miniflux import ClientError, Client
from url_constructor import parse_feed_url

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
        feeds = client.get_feeds() # Fetches all feeds, could be optimized if API allows filtering
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

def update_feed_url(feed_id: int, new_url: str, client: Client) -> tuple[bool, str | None, str | None]:
    """Updates the feed URL for a given feed ID."""
    try:
        client.update_feed(feed_id, feed_url=new_url)
        # Re-fetch feed to confirm the URL
        updated_feed = client.get_feed(feed_id)
        confirmed_url = updated_feed.get("feed_url")
        logging.info(f"Successfully updated feed {feed_id}. New URL: {confirmed_url}")
        return True, confirmed_url, None
    except ClientError as e:
        error_reason = getattr(e, 'error_message', str(e))
        status_code = getattr(e, 'status_code', 'unknown')
        logging.error(f"Miniflux API error updating feed {feed_id} to URL '{new_url}'. Status: {status_code}, Reason: {error_reason}")
        return False, None, f"Status: {status_code}, Error: {error_reason}"
    except Exception as e:
        logging.error(f"Unexpected error updating feed {feed_id} to URL '{new_url}': {e}", exc_info=True)
        return False, None, f"Unexpected error: {str(e)}"

def get_channels_by_category(client: Client) -> dict[str, list[dict]]:
    """
    Fetches feeds, filters for RSS Bridge channels, groups by category,
    and returns a structured dictionary.

    Args:
        client: Miniflux client instance.

    Returns:
        A dictionary where keys are category titles (or 'Unknown')
        and values are lists of dictionaries, each containing feed details
        (title, channel, feed_url, flags, excluded_text, category_id).
        Returns an empty dictionary if no bridge feeds are found.
    """
    try:
        logging.info("Fetching all feeds to identify bridge channels.")
        feeds = client.get_feeds()
    except Exception as e:
        logging.error(f"Failed to fetch feeds from Miniflux: {e}", exc_info=True)
        # Or raise a custom exception
        raise  # Re-raise the exception to be handled by the caller

    bridge_feeds = []
    for feed in feeds:
        feed_url = feed.get("feed_url", "")
        try:
            parsed_data = parse_feed_url(feed_url)
            channel = parsed_data.get("channel_name")

            if channel:
                flags = parsed_data.get("flags") or []
                excluded_text = parsed_data.get("exclude_text") or ""
                bridge_feeds.append({
                    "title": feed.get("title", "Unknown"),
                    "channel": channel,
                    "feed_url": feed_url,
                    "flags": flags,
                    "excluded_text": excluded_text,
                    "category_id": feed.get("category", {}).get("id"),
                    "category_title": feed.get("category", {}).get("title", "Unknown")
                })
        except Exception as parse_error:
            # Log if a specific URL fails to parse but continue with others
            logging.warning(f"Could not parse feed URL '{feed_url}': {parse_error}", exc_info=True)
            continue # Skip this feed

    if not bridge_feeds:
        logging.info("No feeds identified as RSS Bridge channels.")
        return {} # Return empty dict if none found

    # Sort by category title, then feed title
    bridge_feeds.sort(key=lambda x: (x["category_title"].lower(), x["title"].lower()))

    # Group by category title
    grouped_channels = {}
    for feed in bridge_feeds:
        cat_title = feed["category_title"]
        if cat_title not in grouped_channels:
            grouped_channels[cat_title] = []
        # Add only necessary info for display later
        grouped_channels[cat_title].append({
            "title": feed["title"],
            "flags": feed["flags"],
            "excluded_text": feed["excluded_text"]
            # Add other fields if needed by the caller
        })

    logging.info(f"Grouped {len(bridge_feeds)} bridge channels into {len(grouped_channels)} categories.")
    return grouped_channels 