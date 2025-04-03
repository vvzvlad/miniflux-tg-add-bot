## Updated Test Plan

This plan covers functionality that was previously untested or requires more thorough verification.

### 1. `bot.py` (Current Coverage: ~61% - Requires Significant Improvement)

The main bot module, requiring the most attention.

**1.1. State and User Input Handling:**
    *   **Invalid Input in States:**
        *   Enter `awaiting_regex` state, provide invalid regex. Verify error message and re-entry mechanism.
        *   Enter `awaiting_merge_time` state, provide invalid time (non-numeric, negative, too large). Verify error message and re-entry mechanism.
    *   **Operation Cancellation:**
        *   Send `/cancel` or another command while in `awaiting_regex` or `awaiting_merge_time`. Verify correct state reset and user data cleanup.
    *   **Unexpected Input:**
        *   Send plain text, stickers, or other non-command messages when the bot is in the base state. Ensure the bot ignores them or sends a help message.
    *   **Message Editing:**
        *   Test the bot's reaction to a user editing a message that initially triggered a command or state transition.
    *   **Button Callbacks:**
        *   Test clicking a category selection button (`cat_`) when `MINIFLUX_CLIENT` is unavailable or returns an error on `check_feed_exists`.
        *   Test clicking an RSS feed selection button (`rss_link_`) when `MINIFLUX_CLIENT` is unavailable or returns an error on `check_feed_exists` or `add_feed`.
        *   Test clicking the delete button (`delete|feed_id`) when `get_feeds` or `delete_feed` return an error. Ensure the correct error message is shown to the user.

**1.2. Miniflux API Error Handling (Specific Scenarios):**
    *   **`_handle_awaiting_regex`:**
        *   Simulate `MinifluxApiError` during `get_feed` call. Verify user message and state handling.
        *   Simulate `False` return or `MinifluxApiError` during `update_feed_url_api` call. Verify user message and state handling.
    *   **`_handle_awaiting_merge_time`:**
        *   Simulate `MinifluxApiError` during `get_feed` call. Verify user message and state handling.
        *   Simulate `False` return or `MinifluxApiError` during `update_feed_url_api` call. Verify user message and state handling.
    *   **Error Handling in Main Handlers:**
        *   Simulate API error during `fetch_categories` call in `/start` handler (for new channel).
        *   Simulate API error during `get_feeds` call in message handler (for existing channel).
        *   Simulate API error during `get_channels_by_category` call in `/list` handler.

**1.3. Initialization and Startup:**
    *   Ensure `main()` correctly exits with `sys.exit(1)` if `MINIFLUX_CLIENT` is `None` during initialization.
    *   Ensure `main()` correctly exits with `sys.exit(1)` if `TELEGRAM_TOKEN` is `None`.
    *   Simulate an exception during the `set_my_commands` call in `post_init`. Verify error logging and ensure the bot continues running (or exits gracefully if designed to – check code).

**1.4. Logging:**
    *   Verify the presence, correct format, and level for key log messages in various scenarios: API errors, invalid input, initialization failures.
    *   Ensure no sensitive information (PII) is present in logs.

**1.5. Media Groups:**
    *   Send multiple media group messages in quick succession. Ensure they are handled correctly as a single update or ignored after the first. (Previous plan indicated "DONE", but re-verify edge cases given the coverage).

### 4. `url_utils.py` (Current Coverage: ~89% - Needs Specific Tests)

**4.1. `parse_telegram_link`:**
    *   Test private channel links: `t.me/c/1234567890`, `https://t.me/c/1234567890`. Verify correct ID extraction (`-1001234567890`).
    *   Test private channel message links: `t.me/c/1234567890/123`. Verify correct channel ID extraction (`-1001234567890`).
    *   Test channel mention: `@channelname`. Verify correct name extraction (`channelname`).
    *   Test user profile links: `https://t.me/username`. Verify `None` return.
    *   Test invite links: `t.me/+joinchatlink`. Verify `None` return.
    *   Test non-URL strings: `"plain text"`. Verify `None` return.
    *   Test URLs from other platforms: `https://example.com`. Verify `None` return.

**4.2. `is_valid_rss_url`:**
    *   Mock `requests.get` to return non-XML and non-HTML `Content-Type` (e.g., `application/json`). Verify `False` return.
    *   Mock `requests.get` to raise `requests.exceptions.Timeout`. Verify `False` return and corresponding log entry.
    *   Mock `requests.get` to raise `requests.exceptions.ConnectionError`. Verify `False` return and corresponding log entry.
    *   Test with invalid URL strings (e.g., `"htp://invalid format"`). Verify `False` return (likely `requests` will raise an exception which should be handled).

**4.3. `extract_channel_from_feed_url`:**
    *   Test URLs with variations in the RSS-Bridge path (if applicable, e.g., other query parameters, fragments).
    *   Test URLs resembling RSS-Bridge but not matching the regex (e.g., missing `&action=display`, different parameter names). Verify `None` return.
    *   Test with `None` or an empty string as input. Verify `None` return.

---
