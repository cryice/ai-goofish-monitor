"""
新架构的主应用入口
整合所有路由和服务
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.api.routes import (
    dashboard,
    tasks,
    logs,
    settings,
    prompts,
    results,
    login_state,
    websocket,
    accounts,
)

# 导入广播任务进度函数
from src.api.routes.websocket import broadcast_task_progress
from src.api.dependencies import (
    set_process_service,
    set_scheduler_service,
    set_task_generation_service,
)
from src.services.task_service import TaskService
from src.services.process_service import ProcessService
from src.services.scheduler_service import SchedulerService
from src.services.task_log_cleanup_service import cleanup_task_logs
from src.services.task_generation_service import TaskGenerationService
from src.infrastructure.persistence.sqlite_bootstrap import bootstrap_sqlite_storage
from src.infrastructure.persistence.sqlite_task_repository import SqliteTaskRepository
from src.infrastructure.config.settings import settings as app_settings, database_settings
from src.infrastructure.persistence.database_factory import DatabaseFactory
from src.infrastructure.persistence.db_interface import DatabaseInterface


# 全局服务实例
process_service = ProcessService()
scheduler_service = SchedulerService(process_service)
task_generation_service = TaskGenerationService()


async def _sync_task_runtime_status(task_id: int, is_running: bool) -> None:
    from src.infrastructure.persistence.sqlite_task_progress_repository import SqliteTaskProgressRepository
    from src.infrastructure.persistence.sqlite_task_run_repository import SqliteTaskRunRepository

    run_repo = SqliteTaskRunRepository()
    task_service = TaskService(SqliteTaskRepository(), SqliteTaskProgressRepository())
    task = await task_service.get_task(task_id)
    if not task or task.is_running == is_running:
        return
    await task_service.update_task_status(task_id, is_running)
    await websocket.broadcast_message(
        "task_status_changed",
        {"id": task_id, "is_running": is_running},
    )

    # 为所有执行模式创建/完成执行记录
    execution_mode = getattr(task, "execution_mode", "periodic") or "periodic"

    if is_running:
        # 任务启动 → 创建一条 running 状态的执行记录
        try:
            await run_repo.create_run(task_id, execution_mode=execution_mode, batch_number=0)
            print(f"[Task Run] 为 {execution_mode} 模式任务创建 task_run 记录 (task_id={task_id})")
        except Exception as e:
            print(f"创建 task_run 记录失败 (task_id={task_id}): {e}")
    else:
        # 任务停止 → 结束最近一条 running 记录
        try:
            # 从任务主表读取最新进度作为本次统计
            finished_task = await task_service.get_task(task_id)
            pages = getattr(finished_task, "current_page", 0) or 0
            items_found = getattr(finished_task, "total_items_found", 0) or 0
            items_proc = getattr(finished_task, "items_processed", 0) or 0
            last_error = getattr(finished_task, "last_error", None)
            run_status = "failed" if last_error else "completed"
            await run_repo.finish_latest_run_for_task(
                task_id=task_id,
                status=run_status,
                error_message=last_error,
            )
            print(f"[Task Run] 完成 {execution_mode} 模式任务的 task_run 记录 (task_id={task_id})")

            # periodic 模式任务停止后重置 current_page 为 0
            if execution_mode == "periodic":
                try:
                    # 获取任务当前状态
                    current_task = await task_service.get_task(task_id)
                    if current_task:
                        # 使用 update_progress_only 保存其他进度字段，只改 current_page
                        await task_service.repository.update_progress_only(
                            task_id=task_id,
                            current_page=0,
                            total_items_found=current_task.total_items_found or 0,
                            items_processed=current_task.items_processed or 0,
                            estimated_remaining_items=current_task.estimated_remaining_items,
                            progress_percentage=current_task.progress_percentage or 0.0,
                            last_crawl_time=current_task.last_crawl_time,
                        )
                        print(f"[Periodic Reset] 重置任务 {task_id} 的 current_page 为 0")
                except Exception as reset_err:
                    print(f"[Periodic Reset] 重置 current_page 失败 (task_id={task_id}): {reset_err}")

        except Exception as e:
            print(f"结束 task_run 记录失败 (task_id={task_id}): {e}")


process_service.set_lifecycle_hooks(
    on_started=lambda task_id: _sync_task_runtime_status(task_id, True),
    on_stopped=lambda task_id: _sync_task_runtime_status(task_id, False),
)

# 设置全局 ProcessService 实例供依赖注入使用
set_process_service(process_service)
set_scheduler_service(scheduler_service)
set_task_generation_service(task_generation_service)

# ── 孤儿进程清理工具 ────────────────────────────────────────────────────────────
import os as _os
import signal as _signal
import glob as _glob

_SPIDER_PID_DIR = "logs/pids"


def _write_spider_pid(task_id: int, pid: int) -> None:
    """启动爬虫子进程后写入 PID 文件，供下次启动时清理孤儿使用。"""
    try:
        _os.makedirs(_SPIDER_PID_DIR, exist_ok=True)
        with open(f"{_SPIDER_PID_DIR}/task_{task_id}.pid", "w") as f:
            f.write(str(pid))
    except Exception as exc:
        print(f"[PID] 写入 PID 文件失败 (task_id={task_id}): {exc}")


def _remove_spider_pid(task_id: int) -> None:
    """爬虫子进程退出后删除对应 PID 文件。"""
    try:
        pid_file = f"{_SPIDER_PID_DIR}/task_{task_id}.pid"
        if _os.path.exists(pid_file):
            _os.remove(pid_file)
    except Exception:
        pass


def _kill_orphan_spider_processes() -> None:
    """服务启动时扫描残留的 PID 文件，终止仍在运行的孤儿爬虫进程。"""
    if not _os.path.isdir(_SPIDER_PID_DIR):
        return
    for pid_file in _glob.glob(f"{_SPIDER_PID_DIR}/task_*.pid"):
        try:
            with open(pid_file) as f:
                pid = int(f.read().strip())
            try:
                # 发送信号 0 检测进程是否存活
                _os.kill(pid, 0)
                # 进程存活 → 发 SIGTERM 终止整个进程组
                try:
                    import sys as _sys
                    if _sys.platform != "win32":
                        pgid = _os.getpgid(pid)
                        _os.killpg(pgid, _signal.SIGTERM)
                        print(f"[启动清理] 终止孤儿爬虫进程组 pgid={pgid} (pid={pid}, file={pid_file})")
                    else:
                        _os.kill(pid, _signal.SIGTERM)
                        print(f"[启动清理] 终止孤儿爬虫进程 pid={pid} (file={pid_file})")
                except (ProcessLookupError, PermissionError):
                    pass
            except (ProcessLookupError, PermissionError):
                # 进程已不存在，只需清理文件
                pass
        except Exception as exc:
            print(f"[启动清理] 处理 PID 文件 {pid_file} 时出错: {exc}")
        finally:
            try:
                _os.remove(pid_file)
            except Exception:
                pass


def _cleanup_spider_pid_files() -> None:
    """正常关闭时删除所有 PID 文件（stop_all 已发送信号，此处只清理文件）。"""
    if not _os.path.isdir(_SPIDER_PID_DIR):
        return
    for pid_file in _glob.glob(f"{_SPIDER_PID_DIR}/task_*.pid"):
        try:
            _os.remove(pid_file)
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时
    print("正在启动应用...")
    
    # 根据配置创建数据库实例
    database = DatabaseFactory.create_database(database_settings)
    database.init_schema()
    
    # 根据数据库类型决定是否使用SQLite引导程序
    if database_settings.is_sqlite():
        bootstrap_sqlite_storage()

    cleanup_task_logs(keep_days=app_settings.task_log_retention_days)

    # 启动时清理残留的孤儿爬虫进程（上次服务崩溃/强杀时未能正常 stop_all 的子进程）
    _kill_orphan_spider_processes()

    # 重置所有任务状态为停止
    # 注意：目前我们仍需要使用SqliteTaskRepository，因为还没有重构TaskService
    task_repo = SqliteTaskRepository()
    task_service = TaskService(task_repo)
    tasks_list = await task_service.get_all_tasks()

    for task in tasks_list:
        if task.is_running:
            await task_service.update_task_status(task.id, False)

    # 加载定时任务
    await scheduler_service.reload_jobs(tasks_list)
    scheduler_service.start()

    print("应用启动完成")

    yield

    # 关闭时
    print("正在关闭应用...")
    scheduler_service.stop()
    await process_service.stop_all()
    # 清理 PID 文件
    _cleanup_spider_pid_files()
    print("应用已关闭")


# 创建 FastAPI 应用
app = FastAPI(
    title="闲鱼智能监控机器人",
    description="基于AI的闲鱼商品监控系统",
    version="2.0.0",
    lifespan=lifespan
)


# 认证中间件 - 保护所有 /api/ 路由
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class AuthMiddleware(BaseHTTPMiddleware):
    """认证中间件 - 保护 API 端点"""

    async def dispatch(self, request, call_next):
        # 跳过不需要认证的路由
        public_paths = {"/auth/status", "/health", "/"}

        if request.url.path in public_paths:
            return await call_next(request)

        # 内部 API 不需要认证（子进程间通信）
        if request.url.path.startswith("/api/internal/"):
            return await call_next(request)

        # 检查 /api/ 路由是否有有效的 token
        if request.url.path.startswith("/api/"):
            from src.api.dependencies import get_session_token

            auth_header = request.headers.get("Authorization")
            if not auth_header:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "缺少认证凭证"},
                )

            # 提取 Bearer token
            try:
                scheme, token = auth_header.split()
                if scheme.lower() != "bearer":
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "无效的认证方案"},
                    )
            except ValueError:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "无效的认证凭证格式"},
                )

            # 验证 token
            current_session_token = get_session_token()
            if current_session_token is None or token != current_session_token:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "无效或过期的认证令牌"},
                )

        return await call_next(request)


# 添加认证中间件
app.add_middleware(AuthMiddleware)

# 挂载静态文件（必须在路由注册之前，且需要import os）
import os
app.mount("/static", StaticFiles(directory="static"), name="static")
if os.path.exists("dist"):
    app.mount("/assets", StaticFiles(directory="dist/assets"), name="assets")

# 注册路由（必须在静态文件mount之后，以保证API优先）
app.include_router(tasks.router)
app.include_router(dashboard.router)
app.include_router(logs.router)
app.include_router(settings.router)
app.include_router(prompts.router)
app.include_router(results.router)
app.include_router(login_state.router)
app.include_router(accounts.router)
app.include_router(websocket.router)

# 健康检查端点
@app.get("/health")
async def health_check():
    """健康检查（无需认证）"""
    return {"status": "healthy", "message": "服务正常运行"}


# 认证状态检查端点
from fastapi import Request, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/auth/status")
async def auth_status(payload: LoginRequest):
    """用户登录认证

    验证用户名和密码，成功后返回认证 token
    """
    from src.api.dependencies import set_session_token, generate_session_token

    print(f"[AUTH] 登录请求: username={payload.username}")
    if payload.username == app_settings.web_username and payload.password == app_settings.web_password:
        # 生成并设置会话 token
        token = generate_session_token()
        set_session_token(token)
        print(f"[AUTH] ✅ 登录成功，生成 token: {token[:20]}...")
        return {
            "authenticated": True,
            "username": payload.username,
            "token": token,
        }
    print(f"[AUTH] ❌ 登录失败: 用户名或密码错误")
    raise HTTPException(status_code=401, detail="认证失败")


@app.get("/auth/debug")
async def auth_debug():
    """调试端点：检查当前会话 token（无需认证）"""
    from src.api.dependencies import get_session_token
    current_token = get_session_token()
    return {
        "current_session_token": current_token[:20] + "..." if current_token else None,
        "has_session_token": current_token is not None,
    }


# 主页路由 - 服务 Vue 3 SPA
from fastapi.responses import JSONResponse

@app.get("/")
async def read_root(request: Request):
    """提供 Vue 3 SPA 的主页面"""
    if os.path.exists("dist/index.html"):
        return FileResponse("dist/index.html")
    else:
        return JSONResponse(
            status_code=500,
            content={"error": "前端构建产物不存在，请先运行 cd web-ui && npm run build"}
        )


# Catch-all 路由 - 处理所有前端路由（必须放在最后）
@app.get("/{full_path:path}")
async def serve_spa(request: Request, full_path: str):
    """
    Catch-all 路由，将所有非 API 请求重定向到 index.html
    这样可以支持 Vue Router 的 HTML5 History 模式
    """
    # 跳过 API 路径 - 尝试让 FastAPI 路由处理
    pass  # 让 FastAPI 自己处理

    # 如果请求的是静态资源（如 favicon.ico），返回 404
    if full_path.endswith(('.ico', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.css', '.js', '.json')):
        return JSONResponse(status_code=404, content={"error": "资源未找到"})

    # 其他所有路径都返回 index.html，让前端路由处理
    if os.path.exists("dist/index.html"):
        return FileResponse("dist/index.html")
    else:
        return JSONResponse(
            status_code=500,
            content={"error": "前端构建产物不存在，请先运行 cd web-ui && npm run build"}
        )


if __name__ == "__main__":
    import uvicorn
    from src.infrastructure.config.settings import settings

    print(f"启动新架构应用，端口: {app_settings.server_port}")
    uvicorn.run(app, host="0.0.0.0", port=app_settings.server_port)