# System Architecture

## Overview

Telegram Migration Pipeline is a high-throughput, crash-resilient data migration and extraction engine built in Python. It transfers message history, media, and structural content between Telegram channels, supergroups, and private discussions using user-level MTProto protocol connections via Telethon.

The system is designed around four decoupled layers:
1. **Presentation Layer**: Dual interfaces (PyQt6 Desktop GUI and Rich-powered Headless CLI).
2. **Concurrence & Worker Bridge**: Thread-isolated `asyncio` event loop runner maintaining non-blocking UI responsiveness.
3. **Execution Engine (`utils.py`)**: Asynchronous batching, rate-limiting jitter, adaptive FloodWait backoff, and crash-safe state checkpointing.
4. **Extraction & Transformation Engine (`link_extractor.py`)**: Offline entity parsing, URL normalization, metadata classification, and multi-format export (JSON, CSV, Excel).

---

## Architectural Diagram

```
+-----------------------------------------------------------------------------------+
|                                PRESENTATION LAYER                                 |
|                                                                                   |
|   +----------------------------------+     +----------------------------------+   |
|   |         PyQt6 Desktop GUI        |     |       Rich Headless CLI          |   |
|   |      (main.py - QMainWindow)     |     |     (cli.py - Rich Console)      |   |
|   +-----------------+----------------+     +-----------------+----------------+   |
+---------------------|----------------------------------------|--------------------+
                      |                                        |
                      | Qt Signal / Slot                       | Asyncio Direct Run
                      v                                        v
+-----------------------------------------------------------------------------------+
|                        CONCURRENCY & WORKER BRIDGE                                |
|                                                                                   |
|   +---------------------------------------------------------------------------+   |
|   |   ForwardWorker (PyQt6 QThread)                                           |   |
|   |   - Owns private asyncio event loop (asyncio.new_event_loop())            |   |
|   |   - Bridges Telethon async callbacks -> Qt Thread-Safe Signals            |   |
|   |   - Handles graceful cancellation via stop_requested threading Event      |   |
|   +---------------------------------------------------------------------------+   |
+--------------------------------------|--------------------------------------------+
                                       |
                                       v
+-----------------------------------------------------------------------------------+
|                        EXECUTION ENGINE (utils.py)                                |
|                                                                                   |
|   +--------------------+    +--------------------+    +-----------------------+   |
|   |   Route Manager    |    |  Adaptive Throttle |    |   Checkpoint State    |   |
|   |  - Route Key Hash  |    |  - Jitter Delay    |    |   - Atomic JSON Store |   |
|   |  - Queue Resolver  |    |  - FloodWait Catch |    |   - resume/<hash>.json|   |
|   +--------------------+    +--------------------+    +-----------------------+   |
|                                      |                                            |
|                                      v                                            |
|   +---------------------------------------------------------------------------+   |
|   |   Telethon MTProto Transport Layer                                        |   |
|   |   - Forward Messages (Batch RPC: up to 100 msgs/call)                     |   |
|   |   - Anonymized Re-Send (Media download buffer -> upload stream)           |   |
|   |   - Client Reconnect Recovery (WinError 64 / Broken Pipe handler)         |   |
|   +---------------------------------------------------------------------------+   |
+--------------------------------------|--------------------------------------------+
                                       |
                                       v
+-----------------------------------------------------------------------------------+
|                     EXTRACTION & ANALYTICS (link_extractor.py)                    |
|                                                                                   |
|   - Regex & Entity Parser (MessageEntityTextUrl, MessageEntityMention, Links)     |
|   - URL Normalizer & Validator (https, t.me, @handle cleanups)                    |
|   - Multi-format Exporter (JSON stream, RFC 4180 CSV, OpenPyXL XLSX)              |
+-----------------------------------------------------------------------------------+
```

---

## Component Deep Dive

### 1. Dual Interface Layer
* **`cli.py`**: Built for continuous server execution, cron jobs, and terminal power users. Integrates `rich.live.Live` with `rich.progress.Progress`, presenting real-time transfer throughput (msg/s), batch status, and remaining queue items. Listens to `SIGINT`/`SIGTERM` for clean shutdown.
* **`main.py`**: Built for desktop operators. Provides an interactive form for source queue setup, account credential selection, target channel input, type filtering (text, photo, document, video, sticker, voice), and live log streaming.

### 2. PyQt6 QThread to Asyncio Event Loop Bridge
A core technical challenge in Python desktop applications is integrating an asynchronous I/O framework (`asyncio`/Telethon) with an event-driven GUI framework (`PyQt6`). If `asyncio.run()` is invoked on the Qt GUI main thread, the entire UI freezes.

**Solution**:
* `ForwardWorker` inherits from `QThread`.
* In its `run()` method, it spins up an isolated, dedicated OS thread and assigns it a fresh `asyncio.new_event_loop()`.
* It invokes `loop.run_until_complete(bulk_forward(...))`.
* Progress metrics, error logs, and batch completions are converted from asynchronous Python objects into Qt Signals (`sig_progress`, `sig_log`, `sig_finished`), which the Qt event loop receives safely on the main UI thread.

### 3. Execution Engine (`utils.py`)

#### A. Route Key Hashing
Resume states must be uniquely isolated per source-target combination without being susceptible to filesystem-illegal characters (like `/` or `:` in Telegram URLs).
* `build_route_key(source, target)` computes a deterministic SHA-256 hash across both identifiers:
  ```python
  key_raw = f"{str(source).strip().lower()}->{str(target).strip().lower()}"
  return hashlib.sha256(key_raw.encode("utf-8")).hexdigest()[:16]
  ```
* Resume states are safely saved to `resume/<hash>.json`.

#### B. Native Batching vs. Anonymized Forwarding
Telegram MTProto supports two distinct forward modalities:
1. **Native Forward (`forward_messages`)**:
   * Forwards a list of message IDs (up to 100 messages) in a single RPC round-trip.
   * Retains the original channel attribution ("Forwarded from...").
   * Extremely bandwidth-efficient because media is copied server-side on Telegram data centers without client re-downloading.
2. **Anonymized Re-Send (`send_message` / `send_file`)**:
   * Required when users wish to strip original author tags or bypass forward restrictions.
   * Telethon downloads media to an in-memory byte buffer and uploads it freshly to the target channel.
   * Transferred strictly message-by-message with delay jitter to satisfy strict per-channel send quotas.

#### C. Adaptive FloodWait Backoff
Telegram enforces aggressive rate limits on API calls. When an MTProto limit is triggered, Telegram responds with `FloodWaitError` containing `seconds`.
* The engine catches `telethon.errors.FloodWaitError`.
* It logs a clear operational warning indicating the forced sleep duration.
* It safely pauses using non-blocking `asyncio.sleep(err.seconds + 1)` and then retries the operation automatically.
* Progress is not lost: intermediate checkpoints remain saved up to the last successful message batch.

### 4. Link & Content Extractor (`link_extractor.py`)
Provides an offline analysis utility that scans channels for links, mentions, and shared resources without modifying channel states. Extracted entries include:
* Source message ID and timestamp.
* Link URL (normalized to valid HTTP/HTTPS schemes).
* Extracted Telegram channel / user handles (`@channel`).
* Domain classification and context text snippet.
* Exports directly into JSON, CSV, or formatted Excel spreadsheets.
