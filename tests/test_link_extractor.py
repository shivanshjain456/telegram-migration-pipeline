# -*- coding: utf-8 -*-
"""
Tests for LinkExtractor and data export functionality.
Validates extraction from raw text, entities, buttons, webpage previews, and exports.
"""
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from telethon.tl.types import MessageEntityTextUrl, MessageMediaWebPage, WebPage

from link_extractor import (
    LinkExtractor,
    LinkRecord,
    export_links_to_csv,
    export_links_to_json,
    export_links_to_excel,
)


class LinkExtractorTests(unittest.TestCase):
    def setUp(self):
        self.extractor = LinkExtractor()
        self.test_dir = tempfile.mkdtemp(prefix="tg_test_links_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_normalize_valid_tme_url(self):
        self.assertEqual(
            self.extractor.normalize("https://t.me/durov"),
            "https://t.me/durov",
        )
        self.assertEqual(
            self.extractor.normalize("http://telegram.me/joinchat/AAAAAF"),
            "https://telegram.me/joinchat/AAAAAF",
        )

    def test_normalize_handle_conversion(self):
        self.assertEqual(
            self.extractor.normalize("@telegram"),
            "https://t.me/telegram",
        )
        # Short invalid handle should be rejected
        self.assertIsNone(self.extractor.normalize("@abc"))

    def test_normalize_rejects_external_domains(self):
        self.assertIsNone(self.extractor.normalize("https://google.com/search"))
        self.assertIsNone(self.extractor.normalize("https://phishing-site.example/t.me"))

    def test_extract_from_text(self):
        sample = (
            "Check out the official channel at https://t.me/telegram and also "
            "follow @security_news for updates! Also see t.me/durov."
        )
        links = self.extractor.extract_from_text(sample)
        self.assertIn("https://t.me/telegram", links)
        self.assertIn("https://t.me/security_news", links)
        self.assertIn("https://t.me/durov", links)

    def test_extract_from_mock_message(self):
        btn = SimpleNamespace(url="https://t.me/support_bot")
        entity = MessageEntityTextUrl(offset=0, length=5, url="https://t.me/hidden_link")
        webpage = WebPage(
            id=12345,
            url="https://t.me/preview_channel",
            display_url="t.me/preview_channel",
            hash=0,
        )
        media = MessageMediaWebPage(webpage=webpage)

        msg = SimpleNamespace(
            message="Join us on @community_hub!",
            entities=[entity],
            buttons=[[btn]],
            media=media,
        )

        extracted = self.extractor.extract_from_message(msg)
        self.assertIn("https://t.me/community_hub", extracted)
        self.assertIn("https://t.me/support_bot", extracted)
        self.assertIn("https://t.me/hidden_link", extracted)
        self.assertIn("https://t.me/preview_channel", extracted)

    def test_export_json_and_csv_roundtrip(self):
        records = [
            LinkRecord(
                link="https://t.me/channel_alpha",
                dialog_title="Alpha Channel",
                dialog_type="Channel",
                message_id=101,
                message_date="2026-06-01T12:00:00Z",
                snippet="Sample announcement text",
            ),
            LinkRecord(
                link="https://t.me/channel_beta",
                dialog_title="Beta Group",
                dialog_type="Group",
                message_id=102,
                message_date="2026-06-01T12:05:00Z",
                snippet="Discussion link",
            ),
        ]

        json_path = os.path.join(self.test_dir, "export.json")
        export_links_to_json(records, json_path)
        self.assertTrue(os.path.exists(json_path))
        self.assertGreater(os.path.getsize(json_path), 50)

        csv_path = os.path.join(self.test_dir, "export.csv")
        export_links_to_csv(records, csv_path)
        self.assertTrue(os.path.exists(csv_path))
        self.assertGreater(os.path.getsize(csv_path), 50)

        excel_path = os.path.join(self.test_dir, "export.xlsx")
        has_excel = export_links_to_excel(records, excel_path)
        if has_excel:
            self.assertTrue(os.path.exists(excel_path))
        else:
            self.assertTrue(os.path.exists(os.path.join(self.test_dir, "export.csv")))


if __name__ == "__main__":
    unittest.main()
