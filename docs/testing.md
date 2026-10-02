# Testing Strategy and Test Suite Documentation

## Overview

Telegram Migration Pipeline enforces a strict testing discipline:
1. **100% Offline Execution**: Tests never touch real Telegram data centers or make external network calls.
2. **Zero Credential Requirement**: Clean clones run all test suites without needing API IDs, API Hashes, or phone numbers.
3. **High-Risk Domain Coverage**: Tests target failure recovery, state serialization, rate-limit clamping, entity parsing, and asynchronous concurrency boundaries.

All tests utilize Python's standard library `unittest` framework, minimizing test runner overhead and enabling zero-configuration CI execution.

---

## Test Modules and Coverage

### 1. `tests/test_checkpoint.py` (Crash Recovery and Checkpointing)
Validates the persistent checkpointing engine that prevents lost progress during migrations.
* **`test_build_route_key_deterministic`**: Proves that the SHA-256 route hash is strictly deterministic for identical inputs.
* **`test_build_route_key_different_routes`**: Proves that distinct source-target pairs generate distinct checkpoint keys.
* **`test_save_and_load_resume_state`**: Verifies atomic write to disk and subsequent deserialization of `last_id` and metadata.
* **`test_load_nonexistent_resume_state`**: Confirms safe `None` return on missing state files.
* **`test_corrupt_resume_file_fallback`**: Proves that unparseable or corrupted JSON files on disk do not crash the engine, safely logging an operational error and falling back to a clean state.
* **`test_clear_resume_state`**: Ensures completed or reset migrations cleanly delete residual disk checkpoint files.
* **`test_resolve_resume_state_flag`**: Verifies flag priority rules (force-restart vs. auto-resume).
* **`test_sanitize_session_name`**: Verifies that user-supplied session names cannot escape storage folders via path traversal (`../../`).

### 2. `tests/test_rate_limit_backoff.py` (Throttling & Boundary Logic)
Validates numeric boundaries and runtime statistics.
* **`test_normalize_delay_range_valid`**: Validates correct propagation of normal `(min_delay, max_delay)` tuples.
* **`test_normalize_delay_range_inverted`**: Verifies self-healing behavior when `min_delay > max_delay` (e.g. `min=5.0, max=2.0`), ensuring delays are adjusted safely to prevent negative sleep intervals.
* **`test_normalize_delay_range_negative`**: Validates clamp-to-zero when negative delay floats are supplied.
* **`test_stats_dataclass_initialization_and_timing`**: Verifies runtime metrics computation, message counting, and throughput rate tracking.

### 3. `tests/test_link_extractor.py` (Entity Extraction & Export)
Validates content parsing and data export without live channel connections.
* **`test_normalize_url`**: Confirms normalization of raw text links, leading punctuation stripping, and protocol prefixing.
* **`test_extract_tme_handles`**: Confirms detection and extraction of Telegram handles (`@channel`, `t.me/channel`).
* **`test_extract_links_from_plain_text`**: Validates regex-based URL discovery across complex multilingual text.
* **`test_extract_links_from_mock_message_entities`**: Validates extraction from Telegram's structured MTProto entities (`MessageEntityTextUrl`, embedded hyperlinks).
* **`test_export_links_to_json`**: Tests full serialization roundtrip of extracted link models to JSON files.
* **`test_export_links_to_csv_and_excel`**: Verifies RFC 4180 CSV export and verifies graceful fallback when Excel engine is unavailable.

### 4. `tests/test_mock_pipeline.py` (Mock MTProto Ingestion & Concurrency)
Validates end-to-end execution flow by mocking Telethon's asynchronous `TelegramClient`.
* **`test_parse_source_list`**: Validates newline, comma, and whitespace parsing of source channel batches.
* **`test_merge_sources`**: Verifies order preservation and deduplication across manual and queue-file sources.
* **`test_normalize_types`**: Validates case-insensitive message type categorization (`text`, `photo`, `video`, `document`).
* **`test_mock_bulk_forward_batch_mode`**: Simulates Telethon `forward_messages`, asserting chunking into 100-message RPC batches.
* **`test_mock_bulk_forward_anonymize_mode`**: Simulates anonymized forwarding, asserting individual `send_message` and `send_file` invocations.
* **`test_mock_bulk_forward_filter_types`**: Proves that non-matching message types (e.g., photo messages when only text is requested) are skipped without error.
* **`test_mock_bulk_forward_stop_requested`**: Proves that when a cancellation event (`threading.Event`) is set, the asynchronous loop halts immediately, saving intermediate state.

---

## Running Tests

### Command Line
```bash
# Run the entire test suite
python -m unittest discover tests -v

# Run with coverage (optional)
coverage run -m unittest discover tests
coverage report -m
```

### Expected Output
```text
test_corrupt_resume_file_fallback (tests.test_checkpoint.TestCheckpoint) ... ok
test_save_and_load_resume_state (tests.test_checkpoint.TestCheckpoint) ... ok
test_mock_bulk_forward_batch_mode (tests.test_mock_pipeline.TestMockPipeline) ... ok
test_normalize_delay_range_inverted (tests.test_rate_limit_backoff.TestRateLimitBackoff) ... ok
...
----------------------------------------------------------------------
Ran 25 tests in 0.04s

OK
```
