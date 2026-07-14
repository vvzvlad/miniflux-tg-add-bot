"""URL helpers: Telegram link parsing, RSS discovery and feed-URL introspection."""

import logging
import re
import urllib.parse

import requests
from bs4 import BeautifulSoup

from src.settings import settings

# Matches a t.me link anywhere inside a text.
# Supported forms:
#   https://t.me/channel_name/123      -> channel_name
#   t.me/channel_name                  -> channel_name
#   https://t.me/-1002069358234/1951   -> -1002069358234
#   https://t.me/c/1234567890/55       -> 1234567890 (private channel form)
# A trailing query string or fragment (e.g. "?single") is tolerated.
# A t.me link is accepted only when it is either preceded by an explicit
# http(s):// scheme, or, as a bare "t.me/...", at the start of the text or when
# preceded by a character that is not a word character, "." or "/". A bare
# "t.me/..." glued to a host label ("root.me/123"), sitting inside another URL's
# path ("example.com/t.me/foo"), or on a ".me" domain whose label ends in "t"
# ("format.me/page") is therefore NOT matched — those are legitimate feed URLs,
# not Telegram channels.
# The scheme (http/https) is matched case-insensitively, but the "t.me" host is
# matched case-sensitively.
# The lookahead makes sure the link ends here, so non-channel links such as
# t.me/joinchat/<hash> are not mistaken for a channel.
TELEGRAM_LINK_RE = re.compile(
    r"(?:(?i:https?)://t\.me/|(?<![\w./])t\.me/)(?:c/)?(-?\d+|[a-zA-Z0-9_]+)(?:/\d+)*/?(?=[\s?#]|$)"
)


def parse_telegram_link(text: str) -> str | None:
    """Find a t.me link inside the text and return the channel username or id."""
    if not text:
        return None

    match = TELEGRAM_LINK_RE.search(text)
    if match:
        channel_name = match.group(1)
        logging.info(f"Parsed Telegram link: channel='{channel_name}'")
        return channel_name

    logging.debug(f"No valid t.me link found in text: '{text}'")
    return None


def extract_channel_from_feed_url(feed_url):
    """
    Extract the channel username or id from a feed URL, using the configured
    RSS bridge URL template. The template is read at call time on purpose: an
    import-time snapshot goes stale and cannot be patched.
    """
    rss_bridge_url = settings.rss_bridge_url

    if not feed_url or not feed_url.startswith(rss_bridge_url.split("{channel}")[0]):
        logging.debug(f"Feed URL '{feed_url}' does not match the configured RSS_BRIDGE_URL pattern.")
        return None

    base_part = rss_bridge_url.split("{channel}")[0]
    remaining_url_part = feed_url[len(base_part):]
    # The channel name runs until the next slash or the end of the string.
    channel = remaining_url_part.split("/")[0] if "/" in remaining_url_part else remaining_url_part
    # Strip a query string that may directly follow the channel name.
    channel = channel.split("?")[0]

    if channel:
        # Decode URL-encoded characters (e.g. %40 for @).
        decoded_channel = urllib.parse.unquote(channel)
        logging.debug(f"Extracted channel '{decoded_channel}' from feed URL '{feed_url}'.")
        return decoded_channel

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
                        continue  # Skip this link if resolution fails

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

    This performs blocking network requests: callers running inside the event loop
    must wrap it in asyncio.to_thread().

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
        head_response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)

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
