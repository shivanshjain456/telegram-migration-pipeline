# Contributing to Telegram Migration Pipeline

Thank you for your interest in contributing! This project provides a robust, resilient tool for migrating, archiving, and extracting data across Telegram channels and groups.

---

## Code of Conduct

All contributors are expected to uphold respectful, inclusive, and professional communication. Do not submit features designed to facilitate spam, harassment, mass unsolicited messaging, or Terms of Service violations.

---

## Architecture Overview

Before contributing, familiarize yourself with the modular architecture:

* **`utils.py`**: The core execution engine. Houses `bulk_forward()`, asynchronous batching, `FloodWaitError` retry loops, route key generation, and crash-safe JSON checkpoint persistence.
* **`link_extractor.py`**: Content parsing and analysis engine. Extracts message URLs, channel handles, and entity links, exporting to JSON, CSV, and Excel without network overhead.
* **`cli.py`**: Headless CLI runner built with `rich` panels, live progress bars, argument parsing, and UNIX signal handling (`SIGINT`/`SIGTERM`).
* **`main.py`**: Desktop GUI client built with PyQt6. Bridges PyQt signals and Python's `asyncio` event loop through an isolated `QThread` worker (`ForwardWorker`).
* **`tests/`**: Unit test suite using standard library `unittest` with mock Telethon clients and fixture data.

---

## Development Setup

### 1. Prerequisites
* Python 3.10, 3.11, or 3.12
* Git

### 2. Local Setup
```bash
# Clone the repository
git clone https://github.com/your-username/telegram-migration-pipeline.git
cd telegram-migration-pipeline

# Create and activate virtual environment
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Environment Configuration
Copy `.env.example` to `.env` if you plan to run local tests against a live test account:
```bash
cp .env.example .env
```
*Never commit `.env` or any `.session` files to Git.*

---

## Running Automated Tests

Tests are fully isolated and do not make external network calls or require live Telegram credentials:

```bash
# Run all unit tests
python -m unittest discover tests

# Run specific test module
python -m unittest tests/test_checkpoint.py
python -m unittest tests/test_rate_limit_backoff.py
python -m unittest tests/test_link_extractor.py
python -m unittest tests/test_mock_pipeline.py
```

All contributions must pass all existing tests and include new test cases for added logic.

---

## Pull Request Guidelines

1. **Branch Naming**: Use descriptive branch names:
   * `feat/batch-throttling-enhancement`
   * `fix/resume-json-lock`
   * `docs/operations-update`
2. **Commit Messages**: Follow Conventional Commits format:
   * `feat(engine): add exponential backoff on consecutive flood waits`
   * `fix(checkpoint): prevent partial write corruption via atomic rename`
   * `test(extractor): add entity link parsing test cases`
3. **No External Secret Dependencies**: PRs must not require personal credentials to pass automated CI.
4. **Clean Code**: Use type annotations where appropriate, handle exceptions gracefully, and document design decisions in relevant module docstrings.
