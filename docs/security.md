# Security and Privacy Architecture

## Security Boundaries & Credential Model

Operating a user-scoped MTProto client involves high-privilege credentials. This document defines the security architecture and privacy controls implemented in the repository.

---

## 1. Authentication Artifacts & Session Isolation

### MTProto Session Files (`.session`)
When Telethon authenticates with Telegram data centers, it stores the resulting cryptographic authorization keys (`auth_key`), data center connection params, and user account metadata in a local SQLite file (e.g., `account_1.session`).

* **Threat**: A compromised `.session` file permits complete, persistent access to the owner's Telegram account without requiring two-factor SMS or authenticator codes.
* **Defense**:
  * **Strict Exclusion**: All `.session` and `.session-journal` files are blocked in `.gitignore` and banned from version control.
  * **Directory Isolation**: All sessions are confined to `sessions/` or the path specified via `SESSION_DIR`.
  * **Path Traversal Protection**: Session file names are sanitized through `sanitize_session_name()`, which strips illegal characters and path escalation components (`..`, `/`, `\`).

### Application API Credentials (`API_ID`, `API_HASH`)
Telegram issues `API_ID` and `API_HASH` pairs to registered developers. While required for MTProto connection negotiation:
* Credentials are read exclusively from environment variables or local `.env` files.
* Templates use non-functional dummy tokens (`12345678`, `abcdef0123456789abcdef0123456789`).
* The application suppresses credential echoes in logs, stdout, and tracebacks.

---

## 2. Telegram Rate Limiting & Account Protection

Telegram enforces active heuristic rate-limiting algorithms to detect automated scraping and mass forwarding.

### FloodWait Management
* When rate limits are reached, Telegram responds with a `FloodWaitError` containing mandatory cooldown seconds (often between 10 seconds and several hours).
* The pipeline intercepts `FloodWaitError`, logs the event at `WARNING` severity without dumping raw payloads, and schedules a cooperative sleep (`asyncio.sleep(seconds + 1)`).
* The engine never loops aggressively or retries immediately after a FloodWait error, preserving the user account's standing.

### Human-Simulated Jitter
* Sending identical RPC requests with zero latency triggers Telegram's heuristic anti-bot filters.
* All forwarding operations inject randomized delay jitter (`random.uniform(min_delay, max_delay)`).
* Users are discouraged from setting `min_delay < 0.5s` when transferring thousands of items.

---

## 3. Privacy and Data Governance

### Message Payload Ephemerality
* **No Database Caching**: The pipeline does not store message text, member lists, or attachments in a persistent local database. Messages are streamed directly from source to destination.
* **In-Memory Buffering**: For anonymized re-sends, media is buffered temporarily in memory or ephemeral OS temp files and discarded immediately upon transfer.

### Link Extractor Privacy
* `link_extractor.py` scans text strictly for public URLs, channel handles, and domain classifications.
* It does not parse or export private chat participants, phone numbers, or user bio information.

---

## 4. Responsible Use and Legal Boundaries

This tool must only be used in full compliance with applicable laws and Telegram's Terms of Service:
1. **Administrative Migrations**: Migrating channels, groups, and content that you own or have explicit administrative authorization to manage.
2. **Personal Backups**: Creating private archives of chats where you are an authorized participant.
3. **Prohibition of Spam & Scraping**: This tool must never be used to harvest content from channels without consent, mass-spam groups, or evade copyright restrictions.
