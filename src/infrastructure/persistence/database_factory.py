"""
数据库工厂
根据配置创建适当的数据库实例
"""
from typing import Union

from src.infrastructure.persistence.db_interface import DatabaseInterface
from src.infrastructure.persistence.sqlite_database import SQLiteDatabase
from src.infrastructure.persistence.postgres_database import PostgreSQLDatabase
from src.infrastructure.config.database_settings import DatabaseSettings


class DatabaseFactory:
    """数据库工厂类"""
    
    @staticmethod
    def create_database(settings: DatabaseSettings) -> DatabaseInterface:
        """根据配置创建数据库实例"""
        if settings.is_postgresql():
            return PostgreSQLDatabase(settings)
        else:  # 默认使用SQLite
            return SQLiteDatabase(settings)