import unittest
from unittest.mock import MagicMock, patch

from core.default_sync_task import (
    BUILTIN_SYNC_TASK_ID,
    ensure_default_sync_task,
    count_user_active_tasks,
)


class DefaultSyncTaskTest(unittest.TestCase):
    @patch("core.default_sync_task.DB")
    def test_creates_builtin_when_no_user_tasks(self, mock_db):
        session = MagicMock()
        mock_db.get_session.return_value = session

        query = session.query.return_value
        filter_chain = query.filter.return_value
        filter_chain.first.side_effect = [None, None]
        filter_chain.count.return_value = 0

        task = ensure_default_sync_task(session)
        self.assertIsNotNone(task)
        session.add.assert_called_once()
        session.commit.assert_called()

    @patch("core.default_sync_task.DB")
    def test_disables_builtin_when_user_tasks_exist(self, mock_db):
        session = MagicMock()
        mock_db.get_session.return_value = session

        builtin = MagicMock()
        builtin.id = BUILTIN_SYNC_TASK_ID
        builtin.status = 1
        builtin.cron_exp = "*/30 * * * *"
        builtin.mps_id = "[]"
        builtin.name = "系统默认同步（自动）"

        query = session.query.return_value
        filter_chain = query.filter.return_value
        filter_chain.first.return_value = builtin
        filter_chain.count.return_value = 1

        result = ensure_default_sync_task(session)
        self.assertIsNone(result)
        self.assertEqual(builtin.status, 0)


if __name__ == "__main__":
    unittest.main()
