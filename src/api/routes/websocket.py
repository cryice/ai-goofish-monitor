"""
WebSocket 路由
提供实时通信功能
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Body, HTTPException, Request
from typing import Set, Dict
import json


router = APIRouter()

# 全局 WebSocket 连接管理
active_connections: Set[WebSocket] = set()
# 任务进度连接管理
task_progress_connections: Dict[int, Set[WebSocket]] = {}


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
):
    """通用WebSocket端点（需要认证）"""
    # 从查询参数中获取 token
    query_params = websocket.query_params
    token = query_params.get("token")

    # 验证 token
    from src.api.dependencies import get_session_token
    current_session_token = get_session_token()

    print(f"[WS] 接收到 WebSocket 连接请求")
    print(f"[WS] 查询参数中的 token: {token[:20]}..." if token else "[WS] 查询参数中的 token: 无")
    print(f"[WS] 服务器当前会话 token: {current_session_token[:20]}..." if current_session_token else "[WS] 服务器当前会话 token: 无")

    # 检查 token 有效性
    if not token or current_session_token is None or token != current_session_token:
        print(f"[WS] ❌ 认证失败，拒绝连接")
        await websocket.close(code=1008, reason="无效或过期的认证令牌")
        return

    print(f"[WS] ✅ 认证成功，接受连接")
    # 接受连接
    await websocket.accept()
    active_connections.add(websocket)
    print(f"[WS] WebSocket 连接已建立")

    try:
        # 保持连接并接收消息
        while True:
            # 接收客户端消息（如果有的话）
            data = await websocket.receive_text()
            # 这里可以处理客户端发送的消息
            # 目前我们主要用于服务端推送，所以暂时不处理
    except WebSocketDisconnect:
        active_connections.remove(websocket)
        print(f"[WS] WebSocket 连接已断开")
    except Exception as e:
        print(f"WebSocket 错误: {e}")
        if websocket in active_connections:
            active_connections.remove(websocket)


@router.websocket("/ws/task-progress/{task_id}")
async def task_progress_websocket(
    websocket: WebSocket,
    task_id: int,
):
    """任务进度WebSocket端点（需要认证）"""
    # 从查询参数中获取 token
    query_params = websocket.query_params
    token = query_params.get("token")

    print(f"[WS] 接收到任务进度 WebSocket 连接请求: task_id={task_id}")
    print(f"[WS] 查询参数中的 token: {token[:20]}..." if token else "[WS] 查询参数中的 token: 无")

    if not token:
        print(f"[WS] ❌ 缺少认证令牌，拒绝连接")
        await websocket.close(code=1008, reason="缺少认证令牌")
        return

    # 验证 token
    from src.api.dependencies import get_session_token
    current_session_token = get_session_token()
    print(f"[WS] 服务器当前会话 token: {current_session_token[:20]}..." if current_session_token else "[WS] 服务器当前会话 token: 无")

    if current_session_token is None or token != current_session_token:
        print(f"[WS] ❌ 认证失败，拒绝连接")
        await websocket.close(code=1008, reason="无效或过期的认证令牌")
        return

    print(f"[WS] ✅ 认证成功，接受连接")

    print(f"\n[WS] ═══════════════════════════════════════════════════════════")
    print(f"[WS] 【连接建立】客户端尝试连接到任务进度 WebSocket: task_id={task_id}")
    print(f"[WS] ═══════════════════════════════════════════════════════════\n")

    # 接受连接
    await websocket.accept()
    print(f"[WS] ✅ 任务进度 WebSocket 连接已接受: task_id={task_id}")

    # 为特定任务ID添加连接
    if task_id not in task_progress_connections:
        task_progress_connections[task_id] = set()
        print(f"[WS] 创建任务进度连接集合: task_id={task_id}")

    task_progress_connections[task_id].add(websocket)
    print(f"[WS] 任务进度连接已添加: task_id={task_id}, 当前连接数={len(task_progress_connections[task_id])}")
    print(f"[WS] 📊 所有活跃任务连接: {list(task_progress_connections.keys())}\n")

    # 新连接时，立即发送当前最新进度（追赶机制）
    try:
        from src.infrastructure.persistence.sqlite_task_repository import SqliteTaskRepository
        repo = SqliteTaskRepository()
        print(f"[WS] 正在查询任务 {task_id} 的数据...")
        current_task = await repo.find_by_id(task_id)
        print(f"[WS] 查询结果: current_task={current_task}")

        if current_task:
            print(f"[WS] 为新连接的客户端发送当前进度: task_id={task_id}")
            initial_progress = {
                "type": "task_progress_update",
                "task_id": task_id,
                "data": {
                    "current_page": current_task.current_page or 0,
                    "total_items_found": current_task.total_items_found or 0,
                    "items_processed": current_task.items_processed or 0,
                    "estimated_remaining_items": current_task.estimated_remaining_items or 0,
                    "progress_percentage": current_task.progress_percentage or 0,
                    "last_crawl_time": current_task.last_crawl_time,
                }
            }
            print(f"[WS] 准备发送消息: {initial_progress}")
            await websocket.send_json(initial_progress)
            print(f"[WS] ✅ 初始进度已发送: task_id={task_id}, page={current_task.current_page}")
        else:
            print(f"[WS] ⚠️ 任务 {task_id} 不存在或未找到")
    except Exception as e:
        import traceback
        print(f"[WS] ❌ 发送初始进度失败: task_id={task_id}, 错误={e}")
        print(f"[WS] 错误堆栈:")
        traceback.print_exc()

    try:
        # 保持连接并接收消息
        while True:
            # 接收客户端消息（如果有的话）
            data = await websocket.receive_text()
            # 这里可以处理客户端发送的消息
            # 目前我们主要用于服务端推送，所以暂时不处理
    except WebSocketDisconnect:
        print(f"\n[WS] ═══════════════════════════════════════════════════════════")
        print(f"[WS] 【连接断开】任务进度 WebSocket 客户端断开连接: task_id={task_id}")
        print(f"[WS] ═══════════════════════════════════════════════════════════\n")
        task_progress_connections[task_id].discard(websocket)
        # 如果该任务没有连接了，清理字典项
        if not task_progress_connections[task_id]:
            del task_progress_connections[task_id]
            print(f"[WS] 🗑️  删除空的连接集合: task_id={task_id}\n")
        else:
            print(f"[WS] 📊 任务 {task_id} 剩余连接数: {len(task_progress_connections[task_id])}\n")
    except Exception as e:
        print(f"\n[WS] ⚠️  任务进度WebSocket错误: task_id={task_id}, 错误={e}\n")
        task_progress_connections[task_id].discard(websocket)
        # 如果该任务没有连接了，清理字典项
        if not task_progress_connections[task_id]:
            del task_progress_connections[task_id]
            print(f"[WS] 🗑️  删除空的连接集合: task_id={task_id}\n")
        else:
            print(f"[WS] 📊 任务 {task_id} 剩余连接数: {len(task_progress_connections[task_id])}\n")


async def broadcast_message(message_type: str, data: dict):
    """向所有连接的客户端广播消息"""
    message = {
        "type": message_type,
        "data": data
    }

    # 移除已断开的连接
    disconnected = set()

    for connection in active_connections:
        try:
            await connection.send_json(message)
        except Exception:
            disconnected.add(connection)

    # 清理断开的连接
    for connection in disconnected:
        active_connections.discard(connection)


async def broadcast_task_progress(task_id: int, progress_data: dict):
    """向订阅特定任务进度的客户端广播消息"""
    print(f"\n[WS] ❤️ broadcast_task_progress 被调用: task_id={task_id}")
    print(f"[WS] 📊 当前所有任务连接: {list(task_progress_connections.keys())}")
    print(f"[WS] 📋 进度数据: {progress_data}\n")

    message = {
        "type": "task_progress_update",
        "task_id": task_id,
        "data": progress_data
    }

    # 检查该任务是否有连接
    if task_id in task_progress_connections:
        print(f"[WS] ✅ 找到该任务的连接，共 {len(task_progress_connections[task_id])} 个")
        disconnected = set()

        for i, connection in enumerate(task_progress_connections[task_id]):
            try:
                print(f"[WS] 📤 正在发送消息到连接 {i+1}/{len(task_progress_connections[task_id])}")
                await connection.send_json(message)
                print(f"[WS] ✅ 消息已发送到连接 {i+1}/{len(task_progress_connections[task_id])}")
            except Exception as e:
                print(f"[WS] ❌ 发送消息失败: {e}")
                disconnected.add(connection)

        # 清理断开的连接
        for connection in disconnected:
            task_progress_connections[task_id].discard(connection)

        # 如果该任务没有连接了，清理字典项
        if not task_progress_connections[task_id]:
            del task_progress_connections[task_id]
    else:
        print(f"[WS] ⚠️ 任务 {task_id} 没有任何客户端连接！消息无法发送\n")


# 为子进程提供的 API 端点（内部使用）

@router.post("/api/internal/broadcast-task-progress/{task_id}")
async def broadcast_task_progress_api(task_id: int, request: Request):
    """
    内部 API：供爬虫子进程通过 HTTP 调用来推送进度

    这是为了解决子进程无法访问主进程全局变量的问题
    子进程（spider_v2.py）通过 HTTP POST 调用此端点来推送进度
    """
    import traceback
    try:
        progress_data = await request.json()
    except Exception as e:
        print(f"[API] ❌ 解析请求体失败: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=f"无法解析请求体: {e}")

    print(f"[API] 收到子进程的进度推送请求: task_id={task_id}")
    print(f"[API] progress_data: {progress_data}")

    try:
        await broadcast_task_progress(task_id, progress_data)
        return {"success": True, "task_id": task_id, "connections": len(task_progress_connections.get(task_id, set()))}
    except Exception as e:
        print(f"[API] ❌ 推送失败: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))