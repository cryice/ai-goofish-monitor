from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class TaskProgress(BaseModel):
    id: Optional[int] = None
    task_id: int
    current_page: int = Field(default=0, description="当前采集页码")
    total_items_found: int = Field(default=0, description="已找到的商品总数")
    items_processed: int = Field(default=0, description="已处理的商品数")
    last_crawl_time: Optional[str] = Field(default=None, description="最后采集时间")
    estimated_remaining_items: Optional[int] = Field(default=None, description="预估剩余商品数")
    progress_percentage: float = Field(default=0.0, description="进度百分比")
    created_at: Optional[str] = Field(default=None, description="创建时间")
    updated_at: Optional[str] = Field(default=None, description="更新时间")
    
    class Config:
        from_attributes = True