## Token Tracker Implementation Plan

I'll implement a comprehensive token tracking system for the Gemini API calls.

### Architecture

**1. New `token_tracker.py` module** - Core tracking logic
- Track per-request: model, timestamp, input tokens, output tokens, status, duration
- Store usage in `token_usage.json` in AUTA_DIR
- Thread-safe with file locking
- Methods: `record_request()`, `get_stats()`, `get_recent_requests()`

**2. Integration in `llm_client.py`**
- Add token estimation for requests (system_prompt + user_content + images)
- Count actual output tokens from streaming response
- Wrap `_call_gemini()` and `run_grounded_web_research()` with tracking

**3. Integration in `web_server.py`**
- Add `/api/token-usage` endpoint returning JSON stats
- Pass listing slug to tracker for per-listing attribution
- Stream token updates during analysis (e.g., "Tokens sent: X, received: Y")

**4. Simple Dashboard UI**
- Add new page/tab showing token statistics
- Table with recent API calls (model, tokens sent/received, cost estimate, timestamp)
- Summary cards (total requests today, total tokens, estimated cost)
- Per-listing breakdown

### Token Counting Strategy

Since Gemini API doesn't always return exact token counts in streaming mode, we'll:
- **Input**: Approximate via `len(text) / 4` for text, base64 byte count for images
- **Output**: Count characters received and divide by 4 (rough char-to-token ratio)
- Log actual API response usage metadata when available

### File Changes Required

- **New**: `token_tracker.py` (~150 lines)
- **Modify**: `llm_client.py` (~30 lines added)
- **Modify**: `web_server.py` (~50 lines added for endpoint + streaming updates)
- **New**: `web/token-dashboard.html` (simple stats page)

### Safety Considerations
- No modifications to existing analysis logic
- Tracking is additive and can be disabled
- Uses existing AUTA_DIR for storage
- Thread-safe file operations

Does this approach match your expectations? Should I add any specific features like:
- Per-day quota warnings?
- Cost estimation in EUR?
- Export to CSV?