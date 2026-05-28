from abc import ABC, abstractmethod
from typing import List, Optional
from src.domain.models.task_progress import TaskProgress


class TaskProgressRepository(ABC):
    @abstractmethod
    async def save(self, progress: TaskProgress) -> TaskProgress:
        """保存进度记录"""
        pass

    @abstractmethod
    async def find_by_task_id(self, task_id: int) -> Optional[TaskProgress]:
        """根据任务ID查找最新进度"""
        pass

    @abstractmethod
    async def find_all_by_task_id(self, task_id: int) -> List[TaskProgress]:
        """根据任务ID查找所有进度记录"""
        pass

    @abstractmethod
    async def update_progress(self, task_id: int, **kwargs) -> Optional[TaskProgress]:
        """更新任务进度"""
        pass