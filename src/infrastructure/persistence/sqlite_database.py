"""
SQLite 数据库包装器
实现DatabaseInterface接口
"""
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, List, Optional
from pathlib import Path

from src.infrastructure.persistence.db_interface import DatabaseInterface
from src.infrastructure.persistence.sqlite_connection import sqlite_connection, init_schema
from src.infrastructure.config.database_settings import DatabaseSettings


class SQLiteDatabase(DatabaseInterface):
    """SQLite数据库包装器"""
    
    def __init__(self, settings: DatabaseSettings = None):
        self.settings = settings
        if settings:
            self.db_path = settings.sqlite_path
        else:
            # 使用默认路径
            from src.infrastructure.persistence.sqlite_connection import get_database_path
            self.db_path = get_database_path()
    
    def init_schema(self) -> None:
        """初始化数据库schema"""
        with sqlite_connection(self.db_path) as conn:
            init_schema(conn)
    
    @contextmanager
    def get_connection(self):
        """获取数据库连接上下文管理器"""
        with sqlite_connection(self.db_path) as conn:
            yield conn
    
    def execute(self, query: str, params: Optional[tuple] = None) -> Any:
        """执行SQL语句"""
        with sqlite_connection(self.db_path) as conn:
            cursor = conn.execute(query, params or ())
            conn.commit()
            return cursor
    
    def fetch_one(self, query: str, params: Optional[tuple] = None) -> Optional[Dict]:
        """获取单条记录"""
        with sqlite_connection(self.db_path) as conn:
            cursor = conn.execute(query, params or ())
            row = cursor.fetchone()
            if row:
                # 将sqlite3.Row转换为字典
                return dict(row)
            return None
    
    def fetch_all(self, query: str, params: Optional[tuple] = None) -> List[Dict]:
        """获取所有记录"""
        with sqlite_connection(self.db_path) as conn:
            cursor = conn.execute(query, params or ())
            rows = cursor.fetchall()
            # 将sqlite3.Row转换为字典列表
            return [dict(row) for row in rows]