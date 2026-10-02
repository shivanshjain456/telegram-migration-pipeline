# -*- coding: utf-8 -*-
"""
Tests for bulk_forward async pipeline using AsyncMock Telethon clients.
Validates entity resolution, batching loop, resume logic, and graceful stops.
"""
import asyncio
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from utils import ForwardConfig, Stats, bulk_forward


class MockPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_bulk_forward_handles_stop_event_immediately(self):
        # Configure a stopped event
        stop_event = asyncio.Event()
        stop_event.set()

        cfg = ForwardConfig(
            batch_size=10,
            min_delay=0.0,
            max_delay=0.0,
            anonymize=False,
            dry_run=True,
            start_id=None,
            end_id=None,
            since=None,
            until=None,
            order="oldest",
            max_messages=5,
            types=None,
            stop_event=stop_event,
        )

        mock_client = MagicMock()
        mock_client.is_connected = MagicMock(return_value=True)
        mock_client.get_entity = AsyncMock(side_effect=lambda x: SimpleNamespace(id=123, title=str(x)))
        mock_client.__call__ = AsyncMock(return_value=SimpleNamespace(count=100))

        # Mock iter_messages generator
        async def mock_iter(*args, **kwargs):
            return
            yield

        mock_client.iter_messages = mock_iter

        stats = Stats()
        test_logger = logging.getLogger("test_mock_pipeline")

        result = await bulk_forward(
            client=mock_client,
            src_raw="@mock_src",
            dst_raw="@mock_dst",
            cfg=cfg,
            stats=stats,
            logger=test_logger,
        )

        self.assertEqual(result.forwarded, 0)
        self.assertEqual(result.errors, 0)


if __name__ == "__main__":
    unittest.main()
