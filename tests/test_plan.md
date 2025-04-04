# Test Plan for Miniflux-TG Bot (Updated)

## Overall Goal
Increase overall test coverage from 75% to at least 85-90%. Prioritize critical user flows, error handling, and modules with the lowest coverage.

## Coverage Analysis (Current)
*   **Overall:** 75%
*   `bot.py`: 70% (222 lines missed) - **Highest Priority**
*   `channel_management.py`: 80% (22 lines missed) - Medium Priority
*   `config.py`: 85% (8 lines missed) - Low Priority
*   `miniflux_api.py`: 84% (13 lines missed) - Medium Priority
*   `url_constructor.py`: 100% (0 lines missed) - Complete
*   `url_utils.py`: 81% (21 lines missed) - Medium Priority

## Detailed Plan by Component

### `bot.py` (70% coverage | 222 missed lines) - HIGH PRIORITY
*   **Focus:** Core application logic, command handlers, callbacks, error handling.
*   **Actions:**
    *   Identify and cover untested execution paths within primary command handlers (`/add`, `/list`, `/remove`, etc.). Use `pytest --cov --cov-report=html` for detailed line analysis.
    *   Implement tests for error handling scenarios in callbacks:
        *   Miniflux API errors (connection issues, invalid credentials, entry not found).
        *   Telegram API errors (message sending failures, rate limits).
        *   Invalid user input patterns.
        *   Errors during URL fetching/processing (`url_utils` exceptions).
    *   Test conversation flows and state management where applicable.
    *   Verify correct handling of various message types (commands, text, URLs) and potential edge cases (e.g., commands in the middle of text).

### `url_utils.py` (81% coverage | 21 missed lines) - MEDIUM PRIORITY
*   **Focus:** URL validation, content fetching, title extraction.
*   **Actions:**
    *   Add tests for URL validation edge cases:
        *   Non-HTTP/HTTPS schemes (`ftp://`, `mailto:`).
        *   Malformed URLs.
        *   Internationalized Domain Names (IDNs).
        *   URLs with unusual characters or encodings.
    *   Test error handling during title fetching (`fetch_title`):
        *   Network timeouts and connection errors (mock `requests.get`).
        *   Handling of non-HTML content types.
        *   Websites without `<title>` tags or with empty titles.
        *   Parsing errors for malformed HTML.
    *   Test extraction logic with diverse HTML structures.

### `miniflux_api.py` (84% coverage | 13 missed lines) - MEDIUM PRIORITY
*   **Focus:** Interaction with Miniflux API, including authentication, retries, and error mapping.
*   **Actions:**
    *   Test retry logic: Mock API responses to simulate transient failures and verify that retries occur as expected.
    *   Test handling of specific Miniflux API error status codes (e.g., 401 Unauthorized, 404 Not Found, 5xx Server Errors).
    *   Simulate API call timeouts.
    *   Verify correct payload construction for different API calls (`create_entry`, `get_feed_entries`, etc.).

### `channel_management.py` (80% coverage | 22 missed lines) - MEDIUM PRIORITY
*   **Focus:** Channel data persistence, validation, and management commands.
*   **Actions:**
    *   Test channel operations (add, remove, list) with edge-case names:
        *   Names containing special characters (`!@#$%^&*()`, spaces, non-ASCII).
        *   Empty or excessively long names.
    *   Test error handling for duplicate channel additions or removal of non-existent channels.
    *   Verify data loading/saving logic, potentially simulating file I/O errors.

### `config.py` (85% coverage | 8 missed lines) - LOW PRIORITY
*   **Focus:** Configuration loading, default values, environment variable handling.
*   **Actions:**
    *   Test configuration loading when optional environment variables are *not* set, ensuring default values are correctly applied.
    *   Test behaviour with invalid values for environment variables if specific type conversions or validations are performed.

### `url_constructor.py` (100% coverage)
*   **Focus:** None. Coverage is sufficient.

## Testing Strategy Notes
*   Leverage `pytest-mock` heavily to isolate units and simulate external dependencies (APIs, network requests).
*   Use `@pytest.mark.parametrize` to efficiently test functions with multiple input variations and edge cases.
*   Ensure proper use of `pytest-asyncio` for all `async` functions and fixtures.
*   Regularly generate HTML coverage reports (`pytest --cov --cov-report=html`) to visualize missed lines and guide test writing.

## Next Steps
1.  Generate an HTML coverage report for detailed analysis (`pytest --cov --cov-report=html`).
2.  Begin implementing new tests, starting with the highest priority module: `bot.py`.