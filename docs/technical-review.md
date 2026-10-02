# 3-Minute Engineering & Architecture Review

## Quick Overview

**Telegram Migration Pipeline** is a resilient Python/asyncio data engineering pipeline designed to archive, extract, and migrate message history and media across Telegram channels and groups. It provides both an interactive desktop interface (PyQt6) and a headless automation runner (Rich CLI).

* **Primary Problem**: Migrating large Telegram channels involves rate-limiting hurdles (FloodWait errors), network dropouts (`WinError 64`), UI freezing, and lost progress on power or network failure.
* **Core Solution**: An asynchronous engine featuring native MTProto RPC batching (100 msgs/call), SHA-256 hashed crash checkpoints, adaptive rate-limit backoff, and a dedicated thread-to-asyncio event-loop bridge for the GUI.

---

## Technical Competencies Demonstrated

### 1. Asynchronous Systems & Concurrency Architecture
* **GUI-to-Asyncio Bridge**: Solved the classic PyQt/asyncio impedance mismatch by hosting a private `asyncio` event loop inside an isolated `QThread`, dispatching thread-safe Qt Signals to update the UI without freezing.
* **Native RPC Batching**: Implemented high-throughput batching that forwards up to 100 messages per network round-trip, minimizing RPC overhead by 99% compared to sequential message forwarding.

### 2. Resilience & Fault Tolerance
* **Deterministic Route Checkpointing**: Checkpoint state files are keyed by SHA-256 hashes of `source->target` pairs, preventing path traversal vulnerabilities and database lock contention.
* **Crash Recovery**: Checkpoint writes are atomic; corrupted JSON state files trigger automatic fallbacks rather than pipeline crashes.
* **Adaptive Rate Limiting**: Intercepts Telegram `FloodWaitError` exceptions, extracts required cooldown seconds, and pauses execution cooperatively without dropping pending batches.

### 3. Supply Chain & Testing Discipline
* **Zero Credential Dependency**: 25 unit tests run completely offline with mocked Telethon clients and synthetic fixtures. Clean clones pass tests in under 100 milliseconds without requiring real phone numbers or API keys.
* **Dual Runtime Interfaces**: Complete separation between the presentation tier (PyQt6 desktop vs. Rich headless CLI) and the underlying transport engine (`utils.py`).

---

## Technology Stack

| Layer | Technologies |
| :--- | :--- |
| **Language** | Python 3.10, 3.11, 3.12 |
| **Networking & Protocol** | Telethon (MTProto binary protocol), asyncio |
| **User Interfaces** | PyQt6 (Desktop GUI), Rich (Headless Terminal CLI) |
| **Storage & Checkpointing** | JSON atomic serialization, SHA-256 route hashing |
| **Testing & CI** | Standard library `unittest`, GitHub Actions CI matrix |

---

## Representative Technical Interview Questions

### Q: Why did you choose Telethon's MTProto user client instead of the Telegram Bot API?
> "Telegram's Bot API requires bots to be administrators in all source channels, which prevents archiving historical or restricted channels where the user is merely a subscriber. Furthermore, the Bot API enforces strict 50 MB upload limits and lacks native 100-message batch forwarding RPCs. Telethon uses native MTProto, allowing full user-level channel access and server-side message copying with zero client bandwidth."

### Q: How do you prevent UI freezing when running asyncio inside a PyQt6 application?
> "PyQt6 runs a C++ event loop on the main thread. If you run `asyncio.run()` there, it blocks the main thread completely. I implemented `ForwardWorker(QThread)` which instantiates a fresh `asyncio.new_event_loop()` on an isolated OS thread. Telethon runs its coroutines within this worker loop, and progress notifications are marshaled back to the GUI via Qt Signals (`sig_progress`, `sig_log`), ensuring thread safety and 60 FPS UI responsiveness."

### Q: How does the pipeline ensure migrations can resume after unexpected power loss?
> "Every forward batch writes its latest message ID (`last_id`) to a JSON checkpoint file. The filename is a 16-character SHA-256 hash of the normalized source and target channel identifiers. On relaunch, passing `--resume` causes the pipeline to read this state file and start fetching messages starting at `last_id + 1`. If the file was corrupted mid-write during a crash, the loader catches the parsing exception, logs an alert, and safely falls back without halting the application."
