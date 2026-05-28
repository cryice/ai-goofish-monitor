"""
FastAPI 依赖注入
提供服务实例的创建和管理
"""
from fastapi import Depends, HTTPException, status, WebSocketException
import uuid

from src.services.task_service import TaskService
from src.services.notification_service import NotificationService, build_notification_service
from src.services.ai_service import AIAnalysisService
from src.services.process_service import ProcessService
from src.services.scheduler_service import SchedulerService
from src.services.task_generation_service import TaskGenerationService
from src.infrastructure.persistence.sqlite_task_repository import SqliteTaskRepository
from src.infrastructure.external.ai_client import AIClient


# 全局服务实例
_process_service_instance = None
_scheduler_service_instance = None
_task_generation_service_instance = None

# 会话管理（单用户系统简化版）
_session_token = None  # 存储当前登录用户的 token


def set_session_token(token: str):
    """设置当前会话 token"""
    global _session_token
    _session_token = token


def get_session_token() -> str:
    """获取当前会话 token"""
    return _session_token


def generate_session_token() -> str:
    """生成新的会话 token"""
    return str(uuid.uuid4())


# HTTP Bearer 认证方案
# 移除 HTTPBearer 的使用，改为在中间件和路由中手动处理


async def verify_token(auth_header: str = None) -> str:
    """
    验证请求中的 token

    从 Authorization header 中提取 token，验证是否有效
    如果无效或不存在，返回 401
    """
    if not auth_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少认证凭证",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        scheme, token = auth_header.split()
        if scheme.lower() != "bearer":
            raise ValueError("Invalid scheme")
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证凭证格式",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if _session_token is None or token != _session_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return token


async def verify_ws_token(token: str) -> str:
    """
    验证 WebSocket 连接中的 token

    从查询参数或 header 中提取 token
    """
    if not token or _session_token is None or token != _session_token:
        raise WebSocketException(code=1008, reason="认证失败")
    return token


# 全局 ProcessService 实例（将在 app.py 中设置）
_process_service_instance = None
_scheduler_service_instance = None
_task_generation_service_instance = None


def set_process_service(service: ProcessService):
    """设置全局 ProcessService 实例"""
    global _process_service_instance
    _process_service_instance = service


def set_scheduler_service(service: SchedulerService):
    """设置全局 SchedulerService 实例"""
    global _scheduler_service_instance
    _scheduler_service_instance = service


def set_task_generation_service(service: TaskGenerationService):
    """设置全局 TaskGenerationService 实例"""
    global _task_generation_service_instance
    _task_generation_service_instance = service


# 服务依赖注入
def get_task_service() -> TaskService:
    """获取任务管理服务实例"""
    repository = SqliteTaskRepository()
    return TaskService(repository)


def get_notification_service() -> NotificationService:
    """获取通知服务实例"""
    return build_notification_service()


def get_ai_service() -> AIAnalysisService:
    """获取AI分析服务实例"""
    ai_client = AIClient()
    return AIAnalysisService(ai_client)


def get_process_service() -> ProcessService:
    """获取进程管理服务实例"""
    if _process_service_instance is None:
        raise RuntimeError("ProcessService 未初始化")
    return _process_service_instance


def get_scheduler_service() -> SchedulerService:
    """获取调度服务实例"""
    if _scheduler_service_instance is None:
        raise RuntimeError("SchedulerService 未初始化")
    return _scheduler_service_instance


def get_task_generation_service() -> TaskGenerationService:
    """获取任务生成作业服务实例"""
    if _task_generation_service_instance is None:
        raise RuntimeError("TaskGenerationService 未初始化")
    return _task_generation_service_instance
