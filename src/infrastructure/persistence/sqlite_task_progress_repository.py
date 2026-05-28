from typing import List, Optional
from datetime import datetime
import sqlite3
from src.domain.models.task_progress import TaskProgress
from src.domain.repositories.task_progress_repository import TaskProgressRepository
from src.infrastructure.persistence.sqlite_connection import sqlite_connection
from src.infrastructure.persistence.storage_names import DEFAULT_DATABASE_PATH
import os


class SqliteTaskProgressRepository(TaskProgressRepository):
    def __init__(self, db_path: str = None):
        self.db_path = db_path or os.getenv("APP_DATABASE_FILE", DEFAULT_DATABASE_PATH)

    async def save(self, progress: TaskProgress) -> TaskProgress:
        """保存进度记录"""
        with sqlite_connection(self.db_path) as conn:
            if progress.id is None:
                # 新增记录
                cursor = conn.execute(
                    """
                    INSERT INTO task_progress (
                        task_id, current_page, total_items_found, items_processed,
                        last_crawl_time, estimated_remaining_items, progress_percentage
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        progress.task_id,
                        progress.current_page,
                        progress.total_items_found,
                        progress.items_processed,
                        progress.last_crawl_time,
                        progress.estimated_remaining_items,
                        progress.progress_percentage
                    )
                )
                progress.id = cursor.lastrowid
            else:
                # 更新记录
                conn.execute(
                    """
                    UPDATE task_progress SET
                        task_id = ?,
                        current_page = ?,
                        total_items_found = ?,
                        items_processed = ?,
                        last_crawl_time = ?,
                        estimated_remaining_items = ?,
                        progress_percentage = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        progress.task_id,
                        progress.current_page,
                        progress.total_items_found,
                        progress.items_processed,
                        progress.last_crawl_time,
                        progress.estimated_remaining_items,
                        progress.progress_percentage,
                        progress.id
                    )
                )
            conn.commit()
            return progress

    async def find_by_task_id(self, task_id: int) -> Optional[TaskProgress]:
        """根据任务ID查找最新进度"""
        with sqlite_connection(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT id, task_id, current_page, total_items_found, items_processed,
                       last_crawl_time, estimated_remaining_items, progress_percentage,
                       created_at, updated_at
                FROM task_progress
                WHERE task_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (task_id,)
            ).fetchone()
            
            if row:
                return TaskProgress(**dict(row))
            return None

    async def find_all_by_task_id(self, task_id: int) -> List[TaskProgress]:
        """根据任务ID查找所有进度记录"""
        with sqlite_connection(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, task_id, current_page, total_items_found, items_processed,
                       last_crawl_time, estimated_remaining_items, progress_percentage,
                       created_at, updated_at
                FROM task_progress
                WHERE task_id = ?
                ORDER BY created_at DESC
                """,
                (task_id,)
            ).fetchall()
            
            return [TaskProgress(**dict(row)) for row in rows]

    async def update_progress(self, task_id: int, **kwargs) -> Optional[TaskProgress]:
        """更新任务进度（使用 INSERT OR REPLACE 确保一个任务只有一条记录）"""
        with sqlite_connection(self.db_path) as conn:
            # 先查询是否存在旧记录
            row = conn.execute(
                "SELECT id FROM task_progress WHERE task_id = ?",
                (task_id,)
            ).fetchone()

            progress_data = {
                'task_id': task_id,
                'created_at': datetime.now().isoformat(),
                'updated_at': datetime.now().isoformat()
            }
            progress_data.update(kwargs)

            if row:
                # 存在旧记录，使用 UPDATE
                old_id = row[0]
                progress_data['id'] = old_id
                progress_data['created_at'] = datetime.now().isoformat()  # 保留原始创建时间

                # 使用 INSERT OR REPLACE 来确保更新
                conn.execute(
                    """
                    INSERT OR REPLACE INTO task_progress (
                        id, task_id, current_page, total_items_found, items_processed,
                        last_crawl_time, estimated_remaining_items, progress_percentage,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        old_id,
                        progress_data.get('task_id'),
                        progress_data.get('current_page', 0),
                        progress_data.get('total_items_found', 0),
                        progress_data.get('items_processed', 0),
                        progress_data.get('last_crawl_time'),
                        progress_data.get('estimated_remaining_items'),
                        progress_data.get('progress_percentage', 0),
                        progress_data.get('created_at'),
                        progress_data.get('updated_at'),
                    )
                )
            else:
                # 不存在旧记录，创建新记录
                cursor = conn.execute(
                    """
                    INSERT INTO task_progress (
                        task_id, current_page, total_items_found, items_processed,
                        last_crawl_time, estimated_remaining_items, progress_percentage,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        progress_data.get('task_id'),
                        progress_data.get('current_page', 0),
                        progress_data.get('total_items_found', 0),
                        progress_data.get('items_processed', 0),
                        progress_data.get('last_crawl_time'),
                        progress_data.get('estimated_remaining_items'),
                        progress_data.get('progress_percentage', 0),
                        progress_data.get('created_at'),
                        progress_data.get('updated_at'),
                    )
                )
                progress_data['id'] = cursor.lastrowid

            conn.commit()
            progress = TaskProgress(**progress_data)
            return progress