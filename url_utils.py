import logging
import re
import urllib.parse
import requests
from bs4 import BeautifulSoup

from config import RSS_BRIDGE_URL

def parse_telegram_link(text: str) -> str | None:
    """
    Parses a string to find and extract the channel username/ID from a t.me link.
    Handles formats like:
    - https://t.me/channel_name/123
    - t.me/channel_name/123
    - https://t.me/-1002069358234/1951
    - t.me/-1002069358234/1951
    """
    if not text:
        return None

    # Regex to find t.me URLs
    # Handles optional https://, t.me domain, channel name (alphanumeric/underscore) or numeric ID (with optional minus), message ID (numeric)
    # Updated to also match links without a message ID (e.g., https://t.me/channelname)
    # Make stricter: anchor to start/end, allow optional trailing slash, handle optional https:// correctly
    match = re.search(r"^(?:https?://)?t\.me/([-]?\d+|[a-zA-Z0-9_]+)(?:/\d+)?/?$", text)

    if match:
        # If matched without protocol, channel name is group 1
        # If matched with protocol, channel name is still group 1
        channel_name = match.group(1)
        logging.info(f"Parsed Telegram link: channel='{channel_name}'")
        return channel_name
    else:
        logging.debug(f"No valid t.me link found in text: '{text}'")
        return None

def extract_channel_from_feed_url(feed_url):
    """
    Extract channel username or ID from feed URL based on RSS_BRIDGE_URL.
    """
    # Ensure RSS_BRIDGE_URL is set and the feed_url uses it
    if not RSS_BRIDGE_URL or not feed_url.startswith(RSS_BRIDGE_URL.split("{channel}")[0]):
        # Log if RSS_BRIDGE_URL is missing or feed URL doesn't match expected format
        if not RSS_BRIDGE_URL:
            logging.warning("RSS_BRIDGE_URL is not configured. Cannot extract channel name.")
        else:
            logging.debug(f"Feed URL '{feed_url}' does not match the configured RSS_BRIDGE_URL pattern.")
        return None

    # Handle URLs with {channel} placeholder
    if "{channel}" in RSS_BRIDGE_URL:
        base_part = RSS_BRIDGE_URL.split("{channel}")[0]
        if feed_url.startswith(base_part):
            remaining_url_part = feed_url[len(base_part):]
            # Extract channel name until the next slash or end of the string
            channel = remaining_url_part.split("/")[0] if "/" in remaining_url_part else remaining_url_part
            # Decode URL-encoded characters (like %40 for @)
            decoded_channel = urllib.parse.unquote(channel)
            logging.debug(f"Extracted channel '{decoded_channel}' from feed URL '{feed_url}' using placeholder logic.")
            return decoded_channel
    # Handle URLs where the channel name is appended directly after the base URL
    else:
        # Extract the part after the base URL
        remaining_path = feed_url[len(RSS_BRIDGE_URL):]
        # Remove leading slash if present
        if remaining_path.startswith('/'):
            remaining_path = remaining_path[1:]
        # Take only the part before the next '/' or '?'
        channel_part = remaining_path.split('/')[0].split('?')[0]

        if channel_part:
            decoded_channel = urllib.parse.unquote(channel_part)
            logging.debug(f"Extracted channel '{decoded_channel}' from feed URL '{feed_url}' using suffix logic.")
            return decoded_channel

    # Log if no channel could be extracted despite matching the base URL
    logging.warning(f"Could not extract channel name from feed URL '{feed_url}' despite matching base pattern.")
    return None

def extract_rss_links_from_html(html_content, base_url):
    """
    Extract RSS feed links from HTML content by looking for <link> tags.

    Args:
        html_content: HTML content as string.
        base_url: Base URL for resolving relative links.

    Returns:
        list: List of dictionaries with 'title' and 'href' for each RSS/Atom link found.
    """
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        rss_links = []

        # Find all <link> tags that might contain RSS or Atom feeds
        link_tags = soup.find_all('link', rel='alternate')

        for link in link_tags:
            link_type = link.get('type', '').lower()

            # Check if the type indicates an RSS or Atom feed
            if any(rss_type in link_type for rss_type in ['application/rss+xml', 'application/atom+xml']):
                href = link.get('href', '')
                # Use link title, or default if missing
                title = link.get('title', 'RSS/Atom Feed')

                # Resolve relative URLs using the base URL
                if href and not href.startswith(('http://', 'https://')):
                    try:
                        href = urllib.parse.urljoin(base_url, href)
                        logging.debug(f"Resolved relative URL to: {href}")
                    except Exception as url_join_error:
                        logging.warning(f"Failed to resolve relative URL '{link.get('href')}' against base '{base_url}': {url_join_error}")
                        continue # Skip this link if resolution fails

                if href:
                    rss_links.append({
                        'title': title.strip(),
                        'href': href
                    })

        logging.info(f"Found {len(rss_links)} potential RSS/Atom links in HTML content from {base_url}")
        return rss_links

    except Exception as e:
        logging.error(f"Error extracting RSS/Atom links from HTML content at {base_url}: {e}", exc_info=True)
        return []

def is_valid_rss_url(url):
    """
    Check if the provided URL is a direct RSS/Atom feed or an HTML page containing feed links.

    Args:
        url: URL string to check.

    Returns:
        tuple: (is_direct_feed, result)
            - If direct RSS/Atom feed: (True, url)
            - If HTML with RSS/Atom links: (False, list_of_rss_links)
            - If neither or error: (False, [])
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36'
    }
    valid_feed_types = ['application/rss+xml', 'application/atom+xml', 'application/xml', 'text/xml', 'rss', 'atom']
    html_content_type = 'text/html'

    try:
        # 1. Try HEAD request first for efficiency
        logging.debug(f"Sending HEAD request to {url}")
        head_response = requests.head(url, headers=headers, timeout=10, allow_redirects=True)
        head_response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

        content_type = head_response.headers.get('Content-Type', '').lower().split(';')[0].strip()
        logging.info(f"HEAD request to {url}: Status {head_response.status_code}, Content-Type: {content_type}")

        # Check if Content-Type indicates a direct feed
        if any(feed_type in content_type for feed_type in valid_feed_types):
            logging.info(f"URL {url} identified as a direct RSS/Atom feed via HEAD request.")
            return True, url

        # If HEAD indicates HTML, proceed to GET to check for <link> tags
        if html_content_type in content_type:
            logging.info(f"HEAD request to {url} indicates HTML content. Proceeding with GET request.")
            # Fall through to GET request block below
        # If HEAD Content-Type is neither feed nor HTML, it's likely not what we want
        elif content_type:
             logging.warning(f"HEAD request to {url} returned unexpected Content-Type: {content_type}. Assuming not a direct feed or HTML page.")
             return False, []
        # If Content-Type is missing after HEAD, still try GET
        else:
            logging.warning(f"HEAD request to {url} did not return a Content-Type. Proceeding with GET request.")
            # Fall through to GET request block below

    except requests.exceptions.RequestException as head_error:
        logging.warning(f"HEAD request to {url} failed: {head_error}. Proceeding with GET request.")
        # Fall through to GET request block below if HEAD fails

    try:
        # 2. Perform GET request if HEAD didn't confirm a direct feed or failed
        logging.debug(f"Sending GET request to {url}")
        get_response = requests.get(url, headers=headers, timeout=15)
        get_response.raise_for_status()

        content_type = get_response.headers.get('Content-Type', '').lower().split(';')[0].strip()
        logging.info(f"GET request to {url}: Status {get_response.status_code}, Content-Type: {content_type}")

        # Check Content-Type from GET response for direct feed
        if any(feed_type in content_type for feed_type in valid_feed_types):
            logging.info(f"URL {url} identified as a direct RSS/Atom feed via GET request.")
            return True, url

        # Check if GET response is HTML and extract links
        if html_content_type in content_type:
            logging.info(f"GET request to {url} returned HTML content. Extracting RSS/Atom links.")
            rss_links = extract_rss_links_from_html(get_response.text, url)
            if rss_links:
                logging.info(f"Found {len(rss_links)} RSS/Atom links in HTML from {url}.")
                return False, rss_links
            else:
                logging.info(f"No RSS/Atom links found in HTML from {url}.")
                return False, []

        # If GET response is neither feed nor HTML
        logging.warning(f"GET request to {url} returned unexpected Content-Type: {content_type}. Could not identify as feed or find links.")
        return False, []

    except requests.exceptions.RequestException as get_error:
        logging.error(f"GET request to {url} failed: {get_error}", exc_info=True)
        return False, []
    except Exception as e:
        # Catch potential errors during link extraction as well
        logging.error(f"Error processing URL {url}: {e}", exc_info=True)
        return False, [] 