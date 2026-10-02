#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Bulk Forwarder
- Multiple sources via comma or newline in the Source field.
- Per-source resume and a final summary dialog.
- Robust cancellation handling on Python 3.12+.
"""
import asyncio
import contextlib
import json
import logging
import os
import sys
import threading
import traceback
import random
import shutil
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from dotenv import load_dotenv
from telethon import TelegramClient

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSettings  # type: ignore
from PyQt6.QtGui import (
    QAction,
    QColor,
    QFont,
    QPalette,
    QStandardItem,
    QStandardItemModel,
    QTextCursor,
    QKeySequence,
    QShortcut,
)  # type: ignore
from PyQt6.QtWidgets import (  # type: ignore
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QDialog,
    QPushButton,
    QProgressBar,
    QSpinBox,
    QDoubleSpinBox,
    QTableWidget,
    QVBoxLayout,
    QWidget,
    QComboBox,
    QCheckBox,
    QTextEdit,
    QHeaderView,
    QFrame,
    QSplitter,
    QInputDialog,
)

from utils import (
    ForwardConfig,
    Stats,
    bulk_forward,
    build_route_key,
    parse_source_list,
    resolve_resume_state,
    normalize_delay_range,
    normalize_types,
    resolve_storage_dir,
    sanitize_session_name,
    RESUME_DIR,
)

# UI spacing / sizing system
GAP_SM = 8
GAP_MD = 12
GAP_LG = 16
CARD_RADIUS = 14
BTN_MIN_HEIGHT = 32
FIELD_MIN_HEIGHT = 30
BTN_MIN_WIDTH_PRIMARY = 110
BTN_MIN_WIDTH_SECONDARY = 90

# Predefined account presets for quick switching (secrets resolved from env vars)
ACCOUNT_PRESETS = [
    {"name": "Account 1 (Primary)", "env_api_id": "ACCOUNT1_API_ID", "env_api_hash": "ACCOUNT1_API_HASH", "session": "account1"},
    {"name": "Account 2 (Secondary)", "env_api_id": "ACCOUNT2_API_ID", "env_api_hash": "ACCOUNT2_API_HASH", "session": "account2"},
    {"name": "Default Account", "env_api_id": "API_ID", "env_api_hash": "API_HASH", "session": "migration_session"},
]

SESS_DIR = resolve_storage_dir("SESSION_DIR", "sessions", logging.getLogger("tg-bulk-gui"))


def resolve_preset_credentials(preset: dict) -> Tuple[str, str, str, str, str]:
    """
    Load credentials for a preset from environment variables.
    Uses explicitly configured env keys when present, otherwise derives keys from the session/name.
    Falls back to generic API_ID/API_HASH if per-preset keys are absent.
    """
    base_name = preset.get("session") or preset.get("name", "session")
    derived_base = sanitize_session_name(base_name).upper().replace("-", "_").replace(".", "_")
    env_api_id_key = preset.get("env_api_id") or f"{derived_base}_API_ID"
    env_api_hash_key = preset.get("env_api_hash") or f"{derived_base}_API_HASH"
    api_id = os.getenv(env_api_id_key) or os.getenv("API_ID", "")
    api_hash = os.getenv(env_api_hash_key) or os.getenv("API_HASH", "")
    session_env = os.getenv(preset.get("env_session_name", ""), "")
    session_default = base_name
    session_name = sanitize_session_name(session_env or session_default)
    return api_id, api_hash, session_name, env_api_id_key, env_api_hash_key
# Reduce Telethon noise
logging.getLogger("telethon").setLevel(logging.WARNING)
logging.getLogger("telethon.network").setLevel(logging.ERROR)


class MultiSelectComboBox(QComboBox):
    """QComboBox with checkable items. Blank display == All types."""
    def __init__(self, options: List[str], parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().setPlaceholderText("All types")
        self._model = QStandardItemModel(self)
        self.setModel(self._model)
        for opt in options:
            it = QStandardItem(opt)
            it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            it.setData(Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
            self._model.appendRow(it)
        self._model.dataChanged.connect(self._refresh_text)
        self.view().pressed.connect(self._on_item_pressed)
        self._refresh_text()

    def _on_item_pressed(self, index):
        item = self._model.itemFromIndex(index)
        if not item:
            return
        item.setCheckState(
            Qt.CheckState.Unchecked
            if item.checkState() == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )

    def selected_items(self) -> List[str]:
        out = []
        for i in range(self._model.rowCount()):
            it = self._model.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                out.append(it.text())
        return out

    def set_selected(self, values: List[str]):
        s = set(v.lower() for v in values)
        for i in range(self._model.rowCount()):
            it = self._model.item(i)
            it.setCheckState(
                Qt.CheckState.Checked
                if it.text().lower() in s
                else Qt.CheckState.Unchecked
            )
        self._refresh_text()

    def _refresh_text(self):
        vals = self.selected_items()
        self.lineEdit().setText(", ".join(vals) if vals else "")


@dataclass
class RowContext:
    cfg: ForwardConfig
    api_id: int
    api_hash: str
    session_name: str
    sources: List[str]  # queue
    target: str
    resume_enabled: bool
    dedup_ttl: float
    pause_event: threading.Event


class AccountWorker(QThread):
    # row, forwarded, total (-1 if unknown), current source
    progressed = pyqtSignal(int, int, int, object, float)
    # row, per-source summary dict
    finished = pyqtSignal(int, dict)
    failed = pyqtSignal(int, str)
    logline = pyqtSignal(int, str)

    def __init__(self, row: int, ctx: RowContext):
        super().__init__()
        self.row = row
        self.ctx = ctx
        self._loop = None
        self._client: TelegramClient | None = None
        self._per_source_totals: Dict[str, Dict[str, int]] = {}

    def run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run_async())
        except BaseException as e:  # includes CancelledError on 3.12
            self.failed.emit(self.row, str(e))
        finally:
            self._loop.close()

    async def _run_async(self):
        session_name = sanitize_session_name(self.ctx.session_name.strip() or "session")
        sess_path = os.path.join(SESS_DIR, session_name)
        self.logline.emit(self.row, "Connecting to Telegram…")
        self._client = TelegramClient(sess_path, self.ctx.api_id, self.ctx.api_hash, connection_retries=None, retry_delay=2, request_retries=5)
        await self._client.start()
        self.logline.emit(self.row, "Connected. Starting queue…")

        try:
            for idx, src in enumerate(self.ctx.sources, start=1):
                if self.ctx.cfg.stop_event.is_set():
                    break

                route_key = build_route_key(src, self.ctx.target, self.ctx.session_name)
                resume_state, resume_path, stale = resolve_resume_state(route_key, self.ctx.resume_enabled, RESUME_DIR)
                if stale:
                    self.logline.emit(self.row, f"Resume file is older than {os.getenv('RESUME_STALE_DAYS', '30')} days; consider resetting.")

                per_cfg = ForwardConfig(
                    batch_size=self.ctx.cfg.batch_size,
                    min_delay=self.ctx.cfg.min_delay,
                    max_delay=self.ctx.cfg.max_delay,
                    anonymize=self.ctx.cfg.anonymize,
                    dry_run=self.ctx.cfg.dry_run,
                    start_id=None, end_id=None, since=None, until=None,
                    order=self.ctx.cfg.order,
                    max_messages=self.ctx.cfg.max_messages,
                    types=self.ctx.cfg.types,
                    stop_event=self.ctx.cfg.stop_event,
                    resume_state=resume_state,
                    resume_path=resume_path,
                )

                stats = Stats()
                self.logline.emit(self.row, f"[{idx}/{len(self.ctx.sources)}] {src} → {self.ctx.target}")

                async def pulse():
                    while not self.ctx.cfg.stop_event.is_set():
                        total = stats.total if stats.total is not None else -1
                        self.progressed.emit(self.row, stats.forwarded, total, src, stats.throttle)
                        await asyncio.sleep(0.5)

                pulse_task = asyncio.create_task(pulse())
                try:
                    await bulk_forward(self._client, src, self.ctx.target, per_cfg, stats, logging.getLogger(f"gui-row-{self.row}"))
                finally:
                    pulse_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await pulse_task

                self._per_source_totals[src] = {
                    "forwarded": stats.forwarded,
                    "skipped": stats.skipped,
                    "errors": stats.errors,
                }
                self.logline.emit(self.row, f"Done {src}: fwd={stats.forwarded} skip={stats.skipped} err={stats.errors}")

        finally:
            if self._client and self._client.is_connected():
                with contextlib.suppress(Exception):
                    await self._client.disconnect()
            self.finished.emit(self.row, {"totals": self._per_source_totals})


class ForwarderGUI(QMainWindow):
    TYPE_OPTIONS = ["text", "photo", "image", "document", "video", "audio", "webpage"]
    COLS = [
        "Enable","Session","API_ID","API_HASH","Source(s)","Target",
        "Batch","MinD","MaxD","Order","Types",
        "Resume","DryRun","Anonymize","Notes","Progress"
    ]

    def __init__(self):
        super().__init__()
        load_dotenv()
        self.setWindowTitle("Telegram Bulk Forwarder - GUI")
        self.resize(1180, 740)
        self.setFont(QFont("Segoe UI", 10))
        self.dark_mode = True
        self._apply_theme(self.dark_mode)
        self.settings = QSettings("TelegramBulkForwarder", "GUI")

        self.workers: Dict[int, AccountWorker] = {}
        self.row_cfgs: Dict[int, ForwardConfig] = {}
        self.completed_runs = 0
        self.pending_rows: List[int] = []
        self.global_pause = threading.Event()
        self._last_summary: str = ""
        self.dedup_ttl_seconds = 3600.0
        self.max_concurrent = 2
        self.row_start_time: Dict[int, float] = {}
        self.log_filter_level = "all"

        root = QWidget(self)
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(GAP_LG, GAP_LG, GAP_LG, GAP_MD)
        v.setSpacing(GAP_LG)

        v.addWidget(self._build_header())

        controls_card = self._make_card("Quick Controls", self._build_toolbar(), "Launch, duplicate, or stop account queues.")
        v.addWidget(controls_card)

        self.splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.splitter.addWidget(self._make_card("Accounts", self._build_table(), "Configure sessions, sources, and destinations."))
        self.splitter.addWidget(self._make_card("Live Logs", self._build_log_area(), "Real-time updates, errors, and summaries."))
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 1)
        v.addWidget(self.splitter, 1)

        self.statusBar().showMessage("Idle")

        self.add_account_row(prefill_from_env=True)
        self._restore_layout()
        self._update_status_summary()
        if "onedrive" in SESS_DIR.lower():
            self._append_log("Warning: session directory is under OneDrive; set SESSION_DIR env to move it out of synced folders.", level="warn")
        self._setup_shortcuts()

    def _apply_theme(self, dark: bool):
        dark_style = """
            QWidget {{ background-color: #000000; color: #ffffff; }}
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit {{
                background-color: #121212; border: 1px solid #222; border-radius: 6px; padding: 4px;
            }}
            QPushButton {{
                background-color: #0a84ff; color: white; border-radius: 6px; padding: 8px 12px;
            }}
            QPushButton:hover {{ background-color: #1c90ff; }}
            QHeaderView::section {{ background-color: #0e0e0e; border: none; padding: 6px; }}
            QProgressBar {{ border: 1px solid #333; border-radius: 4px; text-align: center; }}
            QProgressBar::chunk {{ background-color: #0a84ff; }}
            QTableWidget {{ gridline-color: #222; }}
            QFrame#HeroHeader {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1d2671, stop:1 #c33764);
                border-radius: 18px;
                padding: 18px;
            }}
            QLabel#HeroTitle {{ font-size: 24px; font-weight: 700; }}
            QLabel#HeroSubtitle {{ color: #d7d7d7; font-size: 13px; }}
            QLabel#StatusBadge {{
                background-color: rgba(255,255,255,0.15);
                padding: 6px 12px;
                border-radius: 16px;
                font-weight: 600;
            }}
            QLabel#HeroMetric {{
                background-color: #111;
                border-radius: 12px;
                padding: 10px 14px;
                font-weight: 600;
            }}
            QFrame#SurfaceCard {{
                background-color: #080808;
                border: 1px solid #1a1a1a;
                border-radius: 14px;
            }}
            QLabel#CardTitle {{ font-size: 16px; font-weight: 600; }}
            QLabel#CardSubtitle {{ color: #bbbbbb; font-size: 12px; }}
            QPushButton {{
                min-height: {btn_height}px;
                padding: 6px 12px;
            }}
            QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{
                min-height: {field_height}px;
            }}
        """

        light_style = """
            QWidget {{ background-color: #ffffff; color: #000000; }}
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit {{
                background-color: #ffffff; border: 1px solid #d0d0d0; border-radius: 6px; padding: 4px;
            }}
            QPushButton {{
                background-color: #0a84ff; color: white; border-radius: 6px; padding: 8px 12px;
            }}
            QPushButton:hover {{ background-color: #1c90ff; }}
            QHeaderView::section {{ background-color: #f0f0f0; border: none; padding: 6px; }}
            QProgressBar {{ border: 1px solid #cccccc; border-radius: 4px; text-align: center; }}
            QProgressBar::chunk {{ background-color: #0a84ff; }}
            QTableWidget {{ gridline-color: #dddddd; }}
            QFrame#HeroHeader {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #88c0d0, stop:1 #81a1c1);
                border-radius: 18px;
                padding: 18px;
            }}
            QLabel#HeroTitle {{ font-size: 24px; font-weight: 700; }}
            QLabel#HeroSubtitle {{ color: #333333; font-size: 13px; }}
            QLabel#StatusBadge {{
                background-color: rgba(0,0,0,0.07);
                padding: 6px 12px;
                border-radius: 16px;
                font-weight: 600;
            }}
            QLabel#HeroMetric {{
                background-color: #eef2f7;
                border-radius: 12px;
                padding: 10px 14px;
                font-weight: 600;
            }}
            QFrame#SurfaceCard {{
                background-color: #fbfbfb;
                border: 1px solid #e0e0e0;
                border-radius: 14px;
            }}
            QLabel#CardTitle {{ font-size: 16px; font-weight: 600; }}
            QLabel#CardSubtitle {{ color: #555555; font-size: 12px; }}
            QPushButton {{
                min-height: {btn_height}px;
                padding: 6px 12px;
            }}
            QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{
                min-height: {field_height}px;
            }}
        """

        if dark:
            pal = QPalette()
            pal.setColor(QPalette.ColorRole.Window, QColor("#000000"))
            pal.setColor(QPalette.ColorRole.WindowText, QColor("#ffffff"))
            pal.setColor(QPalette.ColorRole.Base, QColor("#101010"))
            pal.setColor(QPalette.ColorRole.AlternateBase, QColor("#0d0d0d"))
            pal.setColor(QPalette.ColorRole.Text, QColor("#eeeeee"))
            pal.setColor(QPalette.ColorRole.Button, QColor("#0a84ff"))
            pal.setColor(QPalette.ColorRole.ButtonText, QColor("#ffffff"))
            pal.setColor(QPalette.ColorRole.Highlight, QColor("#1c90ff"))
            pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#000000"))
            self.setPalette(pal)
            self.setStyleSheet(dark_style.format(btn_height=BTN_MIN_HEIGHT, field_height=FIELD_MIN_HEIGHT))
        else:
            pal = QPalette()
            pal.setColor(QPalette.ColorRole.Window, QColor("#ffffff"))
            pal.setColor(QPalette.ColorRole.WindowText, QColor("#000000"))
            pal.setColor(QPalette.ColorRole.Base, QColor("#f7f7f7"))
            pal.setColor(QPalette.ColorRole.AlternateBase, QColor("#f0f0f0"))
            pal.setColor(QPalette.ColorRole.Text, QColor("#111111"))
            pal.setColor(QPalette.ColorRole.Button, QColor("#0a84ff"))
            pal.setColor(QPalette.ColorRole.ButtonText, QColor("#ffffff"))
            pal.setColor(QPalette.ColorRole.Highlight, QColor("#0a84ff"))
            pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
            self.setPalette(pal)
            self.setStyleSheet(light_style.format(btn_height=BTN_MIN_HEIGHT, field_height=FIELD_MIN_HEIGHT))

    def _build_header(self) -> QWidget:
        frame = QFrame(self)
        frame.setObjectName("HeroHeader")
        frame.setMinimumHeight(150)
        frame.setMaximumHeight(180)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(GAP_MD, GAP_MD, GAP_MD, GAP_MD)
        layout.setSpacing(GAP_MD)

        text_box = QVBoxLayout()
        text_box.setSpacing(GAP_SM)
        title = QLabel("Telegram Bulk Forwarder", frame)
        title.setObjectName("HeroTitle")
        subtitle = QLabel("Queue-driven Telethon client with batching, resume, and anonymized reposting.", frame)
        subtitle.setWordWrap(True)
        subtitle.setObjectName("HeroSubtitle")
        text_box.addWidget(title)
        text_box.addWidget(subtitle)

        self.lbl_status = QLabel("Idle", frame)
        self.lbl_status.setObjectName("StatusBadge")

        metrics_box = QHBoxLayout()
        metrics_box.setSpacing(GAP_SM)
        self.lbl_active = QLabel("0 Active", frame)
        self.lbl_completed = QLabel("0 Completed", frame)
        for lbl in (self.lbl_active, self.lbl_completed):
            lbl.setObjectName("HeroMetric")
            lbl.setMinimumWidth(110)
        metrics_box.addWidget(self.lbl_active)
        metrics_box.addWidget(self.lbl_completed)

        self.lbl_account = QLabel("Selected account: -", frame)
        self.lbl_account.setObjectName("HeroMetric")

        right = QVBoxLayout()
        right.setSpacing(GAP_SM)
        right.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        right.addWidget(self.lbl_status, alignment=Qt.AlignmentFlag.AlignRight)
        right.addWidget(self.lbl_account, alignment=Qt.AlignmentFlag.AlignRight)
        right.addLayout(metrics_box)

        layout.addLayout(text_box, stretch=2)
        layout.addLayout(right, stretch=1)
        return frame

    def _make_card(self, title: str, body: QWidget, subtitle: str = "") -> QFrame:
        card = QFrame(self)
        card.setObjectName("SurfaceCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(GAP_LG, GAP_MD, GAP_LG, GAP_LG)
        layout.setSpacing(GAP_MD)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        lbl_title = QLabel(title, card)
        lbl_title.setObjectName("CardTitle")
        header.addWidget(lbl_title)
        header.addStretch(1)
        layout.addLayout(header)

        if subtitle:
            lbl_sub = QLabel(subtitle, card)
            lbl_sub.setObjectName("CardSubtitle")
            lbl_sub.setWordWrap(True)
            layout.addWidget(lbl_sub)

        body.setParent(card)
        layout.addWidget(body)
        return card

    def _style_button(self, btn: QPushButton, primary: bool = True):
        btn.setMinimumHeight(BTN_MIN_HEIGHT)
        btn.setMinimumWidth(BTN_MIN_WIDTH_PRIMARY if primary else BTN_MIN_WIDTH_SECONDARY)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet("padding: 6px 12px; border-radius: 6px;")

    def _update_status_summary(self):
        active = len(self.workers)
        self.lbl_active.setText(f"{active} Active")
        self.lbl_completed.setText(f"{self.completed_runs} Completed")
        state = "Running" if active else "Idle"
        self.lbl_status.setText(state)
        self.statusBar().showMessage(f"{state} • {active} active queue(s)")

    def _build_toolbar(self) -> QWidget:
        box = QWidget()
        root = QVBoxLayout(box)
        root.setContentsMargins(GAP_MD, GAP_MD, GAP_MD, GAP_MD)
        root.setSpacing(GAP_SM)

        # Primary row
        row1 = QHBoxLayout()
        row1.setSpacing(GAP_SM)
        self.btn_add = QPushButton("Add account"); self._style_button(self.btn_add)
        self.btn_add.clicked.connect(self.add_account_row)
        self.btn_remove = QPushButton("Remove selected"); self._style_button(self.btn_remove)
        self.btn_remove.clicked.connect(self.remove_selected_rows)
        self.btn_load = QPushButton("Load accounts.json"); self._style_button(self.btn_load)
        self.btn_load.clicked.connect(self.load_accounts_file)
        self.btn_save = QPushButton("Save accounts.json"); self._style_button(self.btn_save)
        self.btn_save.clicked.connect(self.save_accounts_file)
        self.btn_start = QPushButton("Start all"); self._style_button(self.btn_start)
        self.btn_start.clicked.connect(self.start_all)
        self.btn_start_selected = QPushButton("Start selected"); self._style_button(self.btn_start_selected)
        self.btn_start_selected.clicked.connect(self.start_selected)
        self.btn_stop = QPushButton("Stop all"); self._style_button(self.btn_stop)
        self.btn_stop.clicked.connect(self.stop_all)
        self.btn_stop_selected = QPushButton("Stop selected"); self._style_button(self.btn_stop_selected)
        self.btn_stop_selected.clicked.connect(self.stop_selected)
        self.btn_pause = QPushButton("Pause all"); self._style_button(self.btn_pause, primary=False)
        self.btn_pause.clicked.connect(self.pause_all)
        self.btn_resume = QPushButton("Resume all"); self._style_button(self.btn_resume, primary=False)
        self.btn_resume.clicked.connect(self.resume_all)
        self.btn_bulk_apply = QPushButton("Bulk apply first selected"); self._style_button(self.btn_bulk_apply, primary=False)
        self.btn_bulk_apply.clicked.connect(self.bulk_apply_selected)
        self.btn_migrate = QPushButton("Move storage"); self._style_button(self.btn_migrate, primary=False)
        self.btn_migrate.setToolTip("Copy ./sessions and ./resume into the secure storage path.")
        self.btn_migrate.clicked.connect(self.migrate_storage)
        self.btn_credentials = QPushButton("Credentials"); self._style_button(self.btn_credentials, primary=False)
        self.btn_credentials.setToolTip("Open credentials panel to set API keys for presets.")
        self.btn_credentials.clicked.connect(self.open_credentials_panel)
        for b in [self.btn_add, self.btn_remove, self.btn_load, self.btn_save, self.btn_start, self.btn_start_selected, self.btn_stop, self.btn_stop_selected, self.btn_pause, self.btn_resume, self.btn_bulk_apply, self.btn_migrate, self.btn_credentials]:
            row1.addWidget(b)
        row1.addStretch(1)

        # Secondary row
        row2 = QHBoxLayout()
        row2.setSpacing(GAP_SM)
        self.cmb_preset_mode = QComboBox()
        self.cmb_preset_mode.setMinimumWidth(120)
        self.cmb_preset_mode.addItems(["Default", "Minimal", "Flood-safe"])
        self.btn_apply_preset_mode = QPushButton("Apply table preset"); self._style_button(self.btn_apply_preset_mode, primary=False)
        self.btn_apply_preset_mode.clicked.connect(self.apply_table_preset)

        self.spin_concurrent = QSpinBox()
        self.spin_concurrent.setRange(1, 8)
        self.spin_concurrent.setValue(self.max_concurrent)
        self.spin_concurrent.setToolTip("Max concurrent accounts")
        self.spin_concurrent.valueChanged.connect(lambda v: setattr(self, "max_concurrent", v))

        self.spin_dedup_ttl = QSpinBox()
        self.spin_dedup_ttl.setRange(60, 86400)
        self.spin_dedup_ttl.setValue(int(self.dedup_ttl_seconds))
        self.spin_dedup_ttl.setToolTip("Dedup TTL (seconds)")
        self.spin_dedup_ttl.valueChanged.connect(lambda v: setattr(self, "dedup_ttl_seconds", float(v)))

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Filter sources/targets")
        self.search_box.textChanged.connect(self.filter_rows)
        self.search_box.setMinimumWidth(160)

        self.theme_toggle = QCheckBox("Light theme")
        self.theme_toggle.stateChanged.connect(self.toggle_theme)

        self.cmb_account = QComboBox()
        self.cmb_account.setMinimumWidth(140)
        for preset in ACCOUNT_PRESETS:
            self.cmb_account.addItem(preset["name"], preset)
        self.btn_apply_account = QPushButton("Apply account to rows"); self._style_button(self.btn_apply_account, primary=False)
        self.btn_apply_account.clicked.connect(self.apply_account_to_rows)

        lbl_preset = QLabel("Preset:")
        lbl_concurrent = QLabel("Concurrent:")
        lbl_dedup = QLabel("Dedup TTL(s):")
        lbl_filter = QLabel("Filter:")
        lbl_account = QLabel("Account:")
        for widget in [
            lbl_preset, self.cmb_preset_mode, self.btn_apply_preset_mode,
            lbl_concurrent, self.spin_concurrent,
            lbl_dedup, self.spin_dedup_ttl,
            lbl_filter, self.search_box,
            self.theme_toggle,
            lbl_account, self.cmb_account, self.btn_apply_account,
        ]:
            row2.addWidget(widget)
        row2.addStretch(1)

        root.addLayout(row1)
        root.addLayout(row2)
        return box

    def _build_table(self) -> QWidget:
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(GAP_MD, GAP_MD, GAP_MD, GAP_MD)
        v.setSpacing(GAP_SM)
        self.table = QTableWidget(0, len(self.COLS), self)
        self.table.setHorizontalHeaderLabels(self.COLS)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        header.setDefaultSectionSize(110)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._ctx_menu)
        self.table.setSortingEnabled(True)
        v.addWidget(self.table)
        return container

    def _ctx_menu(self, pos):
        row = self.table.currentRow()
        if row < 0:
            return
        menu = QMenu(self)
        if row not in self.workers:
            act_start = QAction("Start row", self)
            act_start.triggered.connect(lambda: self._start_rows([row]))
            menu.addAction(act_start)
        act_dup = QAction("Duplicate row", self)
        act_dup.triggered.connect(lambda: self.duplicate_row(row))
        menu.addAction(act_dup)
        act_dup_shuffle = QAction("Duplicate with shuffled sources", self)
        act_dup_shuffle.triggered.connect(self.duplicate_with_shuffled_sources)
        menu.addAction(act_dup_shuffle)
        act_up = QAction("Move row up", self)
        act_up.triggered.connect(lambda: self.move_row(row, -1))
        act_down = QAction("Move row down", self)
        act_down.triggered.connect(lambda: self.move_row(row, 1))
        menu.addAction(act_up)
        menu.addAction(act_down)
        if row in self.workers:
            act_stop = QAction("Stop row", self)
            act_stop.triggered.connect(lambda: self.stop_row(row))
            menu.addAction(act_stop)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def duplicate_row(self, r: int):
        self.add_account_row(prefill_from_env=False)
        r2 = self.table.rowCount() - 1
        for c in range(self.table.columnCount()):
            w = self._w(r, c)
            w2 = self._w(r2, c)
            if isinstance(w, QLineEdit) and isinstance(w2, QLineEdit):
                w2.setText(w.text())
            elif isinstance(w, QSpinBox) and isinstance(w2, QSpinBox):
                w2.setValue(w.value())
            elif isinstance(w, QDoubleSpinBox) and isinstance(w2, QDoubleSpinBox):
                w2.setValue(w.value())
            elif isinstance(w, QComboBox) and isinstance(w2, QComboBox) and not isinstance(w, MultiSelectComboBox):
                w2.setCurrentText(w.currentText())
            elif isinstance(w, MultiSelectComboBox) and isinstance(w2, MultiSelectComboBox):
                w2.set_selected(w.selected_items())
            elif isinstance(w, QCheckBox) and isinstance(w2, QCheckBox):
                w2.setChecked(w.isChecked())

    def move_row(self, r: int, delta: int):
        target = r + delta
        if target < 0 or target >= self.table.rowCount():
            return
        for c in range(self.table.columnCount()):
            src = self._w(r, c)
            dst = self._w(target, c)
            if isinstance(src, QLineEdit) and isinstance(dst, QLineEdit):
                src_text, dst_text = src.text(), dst.text()
                src.setText(dst_text)
                dst.setText(src_text)
            elif isinstance(src, QSpinBox) and isinstance(dst, QSpinBox):
                src_val, dst_val = src.value(), dst.value()
                src.setValue(dst_val)
                dst.setValue(src_val)
            elif isinstance(src, QDoubleSpinBox) and isinstance(dst, QDoubleSpinBox):
                src_val, dst_val = src.value(), dst.value()
                src.setValue(dst_val)
                dst.setValue(src_val)
            elif isinstance(src, QComboBox) and isinstance(dst, QComboBox) and not isinstance(src, MultiSelectComboBox):
                src_text, dst_text = src.currentText(), dst.currentText()
                src.setCurrentText(dst_text)
                dst.setCurrentText(src_text)
            elif isinstance(src, MultiSelectComboBox) and isinstance(dst, MultiSelectComboBox):
                src_vals, dst_vals = src.selected_items(), dst.selected_items()
                src.set_selected(dst_vals)
                dst.set_selected(src_vals)
            elif isinstance(src, QCheckBox) and isinstance(dst, QCheckBox):
                src_state, dst_state = src.isChecked(), dst.isChecked()
                src.setChecked(dst_state)
                dst.setChecked(src_state)

    def _w(self, r: int, c: int):
        return self.table.cellWidget(r, c)

    def _reset_widget_style(self, widget):
        base_style = widget.property("__base_stylesheet")
        widget.setStyleSheet(base_style if base_style is not None else "")
        base_tooltip = widget.property("__base_tooltip")
        if base_tooltip is not None:
            widget.setToolTip(base_tooltip)

    def _mark_invalid_widget(self, widget, message: str):
        if widget is None:
            return
        if widget.property("__base_stylesheet") is None:
            widget.setProperty("__base_stylesheet", widget.styleSheet())
        if widget.property("__base_tooltip") is None:
            widget.setProperty("__base_tooltip", widget.toolTip())
        widget.setStyleSheet((widget.property("__base_stylesheet") or "") + "border: 1px solid #ff6b6b;")
        widget.setToolTip(message)

    def _clear_row_validation(self, r: int):
        for c in range(self.table.columnCount()):
            w = self._w(r, c)
            if not w:
                continue
            self._reset_widget_style(w)

    def _validate_row_inputs(self, r: int) -> Tuple[bool, Optional[dict], List[str]]:
        errors: List[str] = []
        session_widget = self._w(r, 1)
        api_id_widget = self._w(r, 2)
        api_hash_widget = self._w(r, 3)
        sources_widget = self._w(r, 4)
        target_widget = self._w(r, 5)
        min_widget = self._w(r, 7)
        max_widget = self._w(r, 8)

        session_name = session_widget.text().strip() if isinstance(session_widget, QLineEdit) else ""
        if not session_name:
            errors.append("Session name is required.")
            self._mark_invalid_widget(session_widget, errors[-1])
        else:
            safe_session = sanitize_session_name(session_name)
            if safe_session != session_name and isinstance(session_widget, QLineEdit):
                session_widget.setText(safe_session)
            session_name = safe_session

        api_id_txt = api_id_widget.text().strip() if isinstance(api_id_widget, QLineEdit) else ""
        api_id: Optional[int] = None
        try:
            api_id = int(api_id_txt)
            if api_id <= 0:
                raise ValueError("API_ID must be positive")
        except Exception:
            errors.append("API_ID must be a positive integer.")
            self._mark_invalid_widget(api_id_widget, errors[-1])

        api_hash = api_hash_widget.text().strip() if isinstance(api_hash_widget, QLineEdit) else ""
        if not api_hash:
            errors.append("API_HASH is required.")
            self._mark_invalid_widget(api_hash_widget, errors[-1])

        sources_raw = sources_widget.text().strip() if isinstance(sources_widget, QLineEdit) else ""
        sources = parse_source_list(sources_raw)
        if not sources:
            errors.append("At least one source is required.")
            self._mark_invalid_widget(sources_widget, errors[-1])

        dst = target_widget.text().strip() if isinstance(target_widget, QLineEdit) else ""
        if not dst:
            errors.append("Target is required.")
            self._mark_invalid_widget(target_widget, errors[-1])

        min_delay = min_widget.value() if isinstance(min_widget, QDoubleSpinBox) else 0.0
        max_delay = max_widget.value() if isinstance(max_widget, QDoubleSpinBox) else 0.0
        min_delay, max_delay = normalize_delay_range(min_delay, max_delay)
        if isinstance(min_widget, QDoubleSpinBox):
            min_widget.setValue(min_delay)
        if isinstance(max_widget, QDoubleSpinBox):
            max_widget.setValue(max_delay)

        if errors:
            return False, None, errors

        return True, {
            "session_name": session_name,
            "api_id": api_id,
            "api_hash": api_hash,
            "sources": sources,
            "dst": dst,
            "min_delay": min_delay,
            "max_delay": max_delay,
        }, errors

    def apply_account_to_rows(self):
        preset = self.cmb_account.currentData()
        if not preset:
            QMessageBox.warning(self, "No preset", "Please select an account preset.")
            return

        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            rows = [0]
            if self.table.rowCount() == 0:
                self.add_account_row(prefill_from_env=False)

        api_id_val, api_hash_val, session_val, env_id_key, env_hash_key = resolve_preset_credentials(preset)
        if not api_id_val or not api_hash_val:
            self._append_log(
                f"Preset '{preset.get('name')}' is missing API_ID/API_HASH env vars; set {env_id_key} and {env_hash_key} (or generic API_ID/API_HASH).",
                level="warn",
            )

        for r in rows:
            if r >= self.table.rowCount():
                continue
            api_id_w = self._w(r, 2)
            api_hash_w = self._w(r, 3)
            session_w = self._w(r, 1)
            if isinstance(api_id_w, QLineEdit):
                api_id_w.setText(str(api_id_val or ""))
            if isinstance(api_hash_w, QLineEdit):
                api_hash_w.setText(api_hash_val or "")
            if isinstance(session_w, QLineEdit):
                session_w.setText(session_val or f"session-{r+1}")
        chosen = preset.get("name", "-")
        self.lbl_account.setText(f"Selected account: {chosen}")
        self.statusBar().showMessage(f"Applied account '{chosen}' to rows {', '.join(str(r+1) for r in rows)}", 4000)
        self._append_log(f"Applied account preset '{chosen}' to rows {', '.join(str(r+1) for r in rows)}")

    def bulk_apply_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if len(rows) < 2:
            QMessageBox.information(self, "Bulk apply", "Select 2+ rows to bulk-apply from the first selection.")
            return
        src_row = rows[0]
        fields_to_copy = [1,2,3,4,5,6,7,8,9,10,11,12,13,14]  # everything except enable/progress
        for r in rows[1:]:
            for c in fields_to_copy:
                src = self._w(src_row, c)
                dst = self._w(r, c)
                if isinstance(src, QLineEdit) and isinstance(dst, QLineEdit):
                    dst.setText(src.text())
                elif isinstance(src, QSpinBox) and isinstance(dst, QSpinBox):
                    dst.setValue(src.value())
                elif isinstance(src, QDoubleSpinBox) and isinstance(dst, QDoubleSpinBox):
                    dst.setValue(src.value())
                elif isinstance(src, QComboBox) and isinstance(dst, QComboBox) and not isinstance(src, MultiSelectComboBox):
                    dst.setCurrentText(src.currentText())
                elif isinstance(src, MultiSelectComboBox) and isinstance(dst, MultiSelectComboBox):
                    dst.set_selected(src.selected_items())
                elif isinstance(src, QCheckBox) and isinstance(dst, QCheckBox):
                    dst.setChecked(src.isChecked())
        self._append_log(f"Bulk applied settings from row {src_row+1} to rows {', '.join(str(r+1) for r in rows[1:])}")

    def filter_rows(self, text: str):
        query = (text or "").lower().strip()
        for r in range(self.table.rowCount()):
            src = self._w(r, 4).text().lower() if isinstance(self._w(r, 4), QLineEdit) else ""
            dst = self._w(r, 5).text().lower() if isinstance(self._w(r, 5), QLineEdit) else ""
            note = self._w(r,14).text().lower() if isinstance(self._w(r,14), QLineEdit) else ""
            show = not query or query in src or query in dst or query in note
            self.table.setRowHidden(r, not show)

    def toggle_theme(self, state):
        self.dark_mode = not bool(state)
        self._apply_theme(self.dark_mode)
        self._append_log("Theme toggled.")

    def pause_all(self):
        self.global_pause.set()
        self._append_log("Global pause enabled for all workers.", level="warn")

    def resume_all(self):
        self.global_pause.clear()
        self._append_log("Global pause cleared; workers resuming.")

    def migrate_storage(self):
        sources = [("sessions", SESS_DIR), ("resume", RESUME_DIR)]
        moved_any = False
        for folder, target in sources:
            if not os.path.exists(folder) or os.path.abspath(folder) == os.path.abspath(target):
                continue
            try:
                shutil.copytree(folder, target, dirs_exist_ok=True)
                moved_any = True
            except Exception as e:
                QMessageBox.warning(self, "Migration failed", f"Failed to copy {folder} to {target}: {e}")
        if moved_any:
            self._append_log("Copied ./sessions and ./resume into secure storage directory.")
        else:
            self._append_log("No migration performed (nothing to move or already in place).")

    def open_credentials_panel(self):
        from PyQt6.QtWidgets import QDialog, QFormLayout, QDialogButtonBox
        dlg = QDialog(self)
        dlg.setWindowTitle("Credentials")
        form = QFormLayout(dlg)
        fields = {}
        generic_api_id = QLineEdit(os.getenv("API_ID", ""))
        generic_api_hash = QLineEdit(os.getenv("API_HASH", ""))
        generic_api_hash.setEchoMode(QLineEdit.EchoMode.Password)
        fields["API_ID"] = generic_api_id
        fields["API_HASH"] = generic_api_hash
        form.addRow("API_ID", generic_api_id)
        form.addRow("API_HASH", generic_api_hash)
        preset_fields = []
        for preset in ACCOUNT_PRESETS:
            name = preset.get("name", "preset")
            api_id_key = resolve_preset_credentials(preset)[3]
            api_hash_key = resolve_preset_credentials(preset)[4]
            api_id_edit = QLineEdit(os.getenv(api_id_key, ""))
            api_hash_edit = QLineEdit(os.getenv(api_hash_key, ""))
            api_hash_edit.setEchoMode(QLineEdit.EchoMode.Password)
            fields[api_id_key] = api_id_edit
            fields[api_hash_key] = api_hash_edit
            form.addRow(f"{name} {api_id_key}", api_id_edit)
            form.addRow(f"{name} {api_hash_key}", api_hash_edit)
            preset_fields.append((api_id_key, api_id_edit, api_hash_key, api_hash_edit))

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, parent=dlg)
        form.addRow(btns)

        def on_save():
            for key, widget in fields.items():
                val = widget.text().strip()
                if val:
                    os.environ[key] = val
            dlg.accept()

        btns.accepted.connect(on_save)
        btns.rejected.connect(dlg.reject)
        dlg.exec()

    def apply_table_preset(self):
        presets = {
            "Default": {"batch": 100, "min_delay": 0.8, "max_delay": 2.5, "anonymize": True},
            "Minimal": {"batch": 50, "min_delay": 0.5, "max_delay": 1.5, "anonymize": True},
            "Flood-safe": {"batch": 30, "min_delay": 2.0, "max_delay": 5.0, "anonymize": True},
        }
        choice = self.cmb_preset_mode.currentText()
        preset = presets.get(choice)
        if not preset:
            return
        rows = sorted({i.row() for i in self.table.selectedIndexes()}) or [0]
        for r in rows:
            if r >= self.table.rowCount():
                continue
            if isinstance(self._w(r, 6), QSpinBox):
                self._w(r, 6).setValue(preset["batch"])
            if isinstance(self._w(r, 7), QDoubleSpinBox):
                self._w(r, 7).setValue(preset["min_delay"])
            if isinstance(self._w(r, 8), QDoubleSpinBox):
                self._w(r, 8).setValue(preset["max_delay"])
            if isinstance(self._w(r, 13), QCheckBox):
                self._w(r, 13).setChecked(preset["anonymize"])
        self._append_log(f"Applied table preset '{choice}' to rows {', '.join(str(r+1) for r in rows)}")

    def _setup_shortcuts(self):
        shortcuts = [
            (QKeySequence("Ctrl+N"), self.add_account_row),
            (QKeySequence("Ctrl+S"), self.save_accounts_file),
            (QKeySequence("Ctrl+O"), self.load_accounts_file),
            (QKeySequence("Ctrl+F"), lambda: self.search_box.setFocus()),
            (QKeySequence("Ctrl+Enter"), self.start_selected),
            (QKeySequence("Ctrl+Shift+S"), self.stop_selected),
            (QKeySequence("Ctrl+P"), self.command_palette),
        ]
        for key, fn in shortcuts:
            sc = QShortcut(key, self)
            sc.activated.connect(fn)

    def command_palette(self):
        actions = {
            "Add row": self.add_account_row,
            "Start selected": self.start_selected,
            "Stop selected": self.stop_selected,
            "Apply account preset": self.apply_account_to_rows,
            "Bulk apply first selected": self.bulk_apply_selected,
            "Duplicate with shuffled sources": self.duplicate_with_shuffled_sources,
            "Reset resume for selected": self.reset_resume_selected,
            "Clear stale resume files": self.clear_stale_resume,
            "Validate creds": self.validate_creds,
            "Move storage to secure dir": self.migrate_storage,
            "Save accounts": self.save_accounts_file,
            "Load accounts": self.load_accounts_file,
            "Toggle theme": lambda: self.theme_toggle.setChecked(not self.theme_toggle.isChecked()),
            "Filter focus": lambda: self.search_box.setFocus(),
        }
        choice, ok = QInputDialog.getItem(self, "Command palette", "Action:", list(actions.keys()), 0, False)
        if ok and choice in actions:
            actions[choice]()

    def duplicate_with_shuffled_sources(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            QMessageBox.information(self, "Duplicate shuffled", "Select a row to duplicate.")
            return
        src_row = rows[0]
        src_widget = self._w(src_row, 4)
        sources = parse_source_list(src_widget.text() if isinstance(src_widget, QLineEdit) else "")
        random.shuffle(sources)
        self.add_account_row(prefill_from_env=False)
        r2 = self.table.rowCount() - 1
        for c in range(self.table.columnCount()):
            w = self._w(src_row, c)
            w2 = self._w(r2, c)
            if isinstance(w, QLineEdit) and isinstance(w2, QLineEdit):
                w2.setText(w.text())
            elif isinstance(w, QSpinBox) and isinstance(w2, QSpinBox):
                w2.setValue(w.value())
            elif isinstance(w, QDoubleSpinBox) and isinstance(w2, QDoubleSpinBox):
                w2.setValue(w.value())
            elif isinstance(w, QComboBox) and isinstance(w2, QComboBox) and not isinstance(w, MultiSelectComboBox):
                w2.setCurrentText(w.currentText())
            elif isinstance(w, MultiSelectComboBox) and isinstance(w2, MultiSelectComboBox):
                w2.set_selected(w.selected_items())
            elif isinstance(w, QCheckBox) and isinstance(w2, QCheckBox):
                w2.setChecked(w.isChecked())
        if isinstance(self._w(r2, 4), QLineEdit):
            self._w(r2, 4).setText(", ".join(sources))
        self._append_log(f"Duplicated row {src_row+1} with shuffled sources into row {r2+1}")

    def reset_resume_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            QMessageBox.information(self, "Reset resume", "Select at least one row.")
            return
        removed = 0
        for r in rows:
            session_name = sanitize_session_name(self._w(r,1).text()) if isinstance(self._w(r,1), QLineEdit) else ""
            dst = self._w(r,5).text().strip() if isinstance(self._w(r,5), QLineEdit) else ""
            sources = parse_source_list(self._w(r,4).text()) if isinstance(self._w(r,4), QLineEdit) else []
            for src in sources:
                key = build_route_key(src, dst, session_name)
                path = os.path.join(RESUME_DIR, f"{key}.json")
                if os.path.exists(path):
                    try:
                        os.remove(path)
                        removed += 1
                    except Exception:
                        pass
        QMessageBox.information(self, "Resume reset", f"Removed {removed} resume file(s).")
        self._append_log(f"Removed {removed} resume files for selected rows.")

    def validate_creds(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}) or [0]
        issues = []
        for r in rows:
            api_id_txt = self._w(r,2).text().strip() if isinstance(self._w(r,2), QLineEdit) else ""
            api_hash_txt = self._w(r,3).text().strip() if isinstance(self._w(r,3), QLineEdit) else ""
            try:
                val = int(api_id_txt)
                if val <= 0:
                    raise ValueError()
            except Exception:
                issues.append(f"Row {r+1}: API_ID invalid")
            if not api_hash_txt:
                issues.append(f"Row {r+1}: API_HASH missing")
        if issues:
            QMessageBox.warning(self, "Credential check", "\n".join(issues))
        else:
            QMessageBox.information(self, "Credential check", "API_ID/API_HASH present and formatted.")

    def clear_stale_resume(self):
        removed = 0
        now = time.time()
        stale_days = int(os.getenv("RESUME_STALE_DAYS", "30"))
        max_age = stale_days * 86400
        for p in Path(RESUME_DIR).glob("*.json"):
            try:
                if now - p.stat().st_mtime > max_age:
                    p.unlink()
                    removed += 1
            except Exception:
                continue
        QMessageBox.information(self, "Clear stale resume", f"Removed {removed} stale resume file(s).")
        self._append_log(f"Removed {removed} stale resume files older than {stale_days}d.")

    def add_account_row(self, prefill_from_env: bool = False):
        r = self.table.rowCount()
        self.table.insertRow(r)

        def _add(col, widget):
            self.table.setCellWidget(r, col, widget)
            return widget

        chk = QCheckBox(); chk.setChecked(True)
        _add(0, chk)

        default_session = os.getenv("SESSION_NAME", "forward-session") if prefill_from_env else f"session-{r+1}"
        session = QLineEdit(default_session)
        session.setToolTip("Session name. File stored in ./sessions/")
        _add(1, session)

        api_id = QLineEdit(os.getenv("API_ID", "") if prefill_from_env else "")
        api_id.setPlaceholderText("API_ID")
        api_hash = QLineEdit(os.getenv("API_HASH", "") if prefill_from_env else "")
        api_hash.setPlaceholderText("API_HASH")
        api_hash.setEchoMode(QLineEdit.EchoMode.Password)
        _add(2, api_id); _add(3, api_hash)

        source = QLineEdit(); source.setPlaceholderText("@source or -100… (comma or newline separated)")
        target = QLineEdit(); target.setPlaceholderText("@target or -100…")
        _add(4, source); _add(5, target)

        batch = QSpinBox(); batch.setRange(1, 10000); batch.setValue(100); batch.setToolTip("Batch size for non-anonymized forwards")
        min_d = QDoubleSpinBox(); min_d.setRange(0.1, 10.0); min_d.setSingleStep(0.1); min_d.setValue(0.8); min_d.setToolTip("Minimum delay between batches/messages")
        max_d = QDoubleSpinBox(); max_d.setRange(0.1, 10.0); max_d.setSingleStep(0.1); max_d.setValue(2.5); max_d.setToolTip("Maximum delay between batches/messages")
        _add(6, batch); _add(7, min_d); _add(8, max_d)

        order = QComboBox(); order.addItems(["oldest", "newest"])
        _add(9, order)

        types = MultiSelectComboBox(["text","photo","image","document","video","audio","webpage"])
        _add(10, types)

        resume = QCheckBox(); resume.setChecked(True); resume.setToolTip("Resume from last forwarded message for this route")
        dry = QCheckBox()
        anon = QCheckBox(); anon.setChecked(True); anon.setToolTip("Anonymize: resend content instead of forwarding")
        _add(11, resume); _add(12, dry); _add(13, anon)

        notes = QLineEdit(); notes.setPlaceholderText("Notes")
        _add(14, notes)

        prog = QProgressBar(); prog.setRange(0, 0); prog.setFormat("Idle")
        _add(15, prog)

    def remove_selected_rows(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            if r in self.workers:
                QMessageBox.warning(self, "Busy", f"Row {r+1} is running. Stop it first.")
                continue
            self.table.removeRow(r)

    def _build_log_area(self) -> QWidget:
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(GAP_MD, GAP_MD, GAP_MD, GAP_MD)

        tools = QHBoxLayout()
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(GAP_SM)
        btn_copy = QPushButton("Copy logs")
        btn_copy.clicked.connect(self.copy_logs)
        btn_clear = QPushButton("Clear log")
        btn_clear.clicked.connect(lambda: self.log.clear())
        btn_jump_error = QPushButton("Jump to last error")
        btn_jump_error.clicked.connect(self.jump_to_last_error)
        btn_copy_summary = QPushButton("Copy last summary")
        btn_copy_summary.clicked.connect(self.copy_last_summary)
        self.log_filter = QComboBox()
        self.log_filter.addItems(["all", "info", "warn", "error"])
        self.log_filter.currentTextChanged.connect(lambda v: setattr(self, "log_filter_level", v))
        self.log_filter_level = "all"
        btn_toggle_history = QPushButton("Toggle history")
        btn_toggle_history.clicked.connect(self.toggle_history)
        for b in [btn_copy, btn_clear, btn_jump_error, btn_copy_summary, btn_toggle_history]:
            self._style_button(b)
        self.log_filter.setMinimumWidth(90)
        tools.addWidget(btn_copy)
        tools.addWidget(btn_clear)
        tools.addWidget(btn_jump_error)
        tools.addWidget(btn_copy_summary)
        tools.addWidget(QLabel("Filter:"))
        tools.addWidget(self.log_filter)
        tools.addWidget(btn_toggle_history)
        tools.addStretch(1)
        v.addLayout(tools)

        self.log = QTextEdit(self)
        self.log.setReadOnly(True)
        self.log.setAcceptRichText(True)
        self.log.setMinimumHeight(220)
        v.addWidget(self.log)
        self.history_view = QTextEdit(self)
        self.history_view.setReadOnly(True)
        self.history_view.setVisible(False)
        v.addWidget(self.history_view)
        return container

    def _append_log(self, txt: str, level: str = "info"):
        colors = {"info": "#ffffff" if self.dark_mode else "#000000", "warn": "#ffb347", "error": "#ff6b6b"}
        color = colors.get(level, colors["info"])
        lf = getattr(self, "log_filter_level", "all")
        if lf != "all" and lf != level:
            return
        safe = txt.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.log.append(f"<span style='color:{color}'>{safe}</span>")
        if level == "error":
            cursor = self.log.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self._last_error_cursor = cursor

    def jump_to_last_error(self):
        cursor = getattr(self, "_last_error_cursor", None)
        if cursor:
            self.log.setTextCursor(cursor)
            self.log.ensureCursorVisible()
        else:
            self.statusBar().showMessage("No error lines in log yet.", 3000)

    def copy_logs(self):
        QApplication.clipboard().setText(self.log.toPlainText())
        self.statusBar().showMessage("Logs copied to clipboard", 3000)

    def copy_last_summary(self):
        if not self._last_summary:
            self.statusBar().showMessage("No summary yet.", 2000)
            return
        QApplication.clipboard().setText(self._last_summary)
        self.statusBar().showMessage("Last run summary copied.", 2000)

    def toggle_history(self):
        if self.history_view:
            self.history_view.setVisible(not self.history_view.isVisible())

    def save_accounts_file(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save accounts file", "accounts.json", "JSON (*.json)")
        if not path:
            return
        out = []
        for r in range(self.table.rowCount()):
            wt = self._w(r, 10)
            sel_types = wt.selected_items() if isinstance(wt, MultiSelectComboBox) else []
            out.append({
                "enabled": self._w(r,0).isChecked(),
                "session_name": self._w(r,1).text().strip(),
                "api_id": self._w(r,2).text().strip(),
                # api_hash intentionally omitted to avoid leaking secrets
                "sources": parse_source_list(self._w(r,4).text().strip()),
                "target": self._w(r,5).text().strip(),
                "batch": self._w(r,6).value(),
                "min_delay": self._w(r,7).value(),
                "max_delay": self._w(r,8).value(),
                "order": self._w(r,9).currentText(),
                "types": sel_types,
                "resume": self._w(r,11).isChecked(),
                "dry_run": self._w(r,12).isChecked(),
                "anonymize": self._w(r,13).isChecked(),
                "notes": self._w(r,14).text().strip() if isinstance(self._w(r,14), QLineEdit) else "",
            })
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
        self._append_log(f"Saved {len(out)} rows to {path} (api_hash not stored; set via env or re-enter).")

    def load_accounts_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open accounts file", "", "JSON (*.json)")
        if not path:
            return
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.table.setRowCount(0)
        for acc in data:
            self.add_account_row()
            r = self.table.rowCount() - 1
            self._w(r,0).setChecked(bool(acc.get("enabled", True)))
            self._w(r,1).setText(acc.get("session_name", f"session-{r+1}"))
            self._w(r,2).setText(str(acc.get("api_id","")))
            # api_hash intentionally not loaded from file; expect env/prompt
            self._w(r,4).setText(", ".join(acc.get("sources", [])))
            self._w(r,5).setText(str(acc.get("target","")))
            self._w(r,6).setValue(acc.get("batch",100))
            self._w(r,7).setValue(acc.get("min_delay",0.8))
            self._w(r,8).setValue(acc.get("max_delay",2.5))
            self._w(r,9).setCurrentText(acc.get("order","oldest"))
            wt = self._w(r,10)
            if isinstance(wt, MultiSelectComboBox):
                wt.set_selected(acc.get("types", []))
            self._w(r,11).setChecked(acc.get("resume",True))
            self._w(r,12).setChecked(acc.get("dry_run",False))
            self._w(r,13).setChecked(acc.get("anonymize",True))
            if isinstance(self._w(r,14), QLineEdit):
                self._w(r,14).setText(acc.get("notes",""))
        self._append_log(f"Loaded {len(data)} rows from {path} (api_hash values must be supplied via env or input).")

    def _start_rows(self, rows: List[int]):
        running = 0
        invalid_rows: List[Tuple[int, str]] = []
        for r in rows:
            if r < 0 or r >= self.table.rowCount():
                continue
            if not self._w(r,0).isChecked():
                continue
            if r in self.workers:
                QMessageBox.warning(self, "Busy", f"Row {r+1} is already running.")
                continue
            if len(self.workers) >= self.max_concurrent:
                if r not in self.pending_rows:
                    self.pending_rows.append(r)
                continue
            self._clear_row_validation(r)
            try:
                valid, data, errors = self._validate_row_inputs(r)
                if not valid or not data:
                    invalid_rows.append((r + 1, errors[0] if errors else "Invalid input"))
                    continue

                session_name = data["session_name"]
                api_id = data["api_id"]
                api_hash = data["api_hash"]
                sources = data["sources"]
                dst = data["dst"]
                min_delay = data["min_delay"]
                max_delay = data["max_delay"]

                wt = self._w(r,10)
                types_list = wt.selected_items() if isinstance(wt, MultiSelectComboBox) else []
                types_set = normalize_types(types_list)

                cfg = ForwardConfig(
                    batch_size=self._w(r,6).value(),
                    min_delay=min_delay,
                    max_delay=max_delay,
                    anonymize=self._w(r,13).isChecked(),
                    dry_run=self._w(r,12).isChecked(),
                    start_id=None, end_id=None, since=None, until=None,
                    order=self._w(r,9).currentText(),
                    max_messages=None,
                    types=types_set,
                    stop_event=threading.Event(),
                    pause_event=self.global_pause,
                    resume_state={},
                    resume_path=None,
                    adaptive=True,
                    dedup_enabled=True,
                    dedup_ttl=self.dedup_ttl_seconds,
                )

                ctx = RowContext(
                    cfg=cfg,
                    api_id=api_id,
                    api_hash=api_hash,
                    session_name=session_name,
                    sources=sources,
                    target=dst,
                    resume_enabled=self._w(r,11).isChecked(),
                    dedup_ttl=self.dedup_ttl_seconds,
                    pause_event=self.global_pause,
                )

                w = AccountWorker(r, ctx)
                w.progressed.connect(self._on_progress)
                w.finished.connect(self._on_finished)
                w.failed.connect(self._on_failed)
                w.logline.connect(self._on_logline)
                self.workers[r] = w
                self.row_cfgs[r] = cfg

                pb = self._w(r,15)
                if isinstance(pb, QProgressBar):
                    pb.setRange(0, 0)
                    pb.setFormat("Working…")

                w.start()
                self.row_start_time[r] = time.time()
                running += 1
            except Exception as e:
                QMessageBox.critical(self, "Invalid row", f"Row {r+1}: {e}")
        if invalid_rows:
            detail = "\n".join(f"Row {idx}: {msg}" for idx, msg in invalid_rows)
            QMessageBox.warning(self, "Invalid rows", f"Fix highlighted rows:\n{detail}")
        if running:
            self._append_log(f"Started {running} account(s).")
        self._update_status_summary()

    def start_all(self):
        rows = [r for r in range(self.table.rowCount()) if self._w(r,0).isChecked()]
        self._start_rows(rows)

    def start_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            QMessageBox.information(self, "No selection", "Select at least one row to start.")
            return
        self._start_rows(rows)

    def stop_row(self, r: int, quiet: bool = False) -> bool:
        cfg = self.row_cfgs.get(r)
        if not cfg:
            if not quiet:
                QMessageBox.information(self, "Not running", f"Row {r+1} is not currently running.")
            return False
        cfg.stop_event.set()
        pb = self._w(r, 15)
        if isinstance(pb, QProgressBar):
            pb.setRange(0, 0)
            pb.setFormat("Stopping...")
        if not quiet:
            self._append_log(f"Stop requested for row {r+1}.")
        self._update_status_summary()
        return True

    def stop_all(self):
        stop_count = 0
        for r in list(self.row_cfgs.keys()):
            if self.stop_row(r, quiet=True):
                stop_count += 1
        self._append_log(f"Stop requested for {stop_count} account(s).")
        self._update_status_summary()

    def stop_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            QMessageBox.information(self, "No selection", "Select at least one row to stop.")
            return
        stop_count = 0
        for r in rows:
            if self.stop_row(r, quiet=True):
                stop_count += 1
        self._append_log(f"Stop requested for {stop_count} selected account(s).")
        self._update_status_summary()

    # Worker slots
    def _on_progress(self, row: int, forwarded: int, total: int, src, throttle: float = 1.0):
        pb = self._w(row, 15)
        if not isinstance(pb, QProgressBar):
            return
        if total and total > 0:
            if pb.maximum() == 0:
                pb.setRange(0, total)
            pb.setValue(min(forwarded, total))
            eta_txt = ""
            start_ts = self.row_start_time.get(row)
            if start_ts and forwarded > 0:
                elapsed = max(0.001, time.time() - start_ts)
                rate = forwarded / elapsed
                remaining = max(total - forwarded, 0)
                if rate > 0:
                    eta = remaining / rate
                    eta_txt = f" ETA {eta:0.0f}s"
            pb.setFormat(f"{forwarded}/{total} ({src}) x{throttle:.2f}{eta_txt}")
        else:
            pb.setRange(0, 0)
            pb.setFormat(f"Forwarded: {forwarded} ({src}) x{throttle:.2f}")

    def _on_finished(self, row: int, payload: dict):
        self.completed_runs += 1
        pb = self._w(row, 15)
        if isinstance(pb, QProgressBar):
            pb.setRange(0, 1)
            pb.setValue(1)
            pb.setFormat("Done")

        totals = payload.get("totals", {})
        lines = []
        grand = {"forwarded": 0, "skipped": 0, "errors": 0}
        for src, t in totals.items():
            lines.append(f"{src}: fwd={t.get('forwarded',0)} skip={t.get('skipped',0)} err={t.get('errors',0)}")
            grand["forwarded"] += t.get("forwarded", 0)
            grand["skipped"] += t.get("skipped", 0)
            grand["errors"] += t.get("errors", 0)
        msg = "\n".join(lines) or "No work performed."
        msg += f"\n\nTotal: fwd={grand['forwarded']} skip={grand['skipped']} err={grand['errors']}"
        QMessageBox.information(self, f"Row {row+1} completed", msg)
        self._append_log(f"Row {row+1} summary:\n{msg}")
        self._last_summary = msg
        if self.history_view:
            self.history_view.append(f"[Row {row+1}] {msg}\n---")
        self._cleanup_row(row)

    def _on_failed(self, row: int, msg: str):
        pb = self._w(row, 15)
        if isinstance(pb, QProgressBar):
            pb.setRange(0, 1)
            pb.setValue(0)
            pb.setFormat("Error")
            pb.setStyleSheet("QProgressBar::chunk { background-color: #ff4d4f; }")
        QMessageBox.critical(self, f"Row {row+1} error", msg)
        self._append_log(f"[Row {row+1}] ERROR: {msg}", level="error")
        self._cleanup_row(row)

    def _on_logline(self, row: int, text: str):
        self._append_log(f"[Row {row+1}] {text}")

    def _cleanup_row(self, row: int):
        self.workers.pop(row, None)
        self.row_cfgs.pop(row, None)
        self.row_start_time.pop(row, None)
        self._update_status_summary()
        if self.pending_rows and len(self.workers) < self.max_concurrent:
            nxt = self.pending_rows.pop(0)
            self._start_rows([nxt])

    # block closing while jobs run
    def closeEvent(self, event):
        if self.workers:
            QMessageBox.warning(self, "Busy", "Jobs are still running. Stop them first.")
            event.ignore()
            return
        self._save_layout()
        super().closeEvent(event)

    def _save_layout(self):
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("windowState", self.saveState())
        self.settings.setValue("splitter", self.splitter.saveState() if hasattr(self, "splitter") else None)
        self.settings.setValue("theme", "light" if self.theme_toggle.isChecked() else "dark")
        if self.table:
            widths = [self.table.columnWidth(i) for i in range(self.table.columnCount())]
            self.settings.setValue("colwidths", widths)

    def _restore_layout(self):
        geo = self.settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        st = self.settings.value("windowState")
        if st:
            self.restoreState(st)
        if hasattr(self, "splitter"):
            sp = self.settings.value("splitter")
            if sp:
                self.splitter.restoreState(sp)
        theme = self.settings.value("theme")
        if theme == "light":
            self.theme_toggle.setChecked(True)
        widths = self.settings.value("colwidths")
        if widths and self.table:
            for i, w in enumerate(widths):
                try:
                    self.table.setColumnWidth(i, int(w))
                except Exception:
                    pass


def install_excepthook(app: QApplication):
    def handler(exc_type, exc_value, exc_tb):
        tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        print(tb, file=sys.stderr)
        QMessageBox.critical(None, "Unhandled error", f"{exc_type.__name__}: {exc_value}")
    sys.excepthook = handler


if __name__ == "__main__":
    load_dotenv()
    app = QApplication(sys.argv)
    install_excepthook(app)
    app.setStyle("Fusion")
    win = ForwarderGUI()
    win.show()
    sys.exit(app.exec())
