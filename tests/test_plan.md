# Test Plan for Miniflux-TG Bot (Updated)

## Overall Goal
Increase overall test coverage from 82% to at least 85-90%. Prioritize critical user flows, error handling, and modules with the lowest coverage.

## Coverage Analysis (Current)
*   **Overall:** 82% (↑ 3% from initial)
*   `bot.py`: 80% (156 lines missed) (↑ 6% from initial) - **Highest Priority**
*   `channel_management.py`: 80% (22 lines missed) - Medium Priority
*   `config.py`: 85% (8 lines missed) - Low Priority
*   `miniflux_api.py`: 98% (2 lines missed) - Low Priority (Coverage significantly improved)
*   `url_constructor.py`: 100% (0 lines missed) - Complete
*   `url_utils.py`: 81% (21 lines missed) - Medium Priority

## Progress

### Completed Tests
* ✅ Message chunking tests for `list_channels` in `bot.py` - Covers message chunking logic when listing many feeds
* ✅ Error handling tests for `_handle_awaiting_regex` in `bot.py` - Covers error conditions in regex handling
* ✅ Message parsing tests for `_parse_message_content` in `bot.py` - Covers handling different message types and error conditions

## Detailed Plan by Component

### `bot.py` (80% coverage | 156 missed lines) - HIGH PRIORITY
*   **Focus:** Core application logic, command handlers, callbacks, complex conditional flows, untested error handling paths.
*   **Actions:**
    *   Analyze `term-missing` output (`pytest --cov --cov-report term-missing`) to pinpoint uncovered branches, especially within primary command handlers (`/add`, `/list`, `/remove`, etc.) and callback query handlers.
    *   Add tests for less common user interactions or message sequences.
    *   Focus on `try...except` blocks that lack test cases triggering the exceptions.
    *   Verify correct handling of various message types and potential edge cases (e.g., commands mid-text, unexpected callback data).
    *   Key missed lines to target:
        *   Lines 448-473: Specific branches in handling logic
        *   Lines 790-810: Callback handling logic
        *   Lines 1020-1027, 1050-1051, 1058-1062: Error conditions in other handlers

### `url_utils.py` (81% coverage | 21 missed lines) - MEDIUM PRIORITY
*   **Focus:** URL validation, content fetching, title extraction, error handling during fetch/parse.
*   **Actions:**
    *   Address missed lines `48-75` and `109-111`. These likely relate to specific failure modes in `fetch_title` or URL validation logic.
    *   Test error handling during title fetching (`fetch_title`):
        *   Network timeouts and connection errors (mock `requests.get`).
        *   Handling of non-HTML content types.
        *   Websites without `<title>` tags or with empty/malformed titles.
        *   Parsing errors for malformed HTML.
    *   Test URL validation with more diverse and potentially invalid inputs.

### `channel_management.py` (80% coverage | 22 missed lines) - MEDIUM PRIORITY
*   **Focus:** Channel data persistence, validation, management commands, error handling for file I/O and duplicates.
*   **Actions:**
    *   Address missed lines (`35-38, 74-76, 145-151, 177-180, 207-210, 240-242`). These likely correspond to specific error conditions or edge cases in add/remove/list operations or data loading/saving.
    *   Test channel operations (add, remove, list) with edge-case names (special chars, spaces, non-ASCII, empty, long).
    *   Test error handling for duplicate channel additions or removal of non-existent channels more thoroughly.
    *   Verify data loading/saving logic, simulating file I/O errors (e.g., permission denied, file not found during load).

### `config.py` (85% coverage | 8 missed lines) - LOW PRIORITY
*   **Focus:** Configuration loading, default values, environment variable handling.
*   **Actions:**
    *   Address missed lines `81-92`. Test configuration loading when optional environment variables are *not* set, ensuring default values apply.
    *   Test behaviour with invalid values for environment variables if specific type conversions or validations are performed.

### `miniflux_api.py` (98% coverage | 2 missed lines) - LOW PRIORITY
*   **Focus:** Minor edge cases or specific error conditions.
*   **Actions:**
    *   Identify and cover the remaining 2 lines (`54-55`), likely related to a specific API response or error condition. Low priority unless these represent critical failure paths.

### `url_constructor.py` (100% coverage)
*   **Focus:** None. Coverage is sufficient.

## Testing Strategy Notes
*   Leverage `pytest-mock` heavily to isolate units and simulate external dependencies (APIs, network requests, file system).
*   Use `@pytest.mark.parametrize` to efficiently test functions with multiple input variations and edge cases.
*   Ensure proper use of `pytest-asyncio` for all `async` functions and fixtures.
*   Regularly check coverage reports (`pytest --cov --cov-report term-missing`) to track progress and guide test writing.

## Next Steps
1.  Continue writing tests for `bot.py`, focusing on callback handling (lines 790-810).
2.  Address missed lines in `url_utils.py` with focus on error conditions during URL fetching.
3.  Test file I/O error handling in `channel_management.py`.
4.  Incrementally cover remaining missed lines in low-priority modules.