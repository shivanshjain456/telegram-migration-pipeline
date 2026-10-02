# -*- coding: utf-8 -*-
"""
Tests for crash-safe checkpointing and resumption state handling.
Validates atomic persistence, corrupted file recovery, and route key construction.
"""
import json
import os
import shutil
import tempfile
import time
import unittest

from utils import (
    build_route_key,
    read_resume,
    write_resume,
    resolve_resume_state,
    sanitize_session_name,
)


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="tg_test_resume_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_build_route_key_deterministic(self):
        k1 = build_route_key("@source_channel", "@dest_channel", "acc1")
        k2 = build_route_key("@source_channel", "@dest_channel", "acc1")
        self.assertEqual(k1, k2)
        self.assertIn("source_channel", k1)
        self.assertIn("dest_channel", k1)

    def test_build_route_key_clamps_length(self):
        long_src = "a" * 150
        long_dst = "b" * 150
        key = build_route_key(long_src, long_dst, "session1")
        self.assertLessEqual(len(key), 80)

    def test_write_and_read_resume_roundtrip(self):
        path = os.path.join(self.test_dir, "route.json")
        write_resume(path, last_id=10452)
        state = read_resume(path)
        self.assertEqual(state.get("last_id"), 10452)

    def test_read_resume_nonexistent_returns_empty(self):
        path = os.path.join(self.test_dir, "missing.json")
        self.assertEqual(read_resume(path), {})

    def test_read_resume_corrupted_json_recovers_gracefully(self):
        path = os.path.join(self.test_dir, "corrupt.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{invalid json content---")
        state = read_resume(path)
        self.assertEqual(state, {})

    def test_resolve_resume_state_disabled(self):
        state, path, stale = resolve_resume_state("test_route", enabled=False, root_dir=self.test_dir)
        self.assertEqual(state, {})
        self.assertIsNone(path)
        self.assertFalse(stale)

    def test_resolve_resume_state_enabled_fresh(self):
        route_key = "test_route_active"
        path = os.path.join(self.test_dir, f"{route_key}.json")
        write_resume(path, last_id=999)

        state, resolved_path, stale = resolve_resume_state(route_key, enabled=True, root_dir=self.test_dir)
        self.assertEqual(state.get("last_id"), 999)
        self.assertEqual(resolved_path, path)
        self.assertFalse(stale)

    def test_sanitize_session_name_path_traversal_protection(self):
        dirty = "../../etc/passwd"
        cleaned = sanitize_session_name(dirty)
        self.assertNotIn("/", cleaned)
        self.assertNotIn("\\", cleaned)
        self.assertNotIn("..", cleaned)


if __name__ == "__main__":
    unittest.main()
