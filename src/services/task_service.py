"""
任务管理服务
封装任务相关的业务逻辑
"""
from typing import List, Optional
from src.domain.models.task import Task, TaskCreate, TaskUpdate
from src.domain.models.task_progress import TaskProgress
from src.domain.repositories.task_repository import TaskRepository
from src.domain.repositories.task_progress_repository import TaskProgressRepository
from src.infrastructure.persistence.sqlite_task_progress_repository import SqliteTaskProgressRepository

# 导入WebSocket广播功能
try:
    from src.api.routes.websocket import broadcast_task_progress
    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False
    broadcast_task_progress = None


class TaskService:
    """任务管理服务"""

    def __init__(self, repository: TaskRepository, progress_repository: TaskProgressRepository = None):
        self.repository = repository
        self.progress_repository = progress_repository or SqliteTaskProgressRepository()

    async def get_all_tasks(self) -> List[Task]:
        """获取所有任务"""
        return await self.repository.find_all()

    async def get_task(self, task_id: int) -> Optional[Task]:
        """获取单个任务"""
        return await self.repository.find_by_id(task_id)

    async def create_task(self, task_create: TaskCreate) -> Task:
        """创建新任务"""
        task = Task(**task_create.model_dump(), is_running=False)
        return await self.repository.save(task)

    async def update_task(self, task_id: int, task_update: TaskUpdate) -> Task:
        """更新任务"""
        task = await self.repository.find_by_id(task_id)
        if not task:
            raise ValueError(f"任务 {task_id} 不存在")

        updated_task = task.apply_update(task_update)
        return await self.repository.save(updated_task)

    async def delete_task(self, task_id: int) -> bool:
        """删除任务"""
        return await self.repository.delete(task_id)

    async def update_task_status(self, task_id: int, is_running: bool) -> Task:
        """更新任务运行状态"""
        task_update = TaskUpdate(is_running=is_running)
        return await self.update_task(task_id, task_update)

    async def update_task_progress(self, task_id: int, current_page: int, total_items_found: int, items_processed: int, estimated_remaining_items: Optional[int] = None) -> Task:
        """更新任务进度（只修改进度字段，不触碰 is_running，避免爬虫子进程的写操作覆盖运行状态）"""
        task = await self.repository.find_by_id(task_id)
        if not task:
            raise ValueError(f"任务 {task_id} 不存在")

        # 计算进度百分比
        total_expected_items = total_items_found + (estimated_remaining_items or 0)
        progress_percentage = (items_processed / total_expected_items * 100) if total_expected_items > 0 else 0.0
        last_crawl_time = self._get_current_time_iso()

        # 使用只更新进度字段的方法，避免 INSERT OR REPLACE 把 is_running 等字段覆盖
        if hasattr(self.repository, 'update_progress_only'):
            await self.repository.update_progress_only(
                task_id=task_id,
                current_page=current_page,
                total_items_found=total_items_found,
                items_processed=items_processed,
                estimated_remaining_items=estimated_remaining_items,
                progress_percentage=round(progress_percentage, 2),
                last_crawl_time=last_crawl_time,
            )
            # 重新读取最新任务状态（包含更新后的进度字段）
            updated_task = await self.repository.find_by_id(task_id)
            if not updated_task:
                updated_task = task
        else:
            # 降级回旧逻辑（兼容非 SQLite 仓储实现）
            task.current_page = current_page
            task.total_items_found = total_items_found
            task.items_processed = items_processed
            task.estimated_remaining_items = estimated_remaining_items
            task.progress_percentage = round(progress_percentage, 2)
            task.last_crawl_time = last_crawl_time
            updated_task = await self.repository.save(task)

        # 同时更新进度表
        await self.progress_repository.update_progress(
            task_id=task_id,
            current_page=current_page,
            total_items_found=total_items_found,
            items_processed=items_processed,
            estimated_remaining_items=estimated_remaining_items,
            progress_percentage=round(progress_percentage, 2),
            last_crawl_time=self._get_current_time_iso()
        )

        # 广播任务进度更新到WebSocket客户端
        if WEBSOCKET_AVAILABLE and broadcast_task_progress:
            progress_data = {
                "task_id": task_id,
                "current_page": current_page,
                "total_items_found": total_items_found,
                "items_processed": items_processed,
                "estimated_remaining_items": estimated_remaining_items,
                "progress_percentage": round(progress_percentage, 2),
                "last_crawl_time": self._get_current_time_iso(),
                "status": "running" if updated_task.is_running else "stopped"
            }
            try:
                print(f"[WebSocket] 正在广播任务 {task_id} 进度: page={current_page}, items={items_processed}")
                await broadcast_task_progress(task_id, progress_data)
                print(f"[WebSocket] ✅ 广播成功: task_id={task_id}")
            except Exception as e:
                print(f"[WebSocket] ❌ 广播失败: task_id={task_id}, 错误={e}")

        return updated_task

    async def record_task_error(self, task_id: int, error_message: str) -> Task:
        """记录任务错误信息"""
        task = await self.repository.find_by_id(task_id)
        if not task:
            raise ValueError(f"任务 {task_id} 不存在")

        # 更新任务的错误信息
        task.last_error = error_message
        task.error_timestamp = self._get_current_time_iso()

        # 保存更新
        return await self.repository.save(task)

    async def clear_task_error(self, task_id: int) -> Task:
        """清除任务错误信息"""
        task = await self.repository.find_by_id(task_id)
        if not task:
            raise ValueError(f"任务 {task_id} 不存在")

        # 清除错误信息
        task.last_error = None
        task.error_timestamp = None

        # 保存更新
        return await self.repository.save(task)

    async def get_task_progress(self, task_id: int) -> Optional[TaskProgress]:
        """获取任务进度信息"""
        return await self.progress_repository.find_by_task_id(task_id)

    async def get_task_history(self, task_id: int) -> List[TaskProgress]:
        """获取任务进度历史"""
        return await self.progress_repository.find_all_by_task_id(task_id)

    def _get_current_time_iso(self) -> str:
        """获取当前时间ISO格式"""
        from datetime import datetime
        return datetime.now().isoformat()