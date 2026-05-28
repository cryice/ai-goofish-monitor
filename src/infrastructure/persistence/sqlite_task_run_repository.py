"""
task_runs 表的持久化层
每次任务执行（start → stop）对应一条记录
"""
from typing import List, Optional
from datetime import datetime
import os

from src.domain.models.task_run import TaskRun
from src.infrastructure.persistence.sqlite_connection import sqlite_connection
from src.infrastructure.persistence.storage_names import DEFAULT_DATABASE_PATH


class SqliteTaskRunRepository:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or os.getenv("APP_DATABASE_FILE", DEFAULT_DATABASE_PATH)

    # ------------------------------------------------------------------
    # 写操作
    # ------------------------------------------------------------------

    async def create_run(self, task_id: int, execution_mode: str = "periodic", batch_number: int = 0) -> TaskRun:
        """任务启动时创建一条 running 记录，返回带 id 的 TaskRun。"""
        now = datetime.now().isoformat()
        run = TaskRun(
            task_id=task_id,
            execution_mode=execution_mode,
            batch_number=batch_number,
            run_start_time=now,
            status="running",
            created_at=now,
            updated_at=now,
        )
        with sqlite_connection(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO task_runs (
                    task_id, execution_mode, batch_number, run_start_time, run_end_time,
                    pages_crawled, items_found, items_processed,
                    status, error_message, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.task_id,
                    run.execution_mode,
                    run.batch_number,
                    run.run_start_time,
                    run.run_end_time,
                    run.pages_crawled,
                    run.items_found,
                    run.items_processed,
                    run.status,
                    run.error_message,
                    run.created_at,
                    run.updated_at,
                ),
            )
            conn.commit()
            run.id = cursor.lastrowid
        return run

    async def finish_run(
        self,
        run_id: int,
        status: str = "completed",
        pages_crawled: int = 0,
        items_found: int = 0,
        items_processed: int = 0,
        error_message: Optional[str] = None,
    ) -> Optional[TaskRun]:
        """任务停止时补全记录（结束时间、统计数据、最终状态）。"""
        now = datetime.now().isoformat()
        with sqlite_connection(self.db_path) as conn:
            conn.execute(
                """
                UPDATE task_runs SET
                    run_end_time   = ?,
                    pages_crawled  = ?,
                    items_found    = ?,
                    items_processed = ?,
                    status         = ?,
                    error_message  = ?,
                    updated_at     = ?
                WHERE id = ?
                """,
                (
                    now,
                    pages_crawled,
                    items_found,
                    items_processed,
                    status,
                    error_message,
                    now,
                    run_id,
                ),
            )
            conn.commit()
        return await self.find_by_id(run_id)

    async def finish_latest_run_for_task(
        self,
        task_id: int,
        status: str = "completed",
        error_message: Optional[str] = None,
    ) -> Optional[TaskRun]:
        """查找该任务最近一条 running 记录并结束它（用于生命周期钩子）。"""
        run = await self.find_latest_running(task_id)
        if run is None:
            return None
        return await self.finish_run(
            run_id=run.id,
            status=status,
            pages_crawled=run.pages_crawled,
            items_found=run.items_found,
            items_processed=run.items_processed,
            error_message=error_message,
        )

    async def update_run_stats(
        self,
        run_id: int,
        pages_crawled: int,
        items_found: int,
        items_processed: int,
    ) -> None:
        """在执行过程中更新统计数据（由 scraper 调用）。"""
        now = datetime.now().isoformat()
        with sqlite_connection(self.db_path) as conn:
            conn.execute(
                """
                UPDATE task_runs SET
                    pages_crawled   = ?,
                    items_found     = ?,
                    items_processed = ?,
                    updated_at      = ?
                WHERE id = ?
                """,
                (pages_crawled, items_found, items_processed, now, run_id),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # 读操作
    # ------------------------------------------------------------------

    async def find_by_id(self, run_id: int) -> Optional[TaskRun]:
        with sqlite_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM task_runs WHERE id = ?", (run_id,)
            ).fetchone()
            return TaskRun(**dict(row)) if row else None

    async def find_latest_running(self, task_id: int) -> Optional[TaskRun]:
        """返回该任务最近一条状态为 running 的记录。"""
        with sqlite_connection(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT * FROM task_runs
                WHERE task_id = ? AND status = 'running'
                ORDER BY run_start_time DESC
                LIMIT 1
                """,
                (task_id,),
            ).fetchone()
            return TaskRun(**dict(row)) if row else None

    async def find_all_by_task_id(self, task_id: int, limit: int = 50) -> List[TaskRun]:
        """返回该任务最近 N 条执行记录（最新在前）。"""
        with sqlite_connection(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT * FROM task_runs
                WHERE task_id = ?
                ORDER BY run_start_time DESC
                LIMIT ?
                """,
                (task_id, limit),
            ).fetchall()
            return [TaskRun(**dict(row)) for row in rows]
