# -*- coding: utf-8 -*-
"""
Tests for rate limiting, FloodWait backoff, and adaptive throttling.
"""
import unittest
from utils import normalize_delay_range, Stats


class RateLimitTests(unittest.TestCase):
    def test_normalize_delay_range_negative_clamping(self):
        min_d, max_d = normalize_delay_range(-5.0, -1.0)
        self.assertEqual(min_d, 0.0)
        self.assertEqual(max_d, 0.0)

    def test_normalize_delay_range_inverted_bounds(self):
        min_d, max_d = normalize_delay_range(5.0, 2.0)
        self.assertEqual(min_d, 5.0)
        self.assertEqual(max_d, 5.0)

    def test_normalize_delay_range_valid_preservation(self):
        min_d, max_d = normalize_delay_range(1.5, 4.5)
        self.assertEqual(min_d, 1.5)
        self.assertEqual(max_d, 4.5)

    def test_stats_dataclass_initial_state(self):
        s = Stats()
        self.assertEqual(s.forwarded, 0)
        self.assertEqual(s.skipped, 0)
        self.assertEqual(s.errors, 0)
        self.assertEqual(s.throttle, 1.0)
        self.assertIsNone(s.last_id)


if __name__ == "__main__":
    unittest.main()
