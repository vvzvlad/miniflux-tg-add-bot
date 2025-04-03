import logging
import time
from miniflux import ClientError, Client, ServerError
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

async def update_feed_url(feed_id: int, new_url: str, client) -> tuple[bool, str | None, str | None]:
    """Updates the URL for a specific feed."""
    try:
        await client.update_feed(feed_id, feed_url=new_url)
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

def get_channels_by_category(client: Client, rss_bridge_url: str | None) -> dict[str, list[dict]]:
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
        raise  # Re-raise the exception to be handled by the caller

    # Determine the base URL for filtering if rss_bridge_url is valid
    base_bridge_url = None
    if rss_bridge_url and "{channel}" in rss_bridge_url:
        base_bridge_url = rss_bridge_url.split('{channel}')[0]
        logging.info(f"Filtering feeds based on RSS Bridge base URL: {base_bridge_url}")
    else:
        logging.warning(f"rss_bridge_url is missing or invalid ('{{channel}}' placeholder not found): {rss_bridge_url}. Proceeding without base URL filtering.")
        # Depending on requirements, you might want to return {} or raise an error here.
        # For now, we'll proceed, and parsing will act as the filter.

    bridge_feeds = []
    for feed in feeds:
        feed_url = feed.get("feed_url", "")

        # Skip if base_bridge_url is defined and feed_url doesn't match
        if base_bridge_url and not feed_url.startswith(base_bridge_url):
            continue

        try:
            # Attempt to parse only potentially matching URLs
            parsed_data = parse_feed_url(feed_url)
            channel = parsed_data.get("channel_name")

            # Check if channel was successfully parsed (further confirms bridge structure)
            if channel:
                flags = parsed_data.get("flags") or []
                excluded_text = parsed_data.get("exclude_text") or ""
                merge_seconds = parsed_data.get("merge_seconds") # Fetch merge seconds

                bridge_feeds.append({
                    "id": feed.get("id"), # Include feed ID might be useful
                    "title": feed.get("title", "Unknown"),
                    "channel": channel,
                    "feed_url": feed_url,
                    "flags": flags,
                    "excluded_text": excluded_text,
                    "merge_seconds": merge_seconds, # Add merge seconds here
                    "category_id": feed.get("category", {}).get("id"),
                    # Use category title from feed data directly
                    "category_title": feed.get("category", {}).get("title", "Unknown")
                })
            # Optional: Log feeds that start with base_bridge_url but fail parsing if needed
            # else:
            #     if base_bridge_url and feed_url.startswith(base_bridge_url):
            #         logging.debug(f"URL matched base but parsing failed to find channel: {feed_url}")

        except Exception as parse_error:
            # Log if a URL that passed the base URL check (or if no check was done) fails to parse
            logging.warning(f"Could not parse feed URL '{feed_url}': {parse_error}", exc_info=False) # Avoid full traceback spamming logs maybe
            continue # Skip this feed

    if not bridge_feeds:
        logging.info("No feeds matched the specified RSS Bridge URL pattern and structure.")
        return {} # Return empty dict if none found

    # Sort by category title, then feed title
    bridge_feeds.sort(key=lambda x: (x["category_title"].lower(), x["title"].lower()))

    # Group by category title
    grouped_channels = {}
    for feed in bridge_feeds:
        cat_title = feed["category_title"]
        if cat_title not in grouped_channels:
            grouped_channels[cat_title] = []
        # Add details needed for the /list command or other callers
        grouped_channels[cat_title].append({
            "id": feed["id"], # Pass feed ID through
            "title": feed["title"],
            "flags": feed["flags"],
            "excluded_text": feed["excluded_text"],
            "merge_seconds": feed["merge_seconds"] # Pass merge seconds through
            # Add other fields like 'channel' or 'feed_url' if needed by the caller
        })

    logging.info(f"Grouped {len(bridge_feeds)} bridge channels into {len(grouped_channels)} categories.")
    return grouped_channels 