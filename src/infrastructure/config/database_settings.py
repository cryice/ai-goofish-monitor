"""
数据库配置和抽象层
支持SQLite和PostgreSQL 18
"""
try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
    _USING_PYDANTIC_SETTINGS = True
except ImportError:
    from pydantic import BaseSettings
    _USING_PYDANTIC_SETTINGS = False
from pydantic import Field
from typing import Optional
import os


def _env_field(default, env_name: str, **kwargs):
    if _USING_PYDANTIC_SETTINGS:
        return Field(default, validation_alias=env_name, **kwargs)
    return Field(default, env=env_name, **kwargs)


if _USING_PYDANTIC_SETTINGS:
    class _EnvSettings(BaseSettings):
        model_config = SettingsConfigDict(
            env_file=".env",
            env_file_encoding="utf-8",
            extra="ignore",
            protected_namespaces=(),
        )
else:
    class _EnvSettings(BaseSettings):
        class Config:
            env_file = ".env"
            env_file_encoding = "utf-8"
            extra = "ignore"
            protected_namespaces = ()


class DatabaseSettings(_EnvSettings):
    """数据库配置"""
    # 数据库类型 (sqlite/postgres)
    db_type: str = _env_field("sqlite", "DB_TYPE")
    
    # SQLite配置
    sqlite_path: str = _env_field("data/app.sqlite3", "SQLITE_PATH")
    
    # PostgreSQL配置
    postgres_host: str = _env_field("localhost", "POSTGRES_HOST")
    postgres_port: int = _env_field(5432, "POSTGRES_PORT")
    postgres_db: str = _env_field("goofish_monitor", "POSTGRES_DB")
    postgres_user: str = _env_field("goofish_user", "POSTGRES_USER")
    postgres_password: str = _env_field("", "POSTGRES_PASSWORD")
    postgres_ssl_mode: str = _env_field("prefer", "POSTGRES_SSL_MODE")
    
    def get_database_url(self) -> str:
        """获取数据库连接字符串"""
        if self.db_type.lower() == "postgres":
            ssl_param = f"?sslmode={self.postgres_ssl_mode}" if self.postgres_ssl_mode else ""
            return f"postgresql://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}{ssl_param}"
        else:  # sqlite
            return f"sqlite:///{self.sqlite_path}"
    
    def is_postgresql(self) -> bool:
        """检查是否使用PostgreSQL"""
        return self.db_type.lower() == "postgres"
    
    def is_sqlite(self) -> bool:
        """检查是否使用SQLite"""
        return self.db_type.lower() == "sqlite"