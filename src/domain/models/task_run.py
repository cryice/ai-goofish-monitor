from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class TaskRun(BaseModel):
    """每次任务执行的记录

    - Periodic 模式：每次启动 → 1 条（batch_number=0）
    - Continuous 模式：每个批次 → 1 条（batch_number=1,2,3...）
    """

    id: Optional[int] = None
    task_id: int
    execution_mode: str = Field(default="periodic", description="执行模式: periodic / continuous")
    batch_number: int = Field(default=0, description="批次号（periodic=0，continuous=1,2,3...）")
    run_start_time: str = Field(description="本次执行开始时间 (ISO)")
    run_end_time: Optional[str] = Field(default=None, description="本次执行结束时间 (ISO)")
    pages_crawled: int = Field(default=0, description="本次执行抓取的页数")
    items_found: int = Field(default=0, description="本次执行发现的商品总数")
    items_processed: int = Field(default=0, description="本次执行处理的商品数")
    status: str = Field(default="running", description="状态: running / completed / failed / stopped")
    error_message: Optional[str] = Field(default=None, description="错误信息")
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True

