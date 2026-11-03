import unittest

from core.sync_state import (
    resolve_sync_page_window,
    record_rate_limit,
    should_defer,
    record_success,
    get_history_next_page,
    advance_history_cursor,
)


class SyncStateTest(unittest.TestCase):
    def test_resolve_pages_empty_feed_uses_history_chunk(self):
        start, end = resolve_sync_page_window("MP_TEST", 0)
        self.assertEqual(start, 0)
        self.assertGreater(end, start)

    def test_resolve_pages_with_articles_incremental(self):
        start, end = resolve_sync_page_window("MP_TEST2", 10)
        self.assertEqual(start, 0)
        self.assertEqual(end, 1)

    def test_rate_limit_backoff(self):
        mp_id = "MP_RATE_TEST"
        record_success(mp_id)
        delay = record_rate_limit(mp_id, "测试号")
        self.assertGreaterEqual(delay, 60)
        defer, reason, retry_at = should_defer(mp_id)
        self.assertTrue(defer)
        self.assertIn("重试", reason)
        self.assertIsNotNone(retry_at)
        record_success(mp_id)
        defer, _, _ = should_defer(mp_id)
        self.assertFalse(defer)

    def test_history_cursor_advances(self):
        mp_id = "MP_HIST"
        record_success(mp_id)
        self.assertEqual(get_history_next_page(mp_id), 0)
        advance_history_cursor(mp_id, 1, 5)
        self.assertEqual(get_history_next_page(mp_id), 1)


if __name__ == "__main__":
    unittest.main()
