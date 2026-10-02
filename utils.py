# -*- coding: utf-8 -*-
"""
TG Bulk Copy Utilities — Reliable Sequential + Resilient Network Edition
- Chronological iteration with resume.
- Long-caption fallback for anonymized sends.
- Resilient to transient network disconnects (WinError 64, 0 bytes read, etc).
- True batching for forwards (anonymize=False).
- Single source-list parser reused by CLI and GUI.
"""
import asyncio
import hashlib
import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from telethon import functions, errors
from telethon.errors import FloodWaitError, MessageEmptyError
from telethon.tl.types import (
    Message,
    MessageMediaDocument,
    MessageMediaPhoto,
    MessageMediaWebPage,
    MessageMediaPoll,
    MessageMediaContact,
    MessageMediaGeo,
    MessageMediaVenue,
    MessageMediaInvoice,
)

logger = logging.getLogger(__name__)

# Telegram limits (safe defaults). Allow env override.
CAPTION_MAX = int(os.getenv("CAPTION_MAX", "1024"))
TEXT_MAX = int(os.getenv("TEXT_MAX", "4096"))

RESUME_STALE_DAYS = int(os.getenv("RESUME_STALE_DAYS", "30"))

ALLOWED_TYPES = {"text", "photo", "image", "document", "video", "audio", "webpage"}
TYPE_SYNONYMS = {
    "images": "image",
    "pics": "photo",
    "pictures": "photo",
    "docs": "document",
    "documents": "document",
    "vid": "video",
    "videos": "video",
    "music": "audio",
    "audios": "audio",
    "links": "webpage",
    "url": "webpage",
    "urls": "webpage",
}


def _default_data_root() -> str:
    if os.name == "nt":
        return os.path.join(os.getenv("LOCALAPPDATA") or os.path.expanduser("~"), "telegram_bulk_forwarder")
    return os.path.join(os.path.expanduser("~"), ".telegram_bulk_forwarder")


def resolve_storage_dir(env_var: str, default_leaf: str, log: Optional[logging.Logger] = None) -> str:
    """
    Resolve a storage directory with an env override, create it, and harden permissions.
    Warns if the path appears to be on a synced folder (OneDrive).
    """
    override = os.getenv(env_var)
    base_path = os.path.abspath(override) if override else os.path.abspath(os.path.join(_default_data_root(), default_leaf))
    os.makedirs(base_path, exist_ok=True)

    if os.name != "nt":
        try:
            os.chmod(base_path, 0o700)
        except Exception:
            if log:
                log.warning("Could not harden permissions for %s", base_path)
    elif "onedrive" in base_path.lower():
        if log:
            log.warning("Storage path %s appears to be under OneDrive; session/resume files may sync to cloud.", base_path)

    return base_path


def sanitize_session_name(name: str, fallback: str = "session") -> str:
    """Whitelist characters for session names and clamp length to avoid path traversal."""
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in (name or ""))
    cleaned = cleaned.strip("._") or fallback
    return cleaned[:80]


RESUME_DIR = resolve_storage_dir("RESUME_DIR", "resume", logger)


def parse_source_list(raw: str) -> List[str]:
    """Comma/newline/semicolon tolerant, de-duplicated, order-preserving."""
    if not raw:
        return []
    raw = raw.replace(";", ",")
    out: List[str] = []
    for line in raw.splitlines():
        for tok in line.split(","):
            s = tok.strip()
            if s:
                out.append(s)
    seen = set()
    uniq = []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


def _slug(s: str) -> str:
    return "".join(ch for ch in str(s) if ch.isalnum() or ch in ("_", "-", "@")).strip()


def build_route_key(src: str, dst: str, session_name: Optional[str] = None) -> str:
    """Stable resume key. Accepts optional session_name for multi-account runs."""
    left = _slug(str(src)).replace("@", "")
    right = _slug(str(dst)).replace("@", "")
    if session_name:
        sess = _slug(session_name)
        key = f"{sess}__{left}__TO__{right}"
    else:
        key = f"{left}__TO__{right}"
    if len(key) > 120:
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        key = f"{key[:40]}__{digest}"
    return key


def read_resume(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        logger.warning("Invalid resume file %s, ignoring: %s", path, exc)
        return {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.warning("Failed to read resume file %s: %s", path, exc)
        return {}


def write_resume(path: str, last_id: Optional[int]):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"last_id": last_id}, f)
    except Exception as exc:
        logger.warning("Failed to write resume file %s: %s", path, exc)


@dataclass
class Stats:
    forwarded: int = 0
    skipped: int = 0
    errors: int = 0
    last_id: Optional[int] = None
    processed: int = 0
    total: Optional[int] = None
    last_msg: Optional[str] = None
    throttle: float = 1.0


@dataclass
class ForwardConfig:
    batch_size: int
    min_delay: float
    max_delay: float
    anonymize: bool
    dry_run: bool
    start_id: Optional[int]
    end_id: Optional[int]
    since: Optional[str]
    until: Optional[str]
    order: str  # "oldest" or "newest"
    max_messages: Optional[int]
    types: Optional[Set[str]]
    stop_event: Any
    pause_event: Any = None
    resume_state: Dict[str, Any] = field(default_factory=dict)
    resume_path: Optional[str] = None
    adaptive: bool = True
    dedup_enabled: bool = True
    dedup_ttl: float = 3600.0
    resume_from_id: Optional[int] = None
    resume_from_date: Optional[str] = None  # ISO string


def merge_sources(*chunks: Iterable[str]) -> List[str]:
    """Merge iterable chunks preserving order and removing duplicates."""
    seen = set()
    merged: List[str] = []
    for chunk in chunks:
        for item in chunk:
            if item and item not in seen:
                seen.add(item)
                merged.append(item)
    return merged


def normalize_delay_range(min_delay: float, max_delay: float) -> Tuple[float, float]:
    """Ensure delay bounds are sane."""
    if min_delay < 0:
        min_delay = 0.0
    if max_delay < 0:
        max_delay = 0.0
    if max_delay < min_delay:
        logger.warning("max_delay < min_delay; adjusting max_delay=%s -> %s", max_delay, min_delay)
        max_delay = min_delay
    return min_delay, max_delay


def normalize_types(types: Optional[Iterable[str]]) -> Optional[Set[str]]:
    """Normalize user-provided media type filters; return None to mean 'all'."""
    if not types:
        return None
    out: Set[str] = set()
    for t in types:
        if not t:
            continue
        key = str(t).strip().lower()
        key = TYPE_SYNONYMS.get(key, key)
        if key in ALLOWED_TYPES:
            out.add(key)
    return out or None


def resolve_resume_state(
    route_key: str,
    enabled: bool,
    root_dir: Optional[str] = None,
) -> Tuple[Dict[str, Any], Optional[str], bool]:
    """Return resume state + path when resume is enabled; also flag stale files."""
    if not enabled:
        return {}, None, False
    base = root_dir or RESUME_DIR
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, f"{route_key}.json")
    stale = False
    if os.path.exists(path):
        mtime = os.path.getmtime(path)
        age_days = (time.time() - mtime) / 86400.0
        stale = age_days > RESUME_STALE_DAYS
    state = read_resume(path) if os.path.exists(path) else {}
    return state, path, stale


def is_transient_network_error(exc: Exception) -> bool:
    s = str(exc).lower()
    return any(
        key in s
        for key in [
            "server closed the connection",
            "0 bytes read",
            "winerror 64",
            "connection reset by peer",
            "timed out",
            "connection aborted",
            "can't write to closing transport",
        ]
    ) or isinstance(exc, (asyncio.TimeoutError, OSError, errors.rpcerrorlist.FloodWaitError))


async def ensure_connected(client, logger: Optional[logging.Logger] = None, attempts: int = 6) -> None:
    if client.is_connected():
        return
    delay = 1.5
    for i in range(attempts):
        try:
            if client.is_connected():
                await client.disconnect()
        except Exception:
            pass
        try:
            await client.connect()
            if client.is_connected():
                if logger:
                    logger.info("Reconnected to Telegram.")
                return
        except Exception as e:
            if logger:
                logger.warning(f"Reconnect attempt {i+1}/{attempts} failed: {e}")
        await asyncio.sleep(delay)
        delay = min(delay * 1.7, 15)
    raise RuntimeError("Failed to reconnect after transient network error")


def detect_type(msg: Message) -> str:
    if msg.media:
        if isinstance(msg.media, MessageMediaDocument):
            mime = getattr(getattr(msg.media, "document", None), "mime_type", "") or ""
            if "video" in mime:
                return "video"
            if "audio" in mime:
                return "audio"
            if "image" in mime:
                return "image"  # images sent as documents
            return "document"
        if isinstance(msg.media, MessageMediaPhoto):
            return "photo"
        if isinstance(msg.media, MessageMediaWebPage):
            return "webpage"
        if isinstance(msg.media, (MessageMediaPoll, MessageMediaContact, MessageMediaGeo, MessageMediaVenue, MessageMediaInvoice)):
            return "unsupported"
        return "other_media"
    if msg.text and msg.text.strip():
        return "text"
    return "empty"


def is_forwardable(msg: Message) -> bool:
    kind = detect_type(msg)
    return kind not in {"unsupported", "empty", "other_media"}


def match_type(msg: Message, allowed: Optional[Set[str]]):
    if not allowed:
        return True
    kind = detect_type(msg)
    # allow minor synonyms
    if kind == "photo" and "image" in allowed:
        return True
    return kind in allowed


def compute_message_fingerprint(msg: Message) -> Optional[str]:
    """Stable-ish fingerprint for deduplication."""
    parts: List[str] = []
    parts.append(str(detect_type(msg)))
    if msg.media and getattr(msg.media, "document", None):
        parts.append(str(getattr(msg.media.document, "id", "")))
        parts.append(str(getattr(msg.media.document, "access_hash", "")))
    if msg.media and getattr(msg.media, "photo", None):
        parts.append(str(getattr(msg.media.photo, "id", "")))
        parts.append(str(getattr(msg.media.photo, "access_hash", "")))
    if msg.web_preview:
        parts.append(str(getattr(msg.web_preview, "url", "")))
    if msg.text:
        parts.append(msg.text.strip())
    raw = "|".join(parts)
    if not raw:
        return None
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def resolve_reply_id(reply_obj) -> Optional[int]:
    """Convert InputReplyToMessage or msg to a reply target ID."""
    if reply_obj is None:
        return None
    return getattr(reply_obj, "top_msg_id", None) or getattr(reply_obj, "reply_to_msg_id", None)


class AdaptiveController:
    """Simple adaptive rate/batch controller based on recent outcomes."""
    def __init__(self, base_min: float, base_max: float, base_batch: int):
        self.base_min = base_min
        self.base_max = base_max
        self.base_batch = base_batch
        self.factor = 1.0
        self._floor_batch = 1
        self._ceil_batch = max(1, base_batch)

    def on_success(self):
        # Gentle recovery toward baseline
        self.factor = max(0.8, self.factor * 0.98)

    def on_error(self):
        self.factor = min(3.0, self.factor * 1.1)

    def on_floodwait(self):
        self.factor = min(3.5, self.factor * 1.25)

    def current_delays(self) -> Tuple[float, float]:
        return self.base_min * self.factor, self.base_max * self.factor

    def current_batch(self) -> int:
        return max(self._floor_batch, min(self._ceil_batch, int(self.base_batch / self.factor) or 1))


def adaptive_delay(min_d: float, max_d: float, count: int) -> float:
    if count and count % 500 == 0:
        max_d += 1.0
        min_d += 0.5
    return random.uniform(min_d, max_d)


def _split_text(text: str, limit: int) -> List[str]:
    if len(text) <= limit:
        return [text]
    chunks: List[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + limit, n)
        cut = text.rfind("\n", start, end)
        if cut == -1:
            cut = text.rfind(" ", start, end)
        if cut == -1 or cut <= start:
            cut = end
        chunks.append(text[start:cut])
        start = cut
    return [c for c in chunks if c]


async def _send_text_chunks(client, dst, text: str, reply_to=None):
    if len(text) <= TEXT_MAX:
        return [await client.send_message(entity=dst, message=text, link_preview=True, reply_to=reply_to)]
    msgs = []
    for chunk in _split_text(text, TEXT_MAX):
        m = await client.send_message(entity=dst, message=chunk, link_preview=True, reply_to=reply_to)
        msgs.append(m)
        reply_to = m.id
    return msgs


async def _send_media_with_long_caption_fallback(client, dst, msg: Message, reply_to=None):
    text = (msg.text or "").strip()
    if not msg.media:
        return await _send_text_chunks(client, dst, text, reply_to=reply_to)

    if len(text) == 0:
        return [await client.send_message(entity=dst, message="", file=msg.media, link_preview=False, reply_to=reply_to)]

    if len(text) <= CAPTION_MAX:
        return [await client.send_message(entity=dst, message=text, file=msg.media, link_preview=False, reply_to=reply_to)]

    sent_media = await client.send_message(entity=dst, message="", file=msg.media, link_preview=False, reply_to=reply_to)
    await _send_text_chunks(client, dst, text, reply_to=sent_media.id)
    return [sent_media]


async def _send_album_with_caption(client, dst, media_list, caption: str, reply_to=None):
    """Send an album preserving caption; split caption if too long."""
    caption = (caption or "").strip()
    if not media_list:
        return []
    if caption and len(caption) > CAPTION_MAX:
        sent = await client.send_file(entity=dst, file=media_list, caption="", reply_to=reply_to, link_preview=False)
        first_msg = sent[0] if isinstance(sent, list) else sent
        await _send_text_chunks(client, dst, caption, reply_to=getattr(first_msg, "id", None))
        return sent if isinstance(sent, list) else [sent]
    sent = await client.send_file(entity=dst, file=media_list, caption=caption, reply_to=reply_to, link_preview=False)
    return sent if isinstance(sent, list) else [sent]


async def _forward_batch(client, src, dst, ids: List[int], logger: logging.Logger):
    """Forward a batch preserving order."""
    if not ids:
        return
    await client.forward_messages(dst, ids, from_peer=src)
    logger.info(f"Forwarded batch of {len(ids)}")


async def bulk_forward(
    client,
    src_raw: str,
    dst_raw: str,
    cfg: ForwardConfig,
    stats: Stats,
    logger: logging.Logger,
) -> Stats:
    # Resolve entities with reconnect if needed
    since_dt = None
    until_dt = None
    if cfg.resume_from_date:
        try:
            since_dt = datetime.fromisoformat(cfg.resume_from_date)
        except Exception:
            logger.warning("Invalid resume_from_date: %s", cfg.resume_from_date)
    if cfg.since:
        try:
            since_dt = datetime.fromisoformat(cfg.since)
        except Exception:
            pass
    if cfg.until:
        try:
            until_dt = datetime.fromisoformat(cfg.until)
        except Exception:
            pass

    for _ in range(2):
        try:
            src = await client.get_entity(int(src_raw)) if str(src_raw).lstrip("-").isdigit() else await client.get_entity(src_raw)
            dst = await client.get_entity(int(dst_raw)) if str(dst_raw).lstrip("-").isdigit() else await client.get_entity(dst_raw)
            break
        except Exception as e:
            if is_transient_network_error(e):
                await ensure_connected(client, logger)
                continue
            raise

    # Resume window
    last_id = cfg.resume_state.get("last_id")
    if last_id is not None and cfg.resume_path:
        if cfg.resume_from_id:
            last_id = cfg.resume_from_id
        if cfg.order == "oldest":
            # Use last_id as the lower bound (not +1) to avoid skipping the next message;
            # deduplication will skip already-sent messages.
            cfg.start_id = max(cfg.start_id or 0, last_id)
        else:
            # For newest-first, allow reprocessing the last_id boundary too.
            cfg.end_id = min(cfg.end_id or 10**18, last_id)

    moving_min = cfg.start_id
    moving_max = cfg.end_id

    try:
        hist = await client(functions.messages.GetHistoryRequest(
            src, limit=1, add_offset=0, offset_id=0, max_id=0, min_id=0, hash=0
        ))
        stats.total = getattr(hist, "count", None)
        latest_msg = None
        try:
            latest_msg = hist.messages[0] if getattr(hist, "messages", None) else None
        except Exception:
            latest_msg = None
        latest_id = getattr(latest_msg, "id", None)
        # If resume would skip everything (no newer messages), reset resume to allow replay
        if latest_id is not None and last_id is not None and last_id >= latest_id:
            cfg.resume_state = {}
            cfg.resume_path = None
            cfg.start_id = None
            cfg.end_id = None
            last_id = None
            moving_min = None
            moving_max = None
            logger.info("Resume checkpoint is at or beyond the latest message; replaying from start.")
    except Exception:
        stats.total = None

    last_forwarded_id = None
    batch_ids: List[int] = []
    dedup_cache: Dict[str, float] = {}
    throttle = AdaptiveController(cfg.min_delay, cfg.max_delay, cfg.batch_size)
    seen_grouped: Set[int] = set()

    def _iter_kwargs():
        kwargs = {"reverse": True if cfg.order == "oldest" else False}
        if moving_min is not None:
            kwargs["min_id"] = moving_min
        if moving_max is not None:
            kwargs["max_id"] = moving_max
        return kwargs

    while True:
        try:
            iterator = client.iter_messages(src, **_iter_kwargs())
            max_reached = False
            async for msg in iterator:
                if cfg.pause_event and cfg.pause_event.is_set():
                    while cfg.pause_event.is_set():
                        await asyncio.sleep(0.25)
                if cfg.stop_event.is_set():
                    logger.info("Stop requested. Halting.")
                    raise asyncio.CancelledError

                stats.processed += 1
                if cfg.max_messages and stats.processed > cfg.max_messages:
                    max_reached = True
                    logger.info("Reached max_messages=%s; stopping iteration.", cfg.max_messages)
                    break

                if since_dt and msg.date and msg.date < since_dt:
                    continue
                if until_dt and msg.date and msg.date > until_dt:
                    continue

                if not match_type(msg, cfg.types) or not is_forwardable(msg):
                    stats.skipped += 1
                    continue

                fp = compute_message_fingerprint(msg) if cfg.dedup_enabled else None
                now = time.monotonic()
                if fp:
                    # cleanup expired
                    dedup_cache = {k: v for k, v in dedup_cache.items() if now - v <= cfg.dedup_ttl}
                    if fp in dedup_cache:
                        stats.skipped += 1
                        continue
                    dedup_cache[fp] = now

                topic_id = None
                try:
                    if msg.reply_to and getattr(msg.reply_to, "reply_to_top_id", None):
                        topic_id = msg.reply_to.reply_to_top_id
                except Exception:
                    topic_id = None
                top_reply = None
                if topic_id:
                    from telethon.tl.types import InputReplyToMessage
                    top_reply = InputReplyToMessage(reply_to_msg_id=None, top_msg_id=topic_id)
                reply_target = resolve_reply_id(top_reply)

                group_id = getattr(msg, "grouped_id", None)
                if group_id and group_id in seen_grouped:
                    continue
                is_album = bool(group_id)

                if cfg.anonymize:
                    # send immediately with fallback logic
                    if not cfg.dry_run:
                        dmin, dmax = throttle.current_delays() if cfg.adaptive else (cfg.min_delay, cfg.max_delay)
                        await asyncio.sleep(adaptive_delay(dmin, dmax, stats.forwarded))
                    retries = 0
                    while retries < 5:
                        try:
                            if cfg.dry_run:
                                logger.info(f"Dry-run: would send #{msg.id}")
                                break
                            if is_album:
                                # collect album chunk
                                ids = [msg.id]
                                async for sibling in client.iter_messages(src, reverse=True if cfg.order == "oldest" else False, min_id=msg.id-10, max_id=msg.id+10):
                                    if sibling.id == msg.id:
                                        continue
                                    if sibling.grouped_id == group_id:
                                        ids.append(sibling.id)
                                ids = sorted(set(ids))
                                seen_grouped.add(group_id)
                                media_list = []
                                caption_text = msg.text.strip() if msg.text else ""
                                async for alb_msg in client.iter_messages(src, ids=ids):
                                    if alb_msg.media:
                                        media_list.append(alb_msg.media)
                                    if not caption_text and alb_msg.text:
                                        caption_text = alb_msg.text.strip()
                                if caption_text and len(caption_text) > CAPTION_MAX:
                                    sent = await client.send_file(entity=dst, file=media_list, caption="", reply_to=reply_target, link_preview=False)
                                    first_msg = sent[0] if isinstance(sent, list) else sent
                                    await _send_text_chunks(client, dst, caption_text, reply_to=getattr(first_msg, "id", None))
                                else:
                                    await client.send_file(entity=dst, file=media_list, caption=caption_text, reply_to=reply_target, link_preview=False)
                            elif isinstance(msg.media, MessageMediaWebPage):
                                wp = getattr(msg.media, "webpage", None)
                                url = getattr(wp, "url", None) or getattr(wp, "display_url", None)
                                content = (msg.text or "").strip() or url
                                if not content:
                                    stats.skipped += 1
                                    break
                                await _send_text_chunks(client, dst, content, reply_to=reply_target)
                            else:
                                await _send_media_with_long_caption_fallback(client, dst, msg, reply_to=reply_target)
                            stats.forwarded += 1
                            last_forwarded_id = msg.id
                            stats.last_id = msg.id
                            stats.last_msg = f"Forwarded message {msg.id} ({detect_type(msg)})"
                            logger.info(stats.last_msg)
                            if fp:
                                dedup_cache[fp] = now
                            throttle.on_success()
                            if group_id:
                                seen_grouped.add(group_id)
                            stats.throttle = throttle.factor
                            break
                        except FloodWaitError as e:
                            logger.warning(f"FloodWait {e.seconds}s on #{msg.id}")
                            await asyncio.sleep(e.seconds + 2)
                            throttle.on_floodwait()
                            stats.throttle = throttle.factor
                            retries += 1
                        except MessageEmptyError:
                            stats.skipped += 1
                            break
                        except Exception as e:
                            if is_transient_network_error(e):
                                await asyncio.sleep(min(30, 1.5 * (retries + 1)))
                                logger.warning(f"Transient network error on #{msg.id}: {e}. Reconnecting…")
                                await ensure_connected(client, logger)
                                throttle.on_error()
                                stats.throttle = throttle.factor
                                retries += 1
                                continue
                            # long caption or generic failure already handled by fallback path
                            stats.errors += 1
                            logger.error(f"Error forwarding #{msg.id}: {e}")
                            throttle.on_error()
                            stats.throttle = throttle.factor
                            retries += 1
                            await asyncio.sleep(3)
                else:
                    # batch forward path
                    # album handling: forward grouped ids together
                    if is_album:
                        ids = [msg.id]
                        async for sibling in client.iter_messages(src, reverse=True if cfg.order == "oldest" else False, min_id=msg.id-10, max_id=msg.id+10):
                            if sibling.id == msg.id:
                                continue
                            if sibling.grouped_id == group_id:
                                ids.append(sibling.id)
                        ids = sorted(set(ids))
                        batch_ids.extend(ids)
                        seen_grouped.add(group_id)
                    else:
                        batch_ids.append(msg.id)
                    last_forwarded_id = msg.id
                    current_batch_target = max(1, throttle.current_batch() if cfg.adaptive else cfg.batch_size)
                    if len(batch_ids) >= current_batch_target:
                        if not cfg.dry_run:
                            dmin, dmax = throttle.current_delays() if cfg.adaptive else (cfg.min_delay, cfg.max_delay)
                            await asyncio.sleep(adaptive_delay(dmin, dmax, stats.forwarded))
                        retries = 0
                        while retries < 5:
                            try:
                                if cfg.dry_run:
                                    logger.info(f"Dry-run: would forward batch {len(batch_ids)}")
                                else:
                                    await client.forward_messages(dst, batch_ids, from_peer=src, top_msg_id=topic_id or None)
                                stats.forwarded += len(batch_ids)
                                stats.last_id = batch_ids[-1]
                                for _ in batch_ids:
                                    throttle.on_success()
                                stats.throttle = throttle.factor
                                batch_ids.clear()
                                break
                            except FloodWaitError as e:
                                logger.warning(f"FloodWait {e.seconds}s on batch")
                                await asyncio.sleep(e.seconds + 2)
                                throttle.on_floodwait()
                                stats.throttle = throttle.factor
                                retries += 1
                            except Exception as e:
                                if is_transient_network_error(e):
                                    await asyncio.sleep(min(30, 1.5 * (retries + 1)))
                                    logger.warning(f"Transient network error on batch: {e}. Reconnecting…")
                                    await ensure_connected(client, logger)
                                    throttle.on_error()
                                    stats.throttle = throttle.factor
                                    retries += 1
                                    continue
                                stats.errors += 1
                                logger.error(f"Batch forward failed: {e}")
                                throttle.on_error()
                                stats.throttle = throttle.factor
                                retries += 1
                                await asyncio.sleep(3)

                # periodic checkpoint
                if stats.forwarded and (stats.forwarded % 20 == 0) and cfg.resume_path and last_forwarded_id:
                    write_resume(cfg.resume_path, last_forwarded_id)

            # flush remaining batch when iterator ends
            if not cfg.anonymize and batch_ids:
                if not cfg.dry_run:
                    dmin, dmax = throttle.current_delays() if cfg.adaptive else (cfg.min_delay, cfg.max_delay)
                    await asyncio.sleep(adaptive_delay(dmin, dmax, stats.forwarded))
                retries = 0
                while retries < 5:
                    try:
                        if cfg.dry_run:
                            logger.info(f"Dry-run: would forward final batch {len(batch_ids)}")
                        else:
                            await client.forward_messages(dst, batch_ids, from_peer=src, top_msg_id=topic_id or None)
                        stats.forwarded += len(batch_ids)
                        stats.last_id = batch_ids[-1]
                        last_forwarded_id = batch_ids[-1]
                        batch_ids.clear()
                        throttle.on_success()
                        stats.throttle = throttle.factor
                        break
                    except FloodWaitError as e:
                        logger.warning(f"FloodWait {e.seconds}s on final batch")
                        await asyncio.sleep(e.seconds + 2)
                        throttle.on_floodwait()
                        stats.throttle = throttle.factor
                        retries += 1
                    except Exception as e:
                        if is_transient_network_error(e):
                            await asyncio.sleep(min(30, 1.5 * (retries + 1)))
                            logger.warning(f"Transient network error on final batch: {e}. Reconnecting…")
                            await ensure_connected(client, logger)
                            throttle.on_error()
                            stats.throttle = throttle.factor
                            retries += 1
                            continue
                        stats.errors += 1
                        logger.error(f"Final batch forward failed: {e}")
                        throttle.on_error()
                        stats.throttle = throttle.factor
                        retries += 1
                        await asyncio.sleep(3)

            # If nothing was processed and resume skipped everything, replay once without resume.
            if stats.processed == 0 and cfg.resume_state and not replayed_resume:
                cfg.resume_state = {}
                cfg.resume_path = None
                cfg.start_id = None
                cfg.end_id = None
                moving_min = None
                moving_max = None
                replayed_resume = True
                logger.info("No messages processed; clearing resume to replay from start.")
                continue
            if max_reached:
                break
            break

        except asyncio.CancelledError:
            break
        except Exception as e:
            if is_transient_network_error(e):
                logger.warning(f"Iterator dropped due to network error: {e}. Reconnecting and resuming…")
                await ensure_connected(client, logger)
                await asyncio.sleep(1.0)
                continue
            stats.errors += 1
            logger.error(f"Fatal iteration error: {e}")
            break

    if cfg.resume_path and last_forwarded_id:
        write_resume(cfg.resume_path, last_forwarded_id)

    return stats
