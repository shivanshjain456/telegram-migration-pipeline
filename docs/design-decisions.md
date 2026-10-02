# Architectural Decision Records (ADR)

This document details the critical design decisions, evaluated tradeoffs, and rationale behind the Telegram Migration Pipeline architecture.

---

## ADR 01: MTProto User Client (Telethon) vs. Telegram Bot API

### Context
Telegram provides two distinct API paradigms:
1. **Telegram Bot API**: HTTP-based JSON API interacting through bot tokens.
2. **MTProto User API**: Native binary protocol interacting directly with Telegram data centers using user credentials (`api_id` and `api_hash`).

### Options Evaluated
* **Option A**: Telegram Bot API (`python-telegram-bot` / `aiogram`).
* **Option B**: Native MTProto Client (`Telethon` / `Pyrogram`).

### Decision
We chose **Option B (Telethon MTProto Client)**.

### Rationale
* **Access Scope**: The Bot API requires bot accounts to be explicitly added as administrators to all source channels. For historical archiving, user migration, or restricted channel backups, users often have read-only subscriber access. A user-scoped MTProto client accesses any channel the authenticated user can view.
* **Payload Constraints**: Bot API limits individual file transfers to 50 MB (or 2 GB for specific premium bot setups), whereas user clients can forward and transfer media files up to 2 GB (4 GB with Telegram Premium).
* **Native Batch Forwarding**: Telegram's Bot API lacks the native `forwardMessages` RPC batching capability that allows forwarding up to 100 messages in a single network round-trip.

### Tradeoffs
* Telethon requires phone verification and generates `.session` files containing sensitive binary authentication keys (`auth_key`), demanding strict local file isolation and `.gitignore` hygiene.

---

## ADR 02: Deterministic SHA-256 Hashed JSON Checkpoints vs. SQLite Database

### Context
When transferring millions of messages across unstable networks, processes may crash or be interrupted. The pipeline requires crash-safe checkpointing to record the last successfully processed message ID (`last_id`) per route.

### Options Evaluated
* **Option A**: Centralized SQLite database (`migration_state.db`).
* **Option B**: Key-value JSON state files named by deterministic SHA-256 route hashes.

### Decision
We chose **Option B (Hashed JSON Checkpoints)**.

### Rationale
* **Zero Database Locking**: When multiple CLI instances or queues run concurrently across different routes, a shared SQLite database can encounter database lock contention (`sqlite3.OperationalError: database is locked`).
* **Path Safety**: Channel identifiers in Telegram take various forms (`@channel`, `-100123456789`, `https://t.me/c/123/456`). Using raw route strings as filenames risks illegal filesystem characters on Windows and Linux. Hashing the route (`sha256(f"{source}->{target}".encode()).hexdigest()[:16]`) produces deterministic, clean 16-character alphanumeric filenames (`resume/a1b2c3d4e5f60718.json`).
* **Atomic Recovery**: If a state file becomes corrupted (e.g., system power loss during write), the engine logs the error and falls back gracefully to beginning or user override without corrupting state for other channels.

### Tradeoffs
* Aggregating global statistics across hundreds of past migrations requires iterating through directory files rather than executing a single SQL query.

---

## ADR 03: PyQt6 QThread Event Loop Isolation vs. Qt Async Frameworks

### Context
PyQt6 operates its own C++ event loop (`QEventLoop`) on the main application thread. Telethon requires Python's `asyncio` event loop. Running `asyncio` directly on the main thread freezes the graphical interface.

### Options Evaluated
* **Option A**: Third-party bridge libraries (such as `qasync`).
* **Option B**: Dedicated worker thread (`QThread`) hosting a private `asyncio` event loop and bridging via Qt Signals.

### Decision
We chose **Option B (Dedicated Worker Thread with Private Event Loop)**.

### Rationale
* **Zero Third-Party Fragility**: `qasync` introduces additional dependency constraints and subtle race conditions across PyQt minor versions.
* **Complete Thread Separation**: The GUI thread remains 100% responsive, handling window resizing, log scrolling, and cancellation clicks smoothly regardless of network latency or heavy media processing.
* **Deterministic Lifecycle**: When a user clicks "Stop", the worker thread safely cancels the async task, closes client network connections, and persists the final checkpoint before exiting.

---

## ADR 04: Jittered Uniform Delay vs. Fixed Interval Throttling

### Context
Telegram monitors API call intervals to detect automated bots and scraping behavior. Consistently spaced API calls (e.g. exactly 1.000s apart) trigger rapid heuristics-based rate limits and temporary account bans.

### Options Evaluated
* **Option A**: Fixed interval delay (`sleep(1.0)`).
* **Option B**: Configurable randomized uniform jitter (`random.uniform(min_delay, max_delay)`).

### Decision
We chose **Option B (Configurable Randomized Uniform Jitter)**.

### Rationale
* Random uniform jitter simulates human-like asynchronous read/forward cadence.
* Delay boundaries are validated and normalized: if a user mistakenly provides `min_delay=5.0` and `max_delay=2.0`, the system automatically logs and corrects the range (`max_delay = min_delay + 1.0`) rather than crashing.

---

## ADR 05: Graceful Degradation for Multi-Format Extraction

### Context
`link_extractor.py` extracts links and structured channel metadata, with export capabilities to JSON, CSV, and Excel (XLSX). `openpyxl` requires C extensions or external packages that may not be available on minimal server environments.

### Options Evaluated
* **Option A**: Hard dependency on `openpyxl`, failing if missing.
* **Option B**: Lazy import with graceful fallback to standard library CSV.

### Decision
We chose **Option B (Lazy Import with CSV Fallback)**.

### Rationale
* The core extraction pipeline continues to operate without friction in minimal container environments, micro-VMs, or stripped-down Python runtimes.
* If `openpyxl` is not present when `--format excel` is invoked, the extractor prints a clear notice and writes a standard RFC 4180 CSV file instead.
