# Known Limitations and Platform Boundaries

## Overview

Telegram Migration Pipeline is engineered for reliable, single-node data transfers. However, operating within Telegram's proprietary MTProto infrastructure introduces explicit platform and architectural limitations.

---

## 1. Telegram Platform & Protocol Constraints

### Strict Server-Side Flood Limits
* Telegram's anti-spam algorithms enforce server-side cooldown periods (`FloodWaitError`) ranging from seconds to 24+ hours.
* **No Bypass Exists**: These limits are evaluated on Telegram data centers. No client-side tool or parameter can circumvent a server-issued FloodWait. The pipeline gracefully pauses and waits, but cannot expedite the server-mandated cooldown.

### Batch Size Upper Limit
* Telegram's MTProto method `messages.forwardMessages` accepts a maximum of **100 message IDs** per RPC invocation.
* Setting `--batch-size` higher than 100 is automatically clamped to 100 to prevent protocol rejection.

### "Restrict Saving Content" Protection
* Channel owners can enable "Restrict Saving Content", which instructs Telegram servers to block forwarding and media downloading.
* When this protection is active, Telegram rejects native forwarding calls. Anonymized re-send requires screenshotting or specialized rendering not supported within standard MTProto API calls.

### File Size Limits
* Standard Telegram user accounts can upload files up to **2 GB**.
* Telegram Premium accounts can upload files up to **4 GB**.
* Messages containing files exceeding these bounds cannot be re-uploaded in anonymized mode.

---

## 2. Bandwidth & Media Transfer Constraints

### Anonymized Mode I/O Overhead
* In native forward mode, message references are copied on Telegram's servers with zero client data transfer.
* In anonymized mode, every media item (video, lossless image, large ZIP archive) must be downloaded to the local host and re-uploaded. This process is bounded entirely by the operator's upstream internet bandwidth and local disk I/O.

### Memory Footprint During Media Buffering
* While text messages consume minimal memory, streaming multiple gigabytes of media concurrently can strain low-memory VPS environments (< 1 GB RAM). Media streaming is executed sequentially to mitigate Out-Of-Memory (OOM) conditions.

---

## 3. Architecture & Operational Boundaries

### Single-Node Execution Model
* The checkpoint engine writes to the local filesystem (`resume/<route_hash>.json`).
* Running multiple concurrent instances across different machines against the same source-destination pair requires a shared distributed filesystem (e.g., NFS, Amazon EFS) or distributed coordination (e.g., Redis locks), which is not part of the core standalone pipeline.

### Session Revocation
* A Telegram session remains valid until the user terminates it via Telegram Settings -> Devices -> Active Sessions, or if Telegram flags the account for suspicious activity. If revoked, the client throws `SessionRevokedError` and requires manual re-authentication.
