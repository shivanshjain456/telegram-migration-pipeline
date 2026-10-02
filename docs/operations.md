# Operational Runbook and Deployment Guide

## Overview

Telegram Migration Pipeline can be operated as an interactive desktop tool (GUI) or as an automated headless background worker (CLI). This guide covers deployment, configuration, logging, failure modes, and recovery procedures.

---

## Deployment Architectures

### 1. Interactive Desktop (GUI)
Best suited for ad-hoc migrations, manual reviews, and desktop operators:
```bash
python main.py
```
* **Window Management**: Launches PyQt6 application.
* **Telemetry**: Real-time progress bars, per-source status indicators, and live log view.

### 2. Headless Server / Daemon (CLI)
Best suited for large historical migrations, cron jobs, and cloud VMs:
```bash
python cli.py \
  --source "@source_channel" \
  --target "@target_channel" \
  --batch-size 100 \
  --min-delay 1.0 \
  --max-delay 2.5 \
  --resume
```

#### Queue-Based Batch Processing
Process an ordered list of channels from a file:
```bash
python cli.py \
  --source-queue "channels.txt" \
  --target "@archive_vault" \
  --resume
```

---

## Operational Commands Reference

| Option | Flag | Default | Description |
| :--- | :--- | :--- | :--- |
| `--source` | `-s` | `None` | Comma- or newline-separated channel handles/IDs |
| `--source-queue` | `-Q` | `None` | Path to text file with one source channel per line |
| `--target` | `-t` | Required | Target channel handle, link, or integer ID |
| `--batch-size` | `-b` | `100` | Messages per batch RPC (native forward mode only) |
| `--min-delay` | | `0.8` | Minimum randomized delay between operations (seconds) |
| `--max-delay` | | `2.5` | Maximum randomized delay between operations (seconds) |
| `--resume` | `-r` | `False` | Resume migration from last recorded checkpoint |
| `--dry-run` | | `False` | Scan and log message count without transferring |
| `--anonymize` | | `False` | Re-send content without original author header |
| `--types` | | `all` | Filter types: `text,photo,video,document,voice` |

---

## Observability & Logging

### File-Based Rotating Logs
The pipeline writes structured diagnostic logs using Python's `logging.handlers.RotatingFileHandler`:
* **Path**: `logs/forwarder.log` (configurable via `LOG_DIR`)
* **Maximum Size**: 10 MB per file
* **Retention**: 3 historical backups (`forwarder.log.1`, `forwarder.log.2`, `forwarder.log.3`)
* **Encoding**: UTF-8

### Real-Time Monitoring
Inspect ongoing migrations in production:
```bash
# Linux / macOS
tail -f logs/forwarder.log

# Windows PowerShell
Get-Content logs/forwarder.log -Wait
```

---

## Failure Modes & Recovery Procedures

### 1. `FloodWaitError: A wait of X seconds is required`
* **Cause**: Telegram's anti-flood heuristic triggered due to rapid requests or high volume.
* **Automatic Action**: The pipeline intercepts the error, pauses the worker for the mandated duration (`seconds + 1`), and resumes automatically.
* **Mitigation**: Increase `--min-delay` to `2.0` or higher and decrease `--batch-size`.

### 2. `WinError 64: The specified network name is no longer available`
* **Cause**: Windows TCP stack dropped the connection during long idle waits or network drops.
* **Automatic Action**: Telethon reconnects automatically. The pipeline catches disconnection exceptions, re-authenticates the session, and re-reads the last checkpoint ID.

### 3. `ChannelPrivateError` or `ChannelInvalidError`
* **Cause**: The authenticated account is not a member of the source channel or the channel has been made private.
* **Resolution**: Ensure the account joins the channel or verify the invite link.

### 4. `ChatWriteForbiddenError`
* **Cause**: The authenticated account lacks post permissions in the target channel.
* **Resolution**: Promote the account to Administrator with "Post Messages" privileges in the target channel.

### 5. Checkpoint File Recovery
If a migration is abruptly halted by power loss:
* Re-run the CLI command with the `--resume` flag.
* The engine loads `resume/<route_hash>.json`, reads `last_id`, and resumes with zero message re-delivery.
