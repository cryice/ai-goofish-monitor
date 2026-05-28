"""
数据库接口定义
定义统一的数据库操作接口
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, ContextManager
from contextlib import contextmanager


class DatabaseInterface(ABC):
    """数据库操作接口"""
    
    @abstractmethod
    def init_schema(self) -> None:
        """初始化数据库schema"""
        pass
    
    @abstractmethod
    @contextmanager
    def get_connection(self):
        """获取数据库连接上下文管理器"""
        pass
    
    @abstractmethod
    def execute(self, query: str, params: Optional[tuple] = None) -> Any:
        """执行SQL语句"""
        pass
    
    @abstractmethod
    def fetch_one(self, query: str, params: Optional[tuple] = None) -> Optional[Dict]:
        """获取单条记录"""
        pass
    
    @abstractmethod
    def fetch_all(self, query: str, params: Optional[tuple] = None) -> List[Dict]:
        """获取所有记录"""
        pass