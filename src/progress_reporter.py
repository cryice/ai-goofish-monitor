"""
进度报告中间件
用于将子进程中scraper的进度信息传递到主进程的WebSocket系统
"""
import asyncio
import json
import os
from datetime import datetime
from typing import Dict, Any, Optional

# 尝试导入WebSocket服务
_websocket_available = True
try:
    from src.api.routes import websocket
except ImportError:
    _websocket_available = False
    print("WebSocket模块不可用，进度信息将仅写入日志")

# 进度报告类型
PROGRESS_TYPES = {
    "page_progress",      # 页面进度
    "item_progress",      # 商品进度
    "continuous_progress", # 连续抓取进度
    "sleep_progress",     # 休眠进度
    "error",              # 错误信息
    "task_progress_detail" # 任务进度详情
}

class ProgressReporter:
    """进度报告器"""
    
    def __init__(self, task_id: int, task_name: str):
        self.task_id = task_id
        self.task_name = task_name
        self.progress_file = f"temp/progress_{task_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        # 确保临时目录存在
        os.makedirs("temp", exist_ok=True)
    
    async def report_progress(self, progress_type: str, message: str) -> None:
        """
        报告进度信息
        这会将进度信息写入临时文件，主进程可以定期读取
        """
        if progress_type not in PROGRESS_TYPES:
            print(f"未知的进度类型: {progress_type}")
            return
            
        progress_data = {
            "timestamp": datetime.now().isoformat(),
            "task_id": self.task_id,
            "task_name": self.task_name,
            "progress_type": progress_type,
            "message": message
        }
        
        # 尝试通过WebSocket发送进度信息
        if _websocket_available:
            try:
                await websocket.broadcast_message("task_progress", {
                    "task_id": self.task_id,
                    "task_name": self.task_name,
                    "progress_type": progress_type,
                    "message": message,
                    "timestamp": progress_data["timestamp"]
                })
            except Exception as e:
                print(f"通过WebSocket发送进度信息失败: {e}")
        
        # 写入临时文件
        try:
            # 读取现有数据
            existing_data = []
            if os.path.exists(self.progress_file):
                with open(self.progress_file, 'r', encoding='utf-8') as f:
                    try:
                        existing_data = json.load(f)
                    except json.JSONDecodeError:
                        existing_data = []
            
            # 添加新数据
            existing_data.append(progress_data)
            
            # 写回文件
            with open(self.progress_file, 'w', encoding='utf-8') as f:
                json.dump(existing_data, f, ensure_ascii=False, indent=2)
                
        except Exception as e:
            print(f"写入进度文件失败: {e}")

    async def report_task_progress_detail(self, current_page: int, total_items_found: int, items_processed: int, estimated_remaining_items: Optional[int] = None) -> None:
        """
        报告任务进度详细信息
        """
        progress_percentage = 0.0
        if estimated_remaining_items is not None and (total_items_found + estimated_remaining_items) > 0:
            progress_percentage = round((items_processed / (total_items_found + estimated_remaining_items)) * 100, 2)
        elif total_items_found > 0:
            # 如果没有预估剩余商品数，使用已找到的商品数作为参考
            progress_percentage = min(100.0, round((items_processed / total_items_found) * 100, 2))
        
        progress_data = {
            "timestamp": datetime.now().isoformat(),
            "task_id": self.task_id,
            "task_name": self.task_name,
            "progress_type": "task_progress_detail",
            "current_page": current_page,
            "total_items_found": total_items_found,
            "items_processed": items_processed,
            "estimated_remaining_items": estimated_remaining_items,
            "progress_percentage": progress_percentage
        }
        
        # 尝试通过WebSocket发送进度信息
        if _websocket_available:
            try:
                await websocket.broadcast_message("task_progress_detail", progress_data)
            except Exception as e:
                print(f"通过WebSocket发送详细进度信息失败: {e}")
        
        # 写入临时文件
        try:
            # 读取现有数据
            existing_data = []
            if os.path.exists(self.progress_file):
                with open(self.progress_file, 'r', encoding='utf-8') as f:
                    try:
                        existing_data = json.load(f)
                    except json.JSONDecodeError:
                        existing_data = []
            
            # 添加新数据
            existing_data.append(progress_data)
            
            # 写回文件
            with open(self.progress_file, 'w', encoding='utf-8') as f:
                json.dump(existing_data, f, ensure_ascii=False, indent=2)
                
        except Exception as e:
            print(f"写入进度文件失败: {e}")

    def cleanup(self):
        """清理临时文件"""
        try:
            if os.path.exists(self.progress_file):
                os.remove(self.progress_file)
        except Exception:
            pass

# 全局进度报告器实例
_progress_reporter: Optional[ProgressReporter] = None

def set_progress_reporter(reporter: ProgressReporter):
    """设置全局进度报告器"""
    global _progress_reporter
    _progress_reporter = reporter

def get_progress_reporter() -> Optional[ProgressReporter]:
    """获取全局进度报告器"""
    return _progress_reporter

async def report_progress(progress_type: str, message: str) -> None:
    """全局进度报告函数"""
    reporter = get_progress_reporter()
    if reporter:
        await reporter.report_progress(progress_type, message)
    else:
        # 如果没有设置报告器，直接写入日志并尝试通过WebSocket发送
        print(f"[{progress_type.upper()}] {message}")
        if _websocket_available:
            try:
                await websocket.broadcast_message("general_progress", {
                    "progress_type": progress_type,
                    "message": message,
                    "timestamp": datetime.now().isoformat()
                })
            except Exception as e:
                print(f"通过WebSocket发送通用进度信息失败: {e}")