"""Built-in MessageTask used when no user-defined sync tasks are active."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from core.config import cfg
from core.db import DB
from core.models.message_task import MessageTask
from core.print import print_info, print_success

# Stable ID so restarts upsert the same row.
BUILTIN_SYNC_TASK_ID = "00000000-0000-4000-8000-000000000001"
BUILTIN_SYNC_TASK_NAME = "系统默认同步（自动）"


def _auto_task_enabled() -> bool:
    return bool(cfg.get("sync.auto_task_enabled", True))


def _builtin_cron() -> str:
    return str(cfg.get("sync.auto_task_cron", "*/30 * * * *") or "*/30 * * * *")


def count_user_active_tasks(session) -> int:
    return (
        session.query(MessageTask)
        .filter(MessageTask.status == 1, MessageTask.id != BUILTIN_SYNC_TASK_ID)
        .count()
    )


def ensure_default_sync_task(session=None) -> Optional[MessageTask]:
    """
    Ensure the built-in sync task exists and is enabled only when no other
    active MessageTasks are configured.
    """
    if not _auto_task_enabled():
        return None

    owns_session = session is None
    if owns_session:
        session = DB.get_session()

    try:
        builtin = (
            session.query(MessageTask)
            .filter(MessageTask.id == BUILTIN_SYNC_TASK_ID)
            .first()
        )
        user_active = count_user_active_tasks(session)
        should_run = user_active == 0
        now = datetime.now()
        cron = _builtin_cron()

        if builtin is None:
            if not should_run:
                return None
            builtin = MessageTask(
                id=BUILTIN_SYNC_TASK_ID,
                name=BUILTIN_SYNC_TASK_NAME,
                message_type=0,
                message_template="",
                web_hook_url="",
                headers="",
                cookies="",
                mps_id="[]",
                cron_exp=cron,
                status=1,
                created_at=now,
                updated_at=now,
            )
            session.add(builtin)
            session.commit()
            print_success(f"已创建内置同步任务（cron={cron}）")
            return builtin

        changed = False
        if builtin.name != BUILTIN_SYNC_TASK_NAME:
            builtin.name = BUILTIN_SYNC_TASK_NAME
            changed = True
        if builtin.cron_exp != cron:
            builtin.cron_exp = cron
            changed = True
        if builtin.mps_id != "[]":
            builtin.mps_id = "[]"
            changed = True

        target_status = 1 if should_run else 0
        if builtin.status != target_status:
            builtin.status = target_status
            changed = True
            if should_run:
                print_info("已启用内置同步任务（当前无用户自定义任务）")
            else:
                print_info("已停用内置同步任务（存在用户自定义任务）")

        if changed:
            builtin.updated_at = now
            session.commit()
        return builtin if should_run else None
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


def is_builtin_task(task: MessageTask | None) -> bool:
    return task is not None and str(task.id) == BUILTIN_SYNC_TASK_ID
