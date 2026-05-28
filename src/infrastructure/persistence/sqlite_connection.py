"""
SQLite 连接与 schema 初始化。
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from src.infrastructure.persistence.storage_names import DEFAULT_DATABASE_PATH


BUSY_TIMEOUT_MS = 5000

# 表定义：只包含 CREATE TABLE 和 UNIQUE 约束
TABLE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS app_metadata (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY,
        task_name TEXT NOT NULL,
        enabled INTEGER NOT NULL,
        keyword TEXT NOT NULL,
        description TEXT,
        analyze_images INTEGER NOT NULL,
        max_pages INTEGER NOT NULL,
        start_page INTEGER NOT NULL DEFAULT 1,
        personal_only INTEGER NOT NULL,
        min_price TEXT,
        max_price TEXT,
        cron TEXT,
        ai_prompt_base_file TEXT NOT NULL,
        ai_prompt_criteria_file TEXT NOT NULL,
        account_state_file TEXT,
        account_strategy TEXT NOT NULL,
        free_shipping INTEGER NOT NULL,
        new_publish_option TEXT,
        region TEXT,
        decision_mode TEXT NOT NULL,
        keyword_rules_json TEXT NOT NULL,
        is_running INTEGER NOT NULL,
        execution_mode TEXT NOT NULL DEFAULT 'periodic',
        max_pages INTEGER NOT NULL DEFAULT 3,
        sleep_interval_min INTEGER NOT NULL DEFAULT 180,
        sleep_interval_max INTEGER NOT NULL DEFAULT 300,
        max_page_limit INTEGER,
        current_page INTEGER DEFAULT 0,
        total_items_found INTEGER DEFAULT 0,
        items_processed INTEGER DEFAULT 0,
        last_crawl_time TEXT,
        estimated_remaining_items INTEGER,
        progress_percentage REAL DEFAULT 0.0,
        last_error TEXT,
        error_timestamp TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS task_progress (
        id INTEGER PRIMARY KEY,
        task_id INTEGER NOT NULL UNIQUE,
        current_page INTEGER DEFAULT 0,
        total_items_found INTEGER DEFAULT 0,
        items_processed INTEGER DEFAULT 0,
        last_crawl_time TEXT,
        estimated_remaining_items INTEGER,
        progress_percentage REAL DEFAULT 0.0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS result_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        result_filename TEXT NOT NULL,
        keyword TEXT NOT NULL,
        task_name TEXT NOT NULL,
        crawl_time TEXT NOT NULL,
        publish_time TEXT,
        price REAL,
        price_display TEXT,
        item_id TEXT,
        title TEXT,
        link TEXT,
        link_unique_key TEXT NOT NULL,
        seller_nickname TEXT,
        is_recommended INTEGER NOT NULL,
        analysis_source TEXT,
        keyword_hit_count INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        raw_json TEXT NOT NULL,
        UNIQUE(result_filename, link_unique_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS price_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        keyword_slug TEXT NOT NULL,
        keyword TEXT NOT NULL,
        task_name TEXT NOT NULL,
        snapshot_time TEXT NOT NULL,
        snapshot_day TEXT NOT NULL,
        run_id TEXT NOT NULL,
        item_id TEXT NOT NULL,
        title TEXT,
        price REAL NOT NULL,
        price_display TEXT,
        tags_json TEXT NOT NULL,
        region TEXT,
        seller TEXT,
        publish_time TEXT,
        link TEXT,
        UNIQUE(keyword_slug, run_id, item_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS result_blacklist_rules (
        result_filename TEXT PRIMARY KEY,
        blacklist_keywords_json TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS item_skus (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        result_item_id INTEGER NOT NULL,
        sku_name TEXT NOT NULL,
        sku_price TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (result_item_id) REFERENCES result_items(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS task_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER NOT NULL,
        execution_mode TEXT NOT NULL DEFAULT 'periodic',
        batch_number INTEGER NOT NULL DEFAULT 0,
        run_start_time TEXT NOT NULL,
        run_end_time TEXT,
        pages_crawled INTEGER NOT NULL DEFAULT 0,
        items_found INTEGER NOT NULL DEFAULT 0,
        items_processed INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'running',
        error_message TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
    )
    """,
)

# 索引定义：在表和列都存在后创建
INDEX_STATEMENTS = (
    """
    CREATE INDEX IF NOT EXISTS idx_task_runs_task_id_batch
    ON task_runs(task_id, execution_mode, batch_number DESC)
    """,
    "CREATE INDEX IF NOT EXISTS idx_tasks_name ON tasks(task_name)",
    """
    CREATE INDEX IF NOT EXISTS idx_results_filename_crawl
    ON result_items(result_filename, crawl_time DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_results_filename_publish
    ON result_items(result_filename, publish_time DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_results_filename_price
    ON result_items(result_filename, price DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_results_filename_recommended
    ON result_items(result_filename, is_recommended, analysis_source, crawl_time DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_snapshots_keyword_time
    ON price_snapshots(keyword_slug, snapshot_time DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_snapshots_keyword_item_time
    ON price_snapshots(keyword_slug, item_id, snapshot_time DESC)
    """,
)


def get_database_path() -> str:
    return os.getenv("APP_DATABASE_FILE", DEFAULT_DATABASE_PATH)


def _prepare_database_file(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")


def init_schema(conn: sqlite3.Connection) -> None:
    # 1. 首先创建所有表（不包括索引）
    for statement in TABLE_STATEMENTS:
        conn.execute(statement)

    # 2. 执行所有迁移（添加缺失的列）
    _migrate_result_items_status(conn)
    _migrate_tasks_execution_mode(conn)
    _migrate_add_task_runs(conn)
    _migrate_add_estimated_remaining_items(conn)

    # 3. 最后创建所有索引（此时所有列都已存在）
    for statement in INDEX_STATEMENTS:
        conn.execute(statement)

    conn.commit()


def _migrate_result_items_status(conn: sqlite3.Connection) -> None:
    """为 result_items 表添加 status 列（仅执行一次）。"""
    row = conn.execute(
        "SELECT value FROM app_metadata WHERE key = 'migration:result_items_status'"
    ).fetchone()
    if row is not None:
        return
    cols = [r[1] for r in conn.execute("PRAGMA table_info(result_items)").fetchall()]
    if "status" not in cols:
        conn.execute(
            "ALTER TABLE result_items ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"
        )
    conn.execute(
        "INSERT OR REPLACE INTO app_metadata(key, value) VALUES ('migration:result_items_status', 'done')"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_results_filename_status_crawl"
        " ON result_items(result_filename, status, crawl_time DESC)"
    )


def _migrate_tasks_execution_mode(conn: sqlite3.Connection) -> None:
    """为 tasks 表添加连续抓取相关字段"""
    row = conn.execute(
        "SELECT value FROM app_metadata WHERE key = 'migration:tasks_execution_mode'"
    ).fetchone()
    if row is not None:
        return
    cols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()]
    if "execution_mode" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'periodic'")
    if "max_pages" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN max_pages INTEGER NOT NULL DEFAULT 3")
    if "sleep_interval_min" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN sleep_interval_min INTEGER NOT NULL DEFAULT 180")
    if "sleep_interval_max" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN sleep_interval_max INTEGER NOT NULL DEFAULT 300")
    if "max_page_limit" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN max_page_limit INTEGER")
    conn.execute(
        "INSERT OR REPLACE INTO app_metadata(key, value) VALUES ('migration:tasks_execution_mode', 'done')"
    )


def _migrate_add_task_runs(conn: sqlite3.Connection) -> None:
    """新增 task_runs 表（仅执行一次）。"""
    row = conn.execute(
        "SELECT value FROM app_metadata WHERE key = 'migration:add_task_runs'"
    ).fetchone()
    if row is not None:
        # 表已存在，检查是否需要添加新字段
        cols = [r[1] for r in conn.execute("PRAGMA table_info(task_runs)").fetchall()]
        if "execution_mode" not in cols:
            # 添加执行模式字段
            conn.execute("ALTER TABLE task_runs ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'periodic'")
        if "batch_number" not in cols:
            # 添加批次号字段
            conn.execute("ALTER TABLE task_runs ADD COLUMN batch_number INTEGER NOT NULL DEFAULT 0")
        return
    # CREATE TABLE IF NOT EXISTS 已在 SCHEMA_STATEMENTS 中处理
    # 这里只需标记迁移完成
    conn.execute(
        "INSERT OR REPLACE INTO app_metadata(key, value) VALUES ('migration:add_task_runs', 'done')"
    )


def _migrate_add_estimated_remaining_items(conn: sqlite3.Connection) -> None:
    """为 task_progress 和 tasks 表添加 estimated_remaining_items 列（如果缺失）。"""
    row = conn.execute(
        "SELECT value FROM app_metadata WHERE key = 'migration:add_estimated_remaining_items'"
    ).fetchone()
    if row is not None:
        return

    # 检查并添加到 task_progress 表
    cols = [r[1] for r in conn.execute("PRAGMA table_info(task_progress)").fetchall()]
    if "estimated_remaining_items" not in cols:
        try:
            conn.execute(
                "ALTER TABLE task_progress ADD COLUMN estimated_remaining_items INTEGER"
            )
            print("[迁移] ✅ 已添加 task_progress.estimated_remaining_items 列")
        except Exception as e:
            print(f"[迁移] ⚠️ 添加 task_progress.estimated_remaining_items 失败: {e}")

    # 检查并添加到 tasks 表
    cols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()]
    if "estimated_remaining_items" not in cols:
        try:
            conn.execute(
                "ALTER TABLE tasks ADD COLUMN estimated_remaining_items INTEGER"
            )
            print("[迁移] ✅ 已添加 tasks.estimated_remaining_items 列")
        except Exception as e:
            print(f"[迁移] ⚠️ 添加 tasks.estimated_remaining_items 失败: {e}")

    conn.execute(
        "INSERT OR REPLACE INTO app_metadata(key, value) VALUES ('migration:add_estimated_remaining_items', 'done')"
    )


@contextmanager
def sqlite_connection(
    db_path: str | None = None,
) -> Iterator[sqlite3.Connection]:
    path = db_path or get_database_path()
    _prepare_database_file(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        _apply_pragmas(conn)
        yield conn
    finally:
        conn.close()