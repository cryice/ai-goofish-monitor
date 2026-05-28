"""
数据库连接管理
支持SQLite和PostgreSQL的统一接口
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from src.infrastructure.config.settings import database_settings
from src.infrastructure.persistence.database_factory import DatabaseFactory
from src.infrastructure.persistence.db_interface import DatabaseInterface


# 全局数据库实例
_database_instance: DatabaseInterface = None


def get_database() -> DatabaseInterface:
    """获取数据库实例"""
    global _database_instance
    if _database_instance is None:
        _database_instance = DatabaseFactory.create_database(database_settings)
    return _database_instance


def init_database():
    """初始化数据库"""
    db = get_database()
    db.init_schema()


# 为了向后兼容现有代码，保留SQLite连接函数
from src.infrastructure.persistence.sqlite_connection import (
    sqlite_connection,
    init_schema,
    get_database_path,
    BUSY_TIMEOUT_MS,
    SCHEMA_STATEMENTS
)