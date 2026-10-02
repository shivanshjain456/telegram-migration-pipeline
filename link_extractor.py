# -*- coding: utf-8 -*-
"""
Telegram Migration Pipeline — Link Extractor & Data Export Engine
- Robust Telegram URL and @handle extraction from message bodies, entities, buttons, and previews.
- Canonical URL normalization (t.me, telegram.me, telegram.dog).
- Deduplication and metadata preservation (source channel, message ID, timestamp, snippet).
- Multi-format export: JSON, CSV, and Excel (openpyxl).
"""
from __future__ import annotations

import csv
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)

try:
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter
except ImportError:  # pragma: no cover
    Workbook = None
    get_column_letter = None


@dataclass
class LinkRecord:
    link: str
    dialog_title: str
    dialog_type: str
    message_id: int
    message_date: Optional[str]
    snippet: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class LinkExtractor:
    """Extracts, sanitizes, and normalizes Telegram links and channel handles."""

    SUPPORTED_DOMAINS = ("t.me", "telegram.me", "telegram.dog")
    LINK_PATTERN = re.compile(
        r"(?:https?://)?(?:t\.me|telegram\.me|telegram\.dog)/[^\s<>\"')]+",
        re.IGNORECASE,
    )
    HANDLE_PATTERN = re.compile(r"@([A-Za-z0-9_]{5,32})")

    @staticmethod
    def _sanitize_token(token: str) -> str:
        return token.strip(" \t\r\n<>()[]{}\"'.,!?:;")

    def normalize(self, raw: Optional[str]) -> Optional[str]:
        """Normalize raw link or handle into a canonical https://t.me/... link."""
        if not raw:
            return None
        token = self._sanitize_token(raw)
        if not token:
            return None

        # Format handles
        if token.startswith("@"):
            username = token[1:]
            if len(username) < 5:
                return None
            return f"https://t.me/{username}"

        lower = token.lower()
        if lower.startswith("tg://"):
            return None

        if not lower.startswith(("http://", "https://")):
            token = f"https://{token.lstrip('/')}"

        try:
            parsed = urlparse(token, scheme="https")
            domain = parsed.netloc.lower()
            if domain.startswith("www."):
                domain = domain[4:]

            if domain not in self.SUPPORTED_DOMAINS:
                return None

            path = parsed.path or ""
            if path != "/" and path.endswith("/"):
                path = path.rstrip("/")

            if not path or path == "/":
                return None

            return urlunparse(("https", domain, path, "", parsed.query, parsed.fragment))
        except Exception:
            return None

    def extract_from_text(self, text: Optional[str]) -> Set[str]:
        """Extract links and handles from plain text body."""
        if not text:
            return set()
        results: Set[str] = set()
        for match in self.LINK_PATTERN.findall(text):
            norm = self.normalize(match)
            if norm:
                results.add(norm)
        for handle in self.HANDLE_PATTERN.findall(text):
            norm = self.normalize(f"@{handle}")
            if norm:
                results.add(norm)
        return results

    def extract_from_message(self, message: Any) -> Set[str]:
        """Extract all links from a Telethon Message object."""
        links: Set[str] = set()

        # 1. Plain text and caption
        text = getattr(message, "message", None) or getattr(message, "text", None)
        links |= self.extract_from_text(text)

        # 2. Rich text entities (MessageEntityTextUrl)
        entities = getattr(message, "entities", None)
        if entities:
            for entity in entities:
                url = getattr(entity, "url", None)
                if url:
                    norm = self.normalize(url)
                    if norm:
                        links.add(norm)

        # 3. Inline keyboard buttons
        buttons = getattr(message, "buttons", None)
        if buttons:
            for row in buttons:
                for button in row:
                    url = getattr(button, "url", None)
                    if url:
                        norm = self.normalize(url)
                        if norm:
                            links.add(norm)

        # 4. Webpage preview media
        media = getattr(message, "media", None)
        webpage = getattr(media, "webpage", None) if media else None
        if webpage:
            url = getattr(webpage, "url", None)
            if url:
                norm = self.normalize(url)
                if norm:
                    links.add(norm)

        return links


def export_links_to_json(records: List[LinkRecord], filepath: str) -> None:
    """Save records to JSON file."""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump([r.to_dict() for r in records], f, indent=2, ensure_ascii=False)


def export_links_to_csv(records: List[LinkRecord], filepath: str) -> None:
    """Save records to CSV file."""
    if not records:
        with open(filepath, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["link", "dialog_title", "dialog_type", "message_id", "message_date", "snippet"])
        return

    with open(filepath, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["link", "dialog_title", "dialog_type", "message_id", "message_date", "snippet"]
        )
        writer.writeheader()
        for r in records:
            writer.writerow(r.to_dict())


def export_links_to_excel(records: List[LinkRecord], filepath: str) -> bool:
    """Save records to Excel (.xlsx) file if openpyxl is available."""
    if Workbook is None:
        logger.warning("openpyxl is not installed; falling back to CSV.")
        csv_path = filepath.rsplit(".", 1)[0] + ".csv"
        export_links_to_csv(records, csv_path)
        return False

    wb = Workbook()
    ws = wb.active
    ws.title = "Extracted Links"

    headers = ["Link", "Channel / Dialog", "Type", "Message ID", "Date", "Snippet"]
    ws.append(headers)

    for r in records:
        ws.append([
            r.link,
            r.dialog_title,
            r.dialog_type,
            r.message_id,
            r.message_date or "",
            r.snippet,
        ])

    for col in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        col_letter = get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = min(max(max_len + 2, 12), 60)

    wb.save(filepath)
    return True
