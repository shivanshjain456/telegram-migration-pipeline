# Security Policy

## Reporting Security Issues

We take the security and privacy of Telegram credentials and personal data seriously. If you discover a security vulnerability or credential leakage vector in this repository, please report it privately rather than opening a public GitHub issue.

Please send details to:
`security@example.com` (or submit a GitHub Private Vulnerability Report)

Include:
1. Description of the vulnerability or leakage vector.
2. Steps to reproduce or proof-of-concept.
3. Potential impact on user credentials or session authorization.

You will receive an acknowledgment within 48 hours and regular updates until resolution.

---

## Threat Model and Credential Security

### 1. Telegram MTProto Session Files
* **Sensitivity**: Telethon `.session` files contain unencrypted binary SQLite databases storing authentication keys (`auth_key`), data center IDs, and active session tokens. Anyone possessing a valid session file has full authorized access to the associated Telegram account.
* **Storage Isolation**: Session files are written exclusively to `sessions/` or the directory pointed to by `SESSION_DIR`.
* **Git Protection**: `.gitignore` strictly excludes `*.session`, `*.session-journal`, and all files in `sessions/` except `.gitkeep`.
* **Sanitization**: All session names passed via CLI or GUI are sanitized via `sanitize_session_name()` to prevent path traversal (`../`) attacks.

### 2. API Credentials (`API_ID`, `API_HASH`)
* Telegram API credentials identify the application to Telegram servers.
* Credentials must be loaded strictly from local `.env` files or environment variables.
* Never commit `.env` files to source control. A safe template is provided in `.env.example`.
* CLI arguments or environment variables are never printed in plain text to log outputs or console stdout.

### 3. Log Sanitization
* File logging uses `RotatingFileHandler` with a default limit of 10 MB per file and 3 historical backups.
* Message content, phone numbers, two-factor authentication passwords, and authentication codes are never written to disk logs.
* Exception handlers catch `FloodWaitError` and rate-limit violations without exposing internal authorization tokens.

### 4. Crash-Safe State Files
* Resume state is persisted under `resume/` as lightweight JSON maps indexed by SHA-256 hashes of `source -> destination` routes.
* State files record solely progress pointers (`last_id`, `forwarded_count`, timestamp), containing zero message payloads or user identities.

---

## Responsible Use Guidelines

This software utilizes user-level MTProto APIs (Telethon). Users are responsible for:
- Operating only on channels, groups, and chats where they have authorized administrative or participant rights.
- Adhering to Telegram FloodWait delays to prevent automated account restrictions.
- Ensuring compliance with relevant privacy regulations (e.g., GDPR, CCPA) when migrating group or channel records.
