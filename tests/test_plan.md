### Test Plan for Full Coverage

1. `bot.py` (Current Coverage: ~64%)
   ✅ Handling Existing Channels (show options keyboard) - DONE
   ✅ Handling RSS Link Selection (`button_callback` with `rss_link_`) - DONE
   ✅ Editing Merge Time (`edit_merge_time|`, `awaiting_merge_time` state) - DONE
   ✅ Category Selection Handling - DONE
   ✅ Media Group Handling - DONE
   - Miniflux API Error Handling (in various handlers):
       ✅ `list_channels`: `get_channels_by_category` raises exception - DONE
       ✅ `handle_message` (new channel): `fetch_categories` raises exception - DONE
       ✅ `handle_message` (existing channel): `get_feeds`/`get_feed` raises exception - DONE
       ✅ `button_callback` (delete): `get_feeds`/`delete_feed` raises exception - DONE
       - `button_callback` (flags): `get_feed`/`update_feed_url_api` raises exception/returns `False`.
       - `button_callback` (edit regex/merge init): `get_feeds`/`get_feed` raises exception.
       - `_handle_awaiting_regex`/`_handle_awaiting_merge_time`: `get_feed`/`update_feed_url_api` raises exception/returns `False`.
   - Invalid Callback Data:
       ✅ Test `button_callback` with unknown/malformed data (e.g., `cat_abc`, `flag_add_`, `delete|`). Verify "Unknown action" message - DONE
   - `main()` and `post_init()`:
       - Test `main()` runs: check `ApplicationBuilder`, `post_init`, `run_polling` calls.
       - Test `post_init` exception during `set_my_commands`: check logging, bot continues/exits as expected.
       - Test `main()` initialization failure (`TELEGRAM_TOKEN is None`): check critical log, `sys.exit(1)` call.
   - **API Error Handling**
     - ✅ Error handling in `/list` command
     - ✅ Handling get_feeds errors in Telegram channel handler
     - ✅ Handling fetch_categories errors in Telegram channel handler
     - ✅ Error handling in delete feed functionality
     - ✅ Successful deletion of feeds
     - ✅ Unknown callback data
   - **Application Initialization**
     - ✅ Test `main()` initialization success: Application is built with correct handlers and polling starts.
     - ✅ Test `main()` initialization failure (miniflux_client is None or TELEGRAM_TOKEN is None): Exits with sys.exit(1).
     - Test `post_init` success: Correctly sets up bot commands.
     - Test `post_init` exception during `set_my_commands`: check logging, bot continues/exits as expected.

2. `config.py` (Current Coverage: ~85%)
   - ✅ Test environment variable loading (correct values).
   - ✅ Test missing environment variables (defaults used or errors raised).
   - ✅ Test invalid environment variables (`MINIFLUX_API_KEY` empty).
   - ✅ Test `ADMIN_USERNAME` parsing (username validation).

3. `miniflux_api.py` (Current Coverage: ~86%)
   - Test API errors in `fetch_categories`, `check_feed_exists`, `update_feed_url` (ClientError, ServerError, other exceptions).
   - Test `get_channels_by_category` with API error on `get_feeds`.
   - Test `get_channels_by_category` with `rss_bridge_url=None` or invalid URL (check warning log).

4. `url_utils.py` (Current Coverage: ~89%)
   - ✅ `parse_telegram_link`:
       - Test various link formats (`t.me/channel`, `t.me/c/12345`, `t.me/c/12345/67`, `https://`, `http://`, @channel).
       - Test private channel links (`-100...`).
       - Test invalid/non-Telegram/user links (return `None`).
   - ✅ `is_valid_rss_url`:
       - Test valid RSS XML URL.
       - Test HTML URL with RSS link tags.
       - Test HTML URL without RSS link tags.
       - Test URL with non-XML/non-HTML content.
       - Test network errors (timeout, connection error).
       - Test invalid URL format.
   - ✅ `extract_channel_from_feed_url`:
       - Test different RSS-Bridge URL structures.
       - Test URLs not matching the expected pattern. 