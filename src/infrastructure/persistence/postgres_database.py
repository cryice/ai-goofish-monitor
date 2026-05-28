"""
PostgreSQL 数据库实现
"""
import os
from contextlib import contextmanager
from typing import Any, Dict, List, Optional
from pathlib import Path

from src.infrastructure.persistence.db_interface import DatabaseInterface
from src.infrastructure.config.database_settings import DatabaseSettings


class PostgreSQLDatabase(DatabaseInterface):
    """PostgreSQL数据库实现"""
    
    def __init__(self, settings: DatabaseSettings):
        self.settings = settings
        # 延迟导入psycopg2以避免在没有安装时出现导入错误
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
            self.psycopg2 = psycopg2
            self.RealDictCursor = RealDictCursor
        except ImportError:
            raise ImportError("psycopg2 is required for PostgreSQL support. Please install it with: pip install psycopg2-binary")
        
        self.connection_params = {
            'host': settings.postgres_host,
            'port': settings.postgres_port,
            'database': settings.postgres_db,
            'user': settings.postgres_user,
            'password': settings.postgres_password,
            'sslmode': settings.postgres_ssl_mode
        }
    
    def init_schema(self) -> None:
        """初始化数据库schema"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # 创建app_metadata表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS app_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            
            # 创建tasks表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id SERIAL PRIMARY KEY,
                    task_name TEXT NOT NULL,
                    enabled BOOLEAN NOT NULL,
                    keyword TEXT NOT NULL,
                    description TEXT,
                    analyze_images BOOLEAN NOT NULL,
                    max_pages INTEGER NOT NULL,
                    start_page INTEGER NOT NULL DEFAULT 1,
                    personal_only BOOLEAN NOT NULL,
                    min_price TEXT,
                    max_price TEXT,
                    cron TEXT,
                    ai_prompt_base_file TEXT NOT NULL,
                    ai_prompt_criteria_file TEXT NOT NULL,
                    account_state_file TEXT,
                    account_strategy TEXT NOT NULL,
                    free_shipping BOOLEAN NOT NULL,
                    new_publish_option TEXT,
                    region TEXT,
                    decision_mode TEXT NOT NULL,
                    keyword_rules_json TEXT NOT NULL DEFAULT '[]',
                    is_running BOOLEAN NOT NULL,
                    execution_mode TEXT NOT NULL DEFAULT 'periodic',
                    max_pages INTEGER NOT NULL DEFAULT 3,
                    sleep_interval_min INTEGER NOT NULL DEFAULT 180,
                    sleep_interval_max INTEGER NOT NULL DEFAULT 300,
                    max_page_limit INTEGER
                )
            """)
            
            # 创建result_items表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS result_items (
                    id SERIAL PRIMARY KEY,
                    result_filename TEXT NOT NULL,
                    keyword TEXT NOT NULL,
                    task_name TEXT NOT NULL,
                    crawl_time TIMESTAMP WITH TIME ZONE NOT NULL,
                    publish_time TIMESTAMP WITH TIME ZONE,
                    price NUMERIC,
                    price_display TEXT,
                    item_id TEXT,
                    title TEXT,
                    link TEXT,
                    link_unique_key TEXT NOT NULL UNIQUE,
                    seller_nickname TEXT,
                    is_recommended INTEGER NOT NULL,
                    analysis_source TEXT,
                    keyword_hit_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'active',
                    raw_json JSONB NOT NULL,
                    UNIQUE(result_filename, link_unique_key)
                )
            """)
            
            # 创建price_snapshots表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS price_snapshots (
                    id SERIAL PRIMARY KEY,
                    keyword_slug TEXT NOT NULL,
                    keyword TEXT NOT NULL,
                    task_name TEXT NOT NULL,
                    snapshot_time TIMESTAMP WITH TIME ZONE NOT NULL,
                    snapshot_day DATE NOT NULL,
                    run_id TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    title TEXT,
                    price NUMERIC NOT NULL,
                    price_display TEXT,
                    tags_json JSONB NOT NULL,
                    region TEXT,
                    seller TEXT,
                    publish_time TIMESTAMP WITH TIME ZONE,
                    link TEXT,
                    UNIQUE(keyword_slug, run_id, item_id)
                )
            """)
            
            # 创建result_blacklist_rules表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS result_blacklist_rules (
                    result_filename TEXT PRIMARY KEY,
                    blacklist_keywords_json JSONB NOT NULL,
                    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
                )
            """)
            
            # 创建item_skus表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS item_skus (
                    id SERIAL PRIMARY KEY,
                    result_item_id INTEGER NOT NULL REFERENCES result_items(id) ON DELETE CASCADE,
                    sku_name TEXT NOT NULL,
                    sku_price TEXT,
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
                )
            """)
            
            # 创建索引
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_name ON tasks(task_name)")
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_results_filename_crawl
                ON result_items(result_filename, crawl_time DESC)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_results_filename_publish
                ON result_items(result_filename, publish_time DESC)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_results_filename_price
                ON result_items(result_filename, price DESC)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_results_filename_recommended
                ON result_items(result_filename, is_recommended, analysis_source, crawl_time DESC)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_snapshots_keyword_time
                ON price_snapshots(keyword_slug, snapshot_time DESC)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_snapshots_keyword_item_time
                ON price_snapshots(keyword_slug, item_id, snapshot_time DESC)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_results_filename_status_crawl
                ON result_items(result_filename, status, crawl_time DESC)
            """)
            
            conn.commit()
    
    @contextmanager
    def get_connection(self):
        """获取数据库连接上下文管理器"""
        conn = self.psycopg2.connect(**self.connection_params)
        try:
            yield conn
        finally:
            conn.close()
    
    def execute(self, query: str, params: Optional[tuple] = None) -> Any:
        """执行SQL语句"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
            return cursor
    
    def fetch_one(self, query: str, params: Optional[tuple] = None) -> Optional[Dict]:
        """获取单条记录"""
        with self.get_connection() as conn:
            cursor = conn.cursor(cursor_factory=self.RealDictCursor)
            cursor.execute(query, params)
            result = cursor.fetchone()
            return dict(result) if result else None
    
    def fetch_all(self, query: str, params: Optional[tuple] = None) -> List[Dict]:
        """获取所有记录"""
        with self.get_connection() as conn:
            cursor = conn.cursor(cursor_factory=self.RealDictCursor)
            cursor.execute(query, params)
            results = cursor.fetchall()
            return [dict(row) for row in results]