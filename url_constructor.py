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
    # Ensure channel name is properly encoded if it needs to be added to path
    # This logic assumes channel_name might NOT be in base_url yet,
    # which aligns with the original create_feed logic using .replace or appending.
    # Let's refine this: Assume base_url provided IS the final base path for the bridge.
    # We might need to adjust how base_url is determined before calling this.

    # Let's stick to the query parameter logic primarily.
    query_params = {}

    if flags:
        query_params[PARAM_EXCLUDE_FLAGS] = ",".join(flags)
    # Ensure exclude_text is added even if it's an empty string (but not None)
    if exclude_text is not None:
        query_params[PARAM_EXCLUDE_TEXT] = exclude_text
    if merge_seconds is not None and merge_seconds > 0: # Don't add if 0 or None
        query_params[PARAM_MERGE_SECONDS] = str(merge_seconds)

    # Rebuild the URL
    # Start with the provided base_url
    final_url = base_url

    if query_params:
        # Encode the parameters correctly
        query_string = urllib.parse.urlencode(query_params, doseq=True)
        # Append query string
        separator = "?" if "?" not in final_url else "&"
        final_url += separator + query_string

    return final_url 