import urllib.parse
from typing import Dict, List, Optional

# Assuming extract_channel_from_feed_url is in url_utils
from url_utils import extract_channel_from_feed_url

# Define constants for query parameters
PARAM_EXCLUDE_FLAGS = "exclude_flags"
PARAM_EXCLUDE_TEXT = "exclude_text"
PARAM_MERGE_SECONDS = "merge_seconds"

def parse_feed_url(feed_url: str) -> Dict[str, Optional[str | List[str] | int]]:
    """
    Parses an RSS-Bridge feed URL and extracts its components.

    Args:
        feed_url: The feed URL string.

    Returns:
        A dictionary containing the extracted components:
        - 'base_url': The URL without the query string.
        - 'channel_name': The extracted channel name or ID.
        - 'flags': A list of flags from the 'exclude_flags' parameter, or None.
        - 'exclude_text': The value of the 'exclude_text' parameter, or None.
        - 'merge_seconds': The integer value of 'merge_seconds', or None.
    """
    parsed_url = urllib.parse.urlparse(feed_url)
    query_params = urllib.parse.parse_qs(parsed_url.query, keep_blank_values=True)

    base_url = urllib.parse.urlunparse((
        parsed_url.scheme,
        parsed_url.netloc,
        parsed_url.path,
        "", "", "" # Remove params, query, fragment for base
    ))

    channel_name = extract_channel_from_feed_url(feed_url) # Use existing utility

    flags = None
    if PARAM_EXCLUDE_FLAGS in query_params:
        flags_str = query_params[PARAM_EXCLUDE_FLAGS][0]
        if flags_str: # Avoid creating [''] for empty param
            flags = flags_str.split(',')

    exclude_text = query_params.get(PARAM_EXCLUDE_TEXT, [None])[0]

    merge_seconds = None
    if PARAM_MERGE_SECONDS in query_params:
        try:
            merge_seconds = int(query_params[PARAM_MERGE_SECONDS][0])
        except (ValueError, IndexError):
            merge_seconds = None # Treat invalid values as None

    return {
        "base_url": base_url,
        "channel_name": channel_name,
        "flags": flags,
        "exclude_text": exclude_text,
        "merge_seconds": merge_seconds,
    }


def build_feed_url(
    base_url: str,
    channel_name: str, # Channel name might be part of the base_url path in some RSS-Bridge setups
    flags: Optional[List[str]] = None,
    exclude_text: Optional[str] = None,
    merge_seconds: Optional[int] = None
) -> str:
    """
    Builds an RSS-Bridge feed URL from its components.

    Args:
        base_url: The base URL (e.g., "http://rss-bridge.org/bridge/TelegramBridge").
                It might already contain the channel name in the path.
        channel_name: The channel name/ID (used for verification and potentially adding to path if needed).
        flags: A list of flags to include in 'exclude_flags'.
        exclude_text: The text/regex for 'exclude_text'.
        merge_seconds: The merge time in seconds for 'merge_seconds'.

    Returns:
        The constructed feed URL string.
    """
    # Assume base_url is the final base path including any channel segment if applicable.
    query_parts = [] # List to hold key=value strings

    if flags:
        # Join flags with comma, DO NOT URL-encode this value here.
        flags_str = ",".join(flags)
        query_parts.append(f"{PARAM_EXCLUDE_FLAGS}={flags_str}")

    # Add exclude_text only if it's a non-empty string
    if exclude_text:
        # URL-encode the value using quote (handles spaces as %20, etc.)
        encoded_text = urllib.parse.quote(exclude_text)
        query_parts.append(f"{PARAM_EXCLUDE_TEXT}={encoded_text}")

    if merge_seconds is not None and merge_seconds > 0:
        # Value is already a string and doesn't need special encoding
        query_parts.append(f"{PARAM_MERGE_SECONDS}={str(merge_seconds)}")

    # Rebuild the URL
    final_url = base_url

    if query_parts:
        # Join the already processed key=value parts with '&'
        query_string = "&".join(query_parts)

        # Append query string
        parsed_base = urllib.parse.urlparse(final_url)
        separator = "&" if parsed_base.query else "?"
        final_url += separator + query_string

    return final_url 