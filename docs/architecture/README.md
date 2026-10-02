# Telegram Migration Pipeline Architecture Diagram System

This directory houses the interactive, editable diagrams.net (draw.io) architecture diagrams for the Telegram Migration Pipeline.

---

## Diagram Index

| Diagram | Source File | Description | Interactive Lightbox |
| :--- | :--- | :--- | :--- |
| **Concurrency & Reliability Architecture** | [`architecture.drawio.svg`](architecture.drawio.svg) | C4 Component model showing dual presentation interfaces (PyQt6 GUI & Rich CLI), QThread-to-asyncio worker bridge, native MTProto batching, and atomic checkpoint persistence. | [Open in Lightbox](https://viewer.diagrams.net/?highlight=0000ff&edit=_blank&layers=1&nav=1&title=architecture.drawio.svg#Uhttps%3A%2F%2Fraw.githubusercontent.com%2Fshivanshjain456%2Ftelegram-migration-pipeline%2Fmain%2Fdocs%2Farchitecture%2Farchitecture.drawio.svg) |
| **Ingestion & Fault-Tolerant Checkpoint Flow** | [`core-flows.drawio.svg`](core-flows.drawio.svg) | 9-step pipeline sequence showing channel setup, SHA-256 route key calculation, 100-msg RPC batching, adaptive FloodWait backoff, atomic checkpoint saves, and link extraction. | [Open in Lightbox](https://viewer.diagrams.net/?highlight=0000ff&edit=_blank&layers=1&nav=1&title=core-flows.drawio.svg#Uhttps%3A%2F%2Fraw.githubusercontent.com%2Fshivanshjain456%2Ftelegram-migration-pipeline%2Fmain%2Fdocs%2Farchitecture%2Fcore-flows.drawio.svg) |

---

## How to View Interactively

Click the **Open in Lightbox** links above (or the embedded images in the root `README.md`). 
The diagram opens in fullscreen vector mode within diagrams.net, allowing:
* Zooming into concurrency bridge signals, MTProto RPC transport modalities, and error recovery states.
* Panning across thread boundaries and persistent storage tiers.
* Full-screen presentation mode for technical and distributed systems interviews.

---

## How to Edit Diagrams

These diagrams use the editable SVG format (`.drawio.svg`), combining visible vector shapes with the complete diagrams.net XML model.

### Option A: Edit Directly on GitHub via diagrams.net
* [Edit System Architecture](https://app.diagrams.net/#Hshivanshjain456%2Ftelegram-migration-pipeline%2Fmain%2Fdocs%2Farchitecture%2Farchitecture.drawio.svg)
* [Edit Ingestion & Checkpoint Flow](https://app.diagrams.net/#Hshivanshjain456%2Ftelegram-migration-pipeline%2Fmain%2Fdocs%2Farchitecture%2Fcore-flows.drawio.svg)

### Option B: Edit Locally via VS Code / Desktop App
1. Install the official **Draw.io Integration** extension in VS Code.
2. Open either `docs/architecture/architecture.drawio.svg` or `docs/architecture/core-flows.drawio.svg` directly.
3. Edit shapes, connector labels, or operational components.
4. Save the file. VS Code will automatically synchronize the embedded XML and visible vector SVG layers.
5. Commit and push:
   ```bash
   git add docs/architecture/
   git commit -m "docs(architecture): update concurrency worker boundaries"
   git push origin main
   ```

---

## Synchronization Rules & Guidelines

When modifying the Telegram Migration Pipeline:
1. **Thread Separation**: The PyQt6 main thread must never run `asyncio.run()` or execute network I/O. All async Telethon operations must remain isolated in `ForwardWorker(QThread)`.
2. **Deterministic Route Keys**: Route checkpoint filenames must always be computed via `sha256(f"{source}->{target}".encode()).hexdigest()[:16]` to guarantee deterministic file paths.
3. **Adaptive FloodWait Handling**: The pipeline must catch `FloodWaitError` and sleep cooperatively (`err.seconds + 1`). Never introduce aggressive spinlocks or busy-loops.
4. **Security Notice**: Never embed real `.session` binary dumps, real phone numbers, real API hashes, or private group invite links in diagram metadata or documentation.
