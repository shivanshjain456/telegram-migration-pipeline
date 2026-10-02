# Telegram Migration Pipeline

> High-throughput, crash-resilient data migration and extraction engine for Telegram channels, supergroups, and media archives.

[![CI](https://github.com/shivanshjain456/telegram-migration-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/shivanshjain456/telegram-migration-pipeline/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-25%20passed-brightgreen)](tests/)

---

## 60-Second Executive Summary

Telegram Migration Pipeline is an asynchronous data pipeline built in Python to migrate, archive, and extract message histories and media between Telegram channels and groups.

* **Problem**: Channel archiving and migration often fails when dealing with Telegram FloodWait rate limits, network disconnects (`WinError 64`), UI freezing, and lost progress on power interruptions.
* **Solution**: A decoupled architecture with a PyQt6 desktop GUI and Rich headless CLI, powered by native MTProto RPC batching (100 msgs/call), deterministic SHA-256 crash checkpoints, adaptive FloodWait backoff, and a dedicated thread-to-asyncio event-loop bridge.
* **Target Audience**: Community managers, system administrators, and developers managing channel migrations, backups, or offline link extraction.

---

## Why This Project Exists

Standard Telegram tools rely on simple Bot APIs with severe limitations (50 MB upload ceilings, admin-only channel restrictions, single-message forward limits) or brittle automation scripts that freeze when rate-limited.

This project was engineered to solve three specific challenges:
1. **Zero UI Freezing**: Bridges PyQt6's C++ GUI event loop and Python's `asyncio` loop through an isolated `QThread`, maintaining 60 FPS responsiveness during high-volume transfers.
2. **Crash-Safe Checkpointing**: Saves atomic state files keyed by SHA-256 hashes of source-to-target routes. If interrupted by network drops or power failure, transfers resume seamlessly from the exact message ID where they stopped.
3. **Adaptive Flood Control**: Detects server-side `FloodWaitError` responses, pauses execution cooperatively for the mandated duration, and resumes without dropping pending message queues.

---

## Core Architecture

```text
+-----------------------------------------------------------------------------------+
|                                PRESENTATION TIER                                  |
|                                                                                   |
|     +-----------------------------+         +-----------------------------+       |
|     |      PyQt6 Desktop GUI      |         |     Rich Headless CLI       |       |
|     |  (Interactive App Runner)   |         |  (Server / Daemon Runner)   |       |
|     +--------------+--------------+         +--------------+--------------+       |
+--------------------|---------------------------------------|----------------------+
                     |                                       |
                     | Qt Signals / Slots                    | Direct Asyncio Run
                     v                                       v
+-----------------------------------------------------------------------------------+
|                          CONCURRENCY & WORKER BRIDGE                              |
|                                                                                   |
|     ForwardWorker (PyQt6 QThread)                                                 |
|     - Hosts private asyncio event loop (asyncio.new_event_loop())                 |
|     - Dispatches thread-safe UI signals (sig_progress, sig_log, sig_finished)   |
|     - Manages graceful cancellation via threading.Event                           |
+-----------------------------------------------------------------------------------+
                                     |
                                     v
+-----------------------------------------------------------------------------------+
|                         EXECUTION ENGINE (utils.py)                               |
|                                                                                   |
|     [Route Manager]           [Adaptive Throttle]         [Checkpoint Engine]     |
|     SHA-256 Route Hashing     Jitter Delay (0.8s - 2.5s)  Atomic JSON Writes      |
|     Channel Queue Resolver    FloodWait Cooldown Pause    resume/<hash>.json      |
+-----------------------------------------------------------------------------------+
                                     |
                                     v
+-----------------------------------------------------------------------------------+
|                        TELETHON MTPROTO TRANSPORT LAYER                           |
|                                                                                   |
|     * Batch Forwarding: Native RPC forward_messages (up to 100 msgs/call)         |
|     * Anonymized Re-Send: In-memory media stream buffer -> send_file upload       |
|     * Socket Reconnect Handler: Automatic TCP handshake recovery                  |
+-----------------------------------------------------------------------------------+
                                     |
                                     v
+-----------------------------------------------------------------------------------+
|                   EXTRACTION & ANALYTICS (link_extractor.py)                      |
|                                                                                   |
|     - Regex & MTProto MessageEntityTextUrl extraction                             |
|     - URL normalization and domain classification                                 |
|     - Export formats: JSON stream, RFC 4180 CSV, formatted Excel spreadsheets     |
+-----------------------------------------------------------------------------------+
```

---

## Key Features

* **High-Throughput Batching**: Forwards up to 100 messages per network round-trip using native MTProto RPC methods, reducing network latency by over 90% compared to sequential forwarding.
* **Deterministic Route Checkpointing**: Progress states are stored under `resume/<sha256_hash>.json`, preventing path traversal vulnerabilities and allowing instant resumption after crashes.
* **Anonymized Forward Mode**: Strips source channel attributions by downloading media buffers in-memory and re-uploading freshly with original captions.
* **Content Filtering**: Filters transferred messages by type (`text`, `photo`, `video`, `document`, `voice`, `sticker`).
* **Rich Headless CLI**: Command-line interface featuring animated progress bars, live transfer speed telemetry (msgs/sec), and queue file support.
* **Offline Link Extraction**: Scans channels for external URLs, channel handles, and domain distributions, exporting to JSON, CSV, and Excel without modifying channel history.

---

## Terminal Telemetry Example

```text
[12:30:15] [INFO] Connecting to MTProto Gateway... Authenticated as @operator
[12:30:16] [INFO] Route: @source_channel -> @backup_vault [Route Key: 7f8a9b1c2d3e4f50]
[12:30:16] [INFO] Resuming from checkpoint: last_id = 4520

Forwarding Messages ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 78% 7,800/10,000 [00:04:12 < 00:01:08, 32.5 msg/s]

+--------------------+---------------------+
| Metric             | Value               |
+--------------------+---------------------+
| Forwarded Messages | 7,800               |
| Batches Dispatched | 78                  |
| Average Delay      | 1.42s               |
| Rate Limit Pauses  | 0 (No FloodWait)    |
| Status             | Running             |
+--------------------+---------------------+
```

---

## Technology Stack

| Domain | Technology | Rationale |
| :--- | :--- | :--- |
| **Language** | Python 3.10+ | Robust async ecosystem and native socket libraries |
| **Protocol** | Telethon (MTProto) | Direct Telegram data center RPCs, bypassing Bot API limits |
| **Concurrency** | asyncio + PyQt6 QThread | High-concurrency I/O with responsive native desktop UI |
| **CLI Formatting** | Rich | Clean progress bars, tables, and terminal formatting |
| **Data Export** | JSON, CSV, OpenPyXL | Multi-format structured reporting for data analysis |
| **Testing** | Standard library unittest | Zero external dependencies for automated test suites |

---

## Quick Start Guide

### 1. Prerequisites
* Python 3.10, 3.11, or 3.12
* Telegram API ID and API Hash from [my.telegram.org](https://my.telegram.org)

### 2. Installation
```bash
# Clone the repository
git clone https://github.com/shivanshjain456/telegram-migration-pipeline.git
cd telegram-migration-pipeline

# Create and activate virtual environment
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configuration
Copy `.env.example` to `.env` and fill in your application credentials:
```bash
cp .env.example .env
```

```env
# Application Credentials (from https://my.telegram.org)
API_ID="12345678"
API_HASH="0123456789abcdef0123456789abcdef"

# Storage Directories
SESSION_DIR="sessions"
RESUME_DIR="resume"
LOG_DIR="logs"
```

### 4. Running the Desktop GUI
```bash
python main.py
```

### 5. Running the Headless CLI
```bash
# Forward messages from source to target with resume enabled
python cli.py \
  --source "@source_channel" \
  --target "@target_channel" \
  --batch-size 100 \
  --min-delay 1.0 \
  --max-delay 2.5 \
  --resume
```

### 6. Extracting Links and Channel Mentions
```bash
# Extract links and export to CSV
python link_extractor.py \
  --source "@tech_news_archive" \
  --output "exports/links.csv" \
  --format csv
```

---

## Automated Testing

The repository contains 25 automated tests covering checkpoint hashing, crash recovery, delay range normalization, link parsing, and mocked MTProto client batching.

**Tests run 100% offline and require zero Telegram credentials**:
```bash
# Run all unit tests
python -m unittest discover tests -v
```

Expected output:
```text
test_corrupt_resume_file_fallback (tests.test_checkpoint.TestCheckpoint) ... ok
test_save_and_load_resume_state (tests.test_checkpoint.TestCheckpoint) ... ok
test_mock_bulk_forward_batch_mode (tests.test_mock_pipeline.TestMockPipeline) ... ok
test_normalize_delay_range_inverted (tests.test_rate_limit_backoff.TestRateLimitBackoff) ... ok
...
Ran 25 tests in 0.04s

OK
```

---

## Technical Documentation Index

Deep technical architecture guides, design tradeoffs, and operational manuals are available in `docs/`:

* [Architecture Guide](docs/architecture.md): Concurrency model, QThread-to-asyncio bridge, and data flow.
* [Design Decisions (ADRs)](docs/design-decisions.md): Tradeoffs between MTProto and Bot API, atomic JSON state vs SQLite, and jitter algorithms.
* [Testing Strategy](docs/testing.md): Unit test matrix, RPC mocking, and corruption recovery verification.
* [Security & Privacy](docs/security.md): MTProto session file handling, path traversal protection, and data governance.
* [Operational Runbook](docs/operations.md): CLI flags, rotating log management, failure mode mitigations, and headless deployment.
* [Known Limitations](docs/limitations.md): Telegram platform constraints, server FloodWait rules, and file size limits.
* [Recruiter Review](docs/recruiter-review.md): One-page architectural breakdown for technical interviews.

---

## What the Owner Built and Owned

* Designed the dual-interface architecture separating desktop UI (PyQt6) from headless CLI (Rich).
* Implemented the `QThread`-to-`asyncio` event-loop bridge to prevent desktop UI freezing during high-throughput I/O.
* Developed the deterministic SHA-256 route hashing and crash-safe JSON checkpoint engine with corrupt state recovery.
* Built the content analysis and link extraction engine with graceful degradation for spreadsheet exports.
* Created the test suite with comprehensive Telethon MTProto client mocks, achieving 100% offline testability.

---

## Security, Privacy, and Responsible Use

* **No Credentials Committed**: `.gitignore` strictly blocks all `.env` files, `.session` SQLite databases, and transient logs.
* **Ephemeral In-Memory Buffering**: Message contents and media are streamed directly between endpoints and are not stored in any local database.
* **Terms of Service Compliance**: This tool is designed strictly for personal archiving and authorized channel administration. Operators must respect Telegram rate limits and the copyright permissions of channel content.

---

## Project Status and Roadmap

* **Status**: Complete and stable. Used for high-volume channel migrations and historical archiving.
* **Planned Improvements**:
  * [ ] SQLite backend option with WAL mode for migrations tracking over 1,000 concurrent channels.
  * [ ] Docker container image for automated cloud deployments.
  * [ ] Webhook notifications (Slack/Discord) on migration completion or unrecoverable FloodWait alerts.
