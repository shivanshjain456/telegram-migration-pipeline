import unittest

from utils import (
    parse_source_list,
    merge_sources,
    normalize_delay_range,
    build_route_key,
    normalize_types,
    match_type,
)
from unittest.mock import patch


class UtilsTests(unittest.TestCase):
    def test_parse_source_list_dedupes_and_strips(self):
        raw = " @foo ,@bar\n;@foo;; @baz "
        self.assertEqual(parse_source_list(raw), ["@foo", "@bar", "@baz"])

    def test_merge_sources_preserves_order(self):
        merged = merge_sources(["@a", "@b"], ["@b", "@c"], [])
        self.assertEqual(merged, ["@a", "@b", "@c"])

    def test_normalize_delay_range_clamps(self):
        min_d, max_d = normalize_delay_range(-1.0, 0.5)
        self.assertEqual(min_d, 0.0)
        self.assertEqual(max_d, 0.5)
        min_d, max_d = normalize_delay_range(2.0, 1.0)
        self.assertEqual((min_d, max_d), (2.0, 2.0))

    def test_build_route_key_is_shortened(self):
        very_long = "@" + "a" * 200
        key = build_route_key(very_long, very_long, session_name="sess")
        self.assertLess(len(key), 90)
        self.assertTrue(key.startswith("sess"))
        self.assertGreaterEqual(key.count("__"), 2)

    def test_normalize_types_maps_and_discards(self):
        self.assertIsNone(normalize_types([]))
        self.assertIsNone(normalize_types(None))
        self.assertEqual(normalize_types(["Images", "Docs", "unknown"]), {"image", "document"})

    def test_match_type_respects_allowed(self):
        dummy = object()
        with patch("utils.detect_type", return_value="document"):
            self.assertTrue(match_type(dummy, {"document", "video"}))
        with patch("utils.detect_type", return_value="photo"):
            self.assertTrue(match_type(dummy, {"image"}))
        with patch("utils.detect_type", return_value="audio"):
            self.assertFalse(match_type(dummy, {"video"}))


if __name__ == "__main__":
    unittest.main()
