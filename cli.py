#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Bulk Forwarder — CLI
- Multiple sources via comma or file queue.
- True batching for non-anonymized forwards.
- Per-source resume and live stats.
"""
import argparse
import asyncio
import logging
import os
import signal
import sys
from logging.handlers import RotatingFileHandler
from typing import List

from dotenv import load_dotenv
from rich import box
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    BarColumn,
    MofNCompleteColumn,
)
from rich.prompt import Prompt, Confirm
from rich.table import Table
from telethon import TelegramClient

from utils import (
    bulk_forward,
    RESUME_DIR,
    build_route_key,
    Stats,
    ForwardConfig,
    parse_source_list,
    merge_sources,
    normalize_delay_range,
    normalize_types,
    resolve_resume_state,
    resolve_storage_dir,
    sanitize_session_name,
)

load_dotenv()
console = Console()

LOG_DIR = os.getenv("LOG_DIR", ".")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "forwarder.log")

logger = logging.getLogger("tg-bulk-cli")
logger.setLevel(logging.INFO)
fh = RotatingFileHandler(LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8")
fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(fh)
ch = logging.StreamHandler(sys.stdout)
ch.setFormatter(logging.Formatter("%(message)s"))
ch.setLevel(logging.INFO)
logger.addHandler(ch)

# Reduce Telethon noise
logging.getLogger("telethon").setLevel(logging.WARNING)
logging.getLogger("telethon.network").setLevel(logging.ERROR)

SESSION_DIR = resolve_storage_dir("SESSION_DIR", "sessions", logger)

ACCOUNT_PRESETS = [
    {"name": "Account 1 (Primary)", "env_api_id": "ACCOUNT_1_API_ID", "env_api_hash": "ACCOUNT_1_API_HASH", "session": "account_1"},
    {"name": "Account 2 (Secondary)", "env_api_id": "ACCOUNT_2_API_ID", "env_api_hash": "ACCOUNT_2_API_HASH", "session": "account_2"},
    {"name": "Default Account", "env_api_id": "API_ID", "env_api_hash": "API_HASH", "session": "default_account"},
]


def resolve_preset_credentials_cli(name: str):
    for preset in ACCOUNT_PRESETS:
        if preset["name"] == name:
            base_session = preset.get("session") or preset["name"]
            api_id = os.getenv(preset.get("env_api_id", ""), "") or os.getenv("API_ID", "")
            api_hash = os.getenv(preset.get("env_api_hash", ""), "") or os.getenv("API_HASH", "")
            session = sanitize_session_name(base_session)
            return api_id, api_hash, session
    return "", "", None


def build_parser():
    p = argparse.ArgumentParser(description="Telegram Bulk Forwarder")
    p.add_argument("--source", "-s", help="Source channel(s). Comma- or newline-separated")
    p.add_argument("--source-queue", "-Q", help="Path to a file with one source per line")
    p.add_argument("--target", "-t", required=True, help="Target channel")
    p.add_argument("--batch-size", type=int, default=100)
    p.add_argument("--min-delay", type=float, default=0.8)
    p.add_argument("--max-delay", type=float, default=2.5)
    p.add_argument("--resume", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--anonymize", action="store_true")
    p.add_argument("--order", choices=["oldest", "newest"], default="oldest")
    p.add_argument("--types", nargs="*", help="Media types to include (default: all). Choices: text photo image document video audio webpage")
    p.add_argument("--max-messages", type=int)
    p.add_argument("--api-id")
    # api-hash flag intentionally not supported to avoid leaking secrets via process list/history
    p.add_argument("--api-hash", dest="deprecated_api_hash", help=argparse.SUPPRESS)
    p.add_argument("--preset", choices=[p["name"] for p in ACCOUNT_PRESETS], help="Load API_ID/API_HASH from preset env vars")
    p.add_argument("--session-name", default=os.getenv("SESSION_NAME", "forward-session"))
    p.add_argument("--yes", action="store_true")
    p.add_argument("--dedup-ttl", type=int, default=3600, help="Deduplication TTL in seconds")
    p.add_argument("--plan", action="store_true", help="Print planned routes and exit")
    return p


def parse_sources(args) -> List[str]:
    chunks: List[List[str]] = []
    chunks.append(parse_source_list(args.source or ""))
    if args.source_queue:
        if not os.path.exists(args.source_queue):
            raise FileNotFoundError(f"Source queue file not found: {args.source_queue}")
        queue_entries: List[str] = []
        with open(args.source_queue, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                queue_entries.append(s)
        if queue_entries:
            chunks.append(parse_source_list("\n".join(queue_entries)))
    sources = merge_sources(*chunks)
    if not sources:
        raise ValueError("No sources provided.")
    return sources


async def async_main(args):
    if getattr(args, "deprecated_api_hash", None):
        console.print("[red]The --api-hash flag is disabled to avoid leaking secrets in process lists. Set API_HASH in the environment or enter it interactively.[/red]")
        sys.exit(2)

    api_id = (os.getenv("API_ID") or args.api_id)
    api_hash = os.getenv("API_HASH")
    session_name = args.session_name
    if args.preset:
        pid, phash, psess = resolve_preset_credentials_cli(args.preset)
        api_id = pid or api_id
        api_hash = phash or api_hash
        if psess:
            session_name = psess
    if args.api_id:
        console.print("[yellow]Warning: --api-id is visible in shell history/process list. Prefer API_ID env vars instead.[/yellow]")
    if not api_id or not api_hash:
        if Confirm.ask("API_ID/API_HASH missing. Enter now?", default=True):
            api_id = Prompt.ask("API_ID")
            api_hash = Prompt.ask("API_HASH", password=True)
        else:
            console.print("[red]Missing credentials.[/red]")
            sys.exit(1)

    try:
        sources = parse_sources(args)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(2)

    if args.plan:
        console.print("[bold]Plan[/bold]:")
        for src in sources:
            console.print(f" - {src} \u2192 {args.target}")
        console.print("Use --dry-run for simulated execution.")
        return

    # client
    session_name = sanitize_session_name(session_name)
    session_path = os.path.join(SESSION_DIR, session_name)
    if "onedrive" in session_path.lower():
        console.print("[yellow]Warning: session directory is under OneDrive; set SESSION_DIR to a non-synced path to avoid leaking session tokens.[/yellow]")
    client = TelegramClient(session_path, int(api_id), api_hash, connection_retries=None, retry_delay=2, request_retries=5)
    await client.start()

    stop_event = asyncio.Event()

    def _shutdown():
        if not stop_event.is_set():
            logger.info("Stop requested…")
            stop_event.set()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            pass

    min_delay, max_delay = normalize_delay_range(args.min_delay, args.max_delay)
    if args.batch_size <= 0:
        console.print("[red]Batch size must be > 0.[/red]")
        sys.exit(2)

    allowed_types = normalize_types(args.types)
    if args.types and not allowed_types:
        console.print("[red]No valid media types provided. Choose from: text photo image document video audio webpage[/red]")
        sys.exit(2)

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
    )
    task_id = progress.add_task("Starting…", total=None)

    totals = {"forwarded": 0, "skipped": 0, "errors": 0}

    with Live(Panel(progress, title="[bold]Forwarding[/bold]"), refresh_per_second=12):
        for idx, src in enumerate(sources, start=1):
            route_key = build_route_key(src, args.target, session_name=session_name)
            resume_state, resume_path, stale = resolve_resume_state(route_key, args.resume, RESUME_DIR)
            if stale:
                logger.warning("Resume file is older than %s days for route %s; consider resetting.", os.getenv("RESUME_STALE_DAYS", "30"), route_key)

            cfg = ForwardConfig(
                batch_size=args.batch_size,
                min_delay=min_delay,
                max_delay=max_delay,
                anonymize=args.anonymize,
                dry_run=args.dry_run,
                start_id=None, end_id=None, since=None, until=None,
                order=args.order,
                max_messages=args.max_messages,
                types=allowed_types,
                stop_event=stop_event,
                pause_event=None,
                resume_state=resume_state,
                resume_path=resume_path,
                adaptive=True,
                dedup_enabled=True,
                dedup_ttl=float(args.dedup_ttl),
            )

            stats = Stats()
            progress.update(task_id, description=f"[{idx}/{len(sources)}] {src} → {args.target}")
            fwd_task = asyncio.create_task(bulk_forward(client, src, args.target, cfg, stats, logger))
            while not fwd_task.done():
                task = progress.get_task(task_id)
                if stats.total and task.total is None:
                    progress.update(task_id, total=stats.total)
                if stats.total:
                    progress.update(task_id, completed=stats.processed)
                await asyncio.sleep(0.25)
            try:
                final = await fwd_task
            except asyncio.CancelledError:
                logger.info("Forwarding cancelled for route %s → %s", src, args.target)
                final = stats
            totals["forwarded"] += final.forwarded
            totals["skipped"] += final.skipped
            totals["errors"] += final.errors
            progress.update(task_id, total=None, completed=0)
            if stop_event.is_set():
                break

    await client.disconnect()

    # summary
    table = Table(title="[bold]Summary[/bold]", box=box.ROUNDED)
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right", style="green")
    for k in ("forwarded", "skipped", "errors"):
        table.add_row(k.capitalize(), str(totals[k]))
    console.print(table)


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    if not (args.source or args.source_queue):
        args.source = Prompt.ask("Source(s) comma-separated")
    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        console.print("\n[red]Stopped by user.[/red]")
        sys.exit(130)
