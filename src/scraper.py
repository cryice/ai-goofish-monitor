import asyncio
import json
import os
import random
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

from playwright.async_api import (
    Response,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from src.ai_handler import (
    download_all_images,
    get_ai_analysis,
    send_ntfy_notification,
    cleanup_task_images,
)
from src.config import (
    AI_DEBUG_MODE,
    DETAIL_API_URL_PATTERN,
    LOGIN_IS_EDGE,
    RUN_HEADLESS,
    RUNNING_IN_DOCKER,
    SKIP_AI_ANALYSIS,
    STATE_FILE,
)
from src.parsers import (
    _parse_search_results_json,
    _parse_user_items_data,
    calculate_reputation_from_ratings,
    parse_ratings_data,
    parse_user_head_data,
)
from src.utils import (
    format_registration_days,
    get_link_unique_key,
    log_time,
    random_sleep,
    safe_get,
    save_to_jsonl,
)
from src.rotation import RotationPool, load_state_files, parse_proxy_pool, RotationItem
from src.failure_guard import FailureGuard
from src.services.account_strategy_service import resolve_account_runtime_plan
from src.infrastructure.persistence.storage_names import build_result_filename
from src.services.task_service import TaskService
from src.infrastructure.persistence.sqlite_task_repository import SqliteTaskRepository
from src.infrastructure.persistence.sqlite_task_run_repository import SqliteTaskRunRepository
from src.services.item_analysis_dispatcher import (
    ItemAnalysisDispatcher,
    ItemAnalysisJob,
)
from src.services.price_history_service import (
    build_market_reference,
    load_price_snapshots,
    record_market_snapshots,
)
from src.services.result_storage_service import load_processed_link_keys
from src.services.seller_profile_cache import SellerProfileCache
from src.services.search_pagination import (
    advance_search_page,
    is_search_results_response,
)


class RiskControlError(Exception):
    pass


class LoginRequiredError(Exception):
    """Raised when Goofish redirects to the passport/mini_login flow."""


FAILURE_GUARD = FailureGuard()
EDGE_DOCKER_WARNING_PRINTED = False


def _is_login_url(url: str) -> bool:
    if not url:
        return False
    lowered = url.lower()
    return "passport.goofish.com" in lowered or "mini_login" in lowered


def _resolve_browser_channel() -> str:
    global EDGE_DOCKER_WARNING_PRINTED
    if RUNNING_IN_DOCKER:
        if LOGIN_IS_EDGE and not EDGE_DOCKER_WARNING_PRINTED:
            print(
                "检测到 LOGIN_IS_EDGE=true，但 Docker 镜像未内置 Edge，"
                "任务运行时将改用 Chromium。"
            )
            EDGE_DOCKER_WARNING_PRINTED = True
        return "chromium"
    return "msedge" if LOGIN_IS_EDGE else "chrome"


def _should_analyze_images(task_config: dict) -> bool:
    raw_value = task_config.get("analyze_images", True)
    if isinstance(raw_value, bool):
        return raw_value
    return str(raw_value).strip().lower() not in {"false", "0", "no", "off"}


def _format_failure_reason(reason: str, limit: int = 500) -> str:
    if not reason:
        return "未知错误"
    cleaned = " ".join(str(reason).split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


async def _notify_task_failure(
    task_config: dict, reason: str, *, cookie_path: Optional[str]
) -> None:
    task_name = task_config.get("task_name", "未命名任务")
    keyword = task_config.get("keyword", "")
    formatted_reason = _format_failure_reason(reason)

    # Some failures are deterministic misconfiguration and should pause/notify immediately.
    pause_immediately = any(
        marker in formatted_reason
        for marker in (
            "未找到可用的代理地址",
            "未找到可用的登录状态文件",
        )
    )

    guard_result = FAILURE_GUARD.record_failure(
        task_name,
        formatted_reason,
        cookie_path=cookie_path,
        min_failures_to_pause=1 if pause_immediately else None,
    )

    if not guard_result.get("should_notify"):
        print(
            f"[FailureGuard] 任务 '{task_name}' 失败计数 {guard_result.get('consecutive_failures')}/{FAILURE_GUARD.threshold}，暂不通知。"
        )
        return

    paused_until = guard_result.get("paused_until")
    paused_until_str = (
        paused_until.strftime("%Y-%m-%d %H:%M:%S") if paused_until else "N/A"
    )

    product_data = {
        "商品标题": f"[任务异常] {task_name}",
        "当前售价": "N/A",
        "商品链接": "#",
    }
    notify_reason = (
        f"任务运行失败(已连续 {guard_result.get('consecutive_failures')}/{FAILURE_GUARD.threshold} 次): {formatted_reason}"
        f"\n任务: {task_name}"
        f"\n关键词: {keyword or 'N/A'}"
        f"\n已自动暂停重试，暂停到: {paused_until_str}"
        f"\n修复后(更新登录态/cookies文件)将自动恢复。"
    )

    try:
        await send_ntfy_notification(product_data, notify_reason)
    except Exception as e:
        print(f"发送任务异常通知失败: {e}")


def _as_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_int(value, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_rotation_settings(task_config: dict) -> dict:
    account_cfg = task_config.get("account_rotation") or {}
    proxy_cfg = task_config.get("proxy_rotation") or {}

    account_enabled = _as_bool(
        account_cfg.get("enabled"),
        _as_bool(os.getenv("ACCOUNT_ROTATION_ENABLED"), False),
    )
    account_mode = (
        account_cfg.get("mode") or os.getenv("ACCOUNT_ROTATION_MODE", "per_task")
    ).lower()
    account_state_dir = account_cfg.get("state_dir") or os.getenv(
        "ACCOUNT_STATE_DIR", "state"
    )
    account_retry_limit = _as_int(
        account_cfg.get("retry_limit"),
        _as_int(os.getenv("ACCOUNT_ROTATION_RETRY_LIMIT"), 2),
    )
    account_blacklist_ttl = _as_int(
        account_cfg.get("blacklist_ttl_sec"),
        _as_int(os.getenv("ACCOUNT_BLACKLIST_TTL"), 300),
    )

    proxy_enabled = _as_bool(
        proxy_cfg.get("enabled"), _as_bool(os.getenv("PROXY_ROTATION_ENABLED"), False)
    )
    proxy_mode = (
        proxy_cfg.get("mode") or os.getenv("PROXY_ROTATION_MODE", "per_task")
    ).lower()
    proxy_pool = proxy_cfg.get("proxy_pool") or os.getenv("PROXY_POOL", "")
    proxy_retry_limit = _as_int(
        proxy_cfg.get("retry_limit"),
        _as_int(os.getenv("PROXY_ROTATION_RETRY_LIMIT"), 2),
    )
    proxy_blacklist_ttl = _as_int(
        proxy_cfg.get("blacklist_ttl_sec"),
        _as_int(os.getenv("PROXY_BLACKLIST_TTL"), 300),
    )

    return {
        "account_enabled": account_enabled,
        "account_mode": account_mode,
        "account_state_dir": account_state_dir,
        "account_retry_limit": max(1, account_retry_limit),
        "account_blacklist_ttl": max(0, account_blacklist_ttl),
        "proxy_enabled": proxy_enabled,
        "proxy_mode": proxy_mode,
        "proxy_pool": proxy_pool,
        "proxy_retry_limit": max(1, proxy_retry_limit),
        "proxy_blacklist_ttl": max(0, proxy_blacklist_ttl),
    }


def _get_ai_analysis_concurrency(task_config: dict) -> int:
    configured = task_config.get("ai_analysis_concurrency")
    default = _as_int(os.getenv("AI_ANALYSIS_CONCURRENCY"), 2)
    return max(1, _as_int(configured, default))


def _get_seller_profile_cache_ttl(task_config: dict) -> int:
    configured = task_config.get("seller_profile_cache_ttl")
    default = _as_int(os.getenv("SELLER_PROFILE_CACHE_TTL"), 1800)
    return max(0, _as_int(configured, default))


def _default_context_options() -> dict:
    return {
        "user_agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
        "viewport": {"width": 412, "height": 915},
        "device_scale_factor": 2.625,
        "is_mobile": True,
        "has_touch": True,
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
        "permissions": ["geolocation"],
        "geolocation": {"longitude": 121.4737, "latitude": 31.2304},
        "color_scheme": "light",
    }


def _clean_kwargs(options: dict) -> dict:
    return {k: v for k, v in options.items() if v is not None}


def _looks_like_mobile(ua: str) -> Optional[bool]:
    if not ua:
        return None
    ua_lower = ua.lower()
    if "mobile" in ua_lower or "android" in ua_lower or "iphone" in ua_lower:
        return True
    if "windows" in ua_lower or "macintosh" in ua_lower:
        return False
    return None


def _build_context_overrides(snapshot: dict) -> dict:
    env = snapshot.get("env") or {}
    headers = snapshot.get("headers") or {}
    navigator = env.get("navigator") or {}
    screen = env.get("screen") or {}
    intl = env.get("intl") or {}

    overrides = {}

    ua = (
        headers.get("User-Agent")
        or headers.get("user-agent")
        or navigator.get("userAgent")
    )
    if ua:
        overrides["user_agent"] = ua

    accept_language = headers.get("Accept-Language") or headers.get("accept-language")
    locale = None
    if accept_language:
        locale = accept_language.split(",")[0].strip()
    elif navigator.get("language"):
        locale = navigator["language"]
    if locale:
        overrides["locale"] = locale

    tz = intl.get("timeZone")
    if tz:
        overrides["timezone_id"] = tz

    width = screen.get("width")
    height = screen.get("height")
    if isinstance(width, (int, float)) and isinstance(height, (int, float)):
        overrides["viewport"] = {"width": int(width), "height": int(height)}

    dpr = screen.get("devicePixelRatio")
    if isinstance(dpr, (int, float)):
        overrides["device_scale_factor"] = float(dpr)

    touch_points = navigator.get("maxTouchPoints")
    if isinstance(touch_points, (int, float)):
        overrides["has_touch"] = touch_points > 0

    mobile_flag = _looks_like_mobile(ua or "")
    if mobile_flag is not None:
        overrides["is_mobile"] = mobile_flag

    return _clean_kwargs(overrides)


def _build_extra_headers(raw_headers: Optional[dict]) -> dict:
    if not raw_headers:
        return {}
    excluded = {"cookie", "content-length"}
    headers = {}
    for key, value in raw_headers.items():
        if not key or key.lower() in excluded or value is None:
            continue
        headers[key] = value
    return headers


async def scrape_user_profile(context, user_id: str) -> dict:
    """
    【新版】访问指定用户的个人主页，按顺序采集其摘要信息、完整的商品列表和完整的评价列表。
    """
    print(f"   -> 开始采集用户ID: {user_id} 的完整信息...")
    profile_data = {}
    page = await context.new_page()

    # 为各项异步任务准备Future和数据容器
    head_api_future = asyncio.get_event_loop().create_future()

    all_items, all_ratings = [], []
    stop_item_scrolling, stop_rating_scrolling = asyncio.Event(), asyncio.Event()

    async def handle_response(response: Response):
        # 捕获头部摘要API
        if (
            "mtop.idle.web.user.page.head" in response.url
            and not head_api_future.done()
        ):
            try:
                head_api_future.set_result(await response.json())
                print(f"      [API捕获] 用户头部信息... 成功")
            except Exception as e:
                if not head_api_future.done():
                    head_api_future.set_exception(e)

        # 捕获商品列表API
        elif "mtop.idle.web.xyh.item.list" in response.url:
            try:
                data = await response.json()
                all_items.extend(data.get("data", {}).get("cardList", []))
                print(f"      [API捕获] 商品列表... 当前已捕获 {len(all_items)} 件")
                if not data.get("data", {}).get("nextPage", True):
                    stop_item_scrolling.set()
            except Exception as e:
                stop_item_scrolling.set()

        # 捕获评价列表API
        elif "mtop.idle.web.trade.rate.list" in response.url:
            try:
                data = await response.json()
                all_ratings.extend(data.get("data", {}).get("cardList", []))
                print(f"      [API捕获] 评价列表... 当前已捕获 {len(all_ratings)} 条")
                if not data.get("data", {}).get("nextPage", True):
                    stop_rating_scrolling.set()
            except Exception as e:
                stop_rating_scrolling.set()

    page.on("response", handle_response)

    try:
        # --- 任务1: 导航并采集头部信息 ---
        await page.goto(
            f"https://www.goofish.com/personal?userId={user_id}",
            wait_until="domcontentloaded",
            timeout=20000,
        )
        head_data = await asyncio.wait_for(head_api_future, timeout=15)
        profile_data = await parse_user_head_data(head_data)

        # --- 任务2: 滚动加载所有商品 (默认页面) ---
        print("      [采集阶段] 开始采集该用户的商品列表...")
        await random_sleep(2, 4)  # 等待第一页商品API完成
        while not stop_item_scrolling.is_set():
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            try:
                await asyncio.wait_for(stop_item_scrolling.wait(), timeout=8)
            except asyncio.TimeoutError:
                print("      [滚动超时] 商品列表可能已加载完毕。")
                break
        profile_data["卖家发布的商品列表"] = await _parse_user_items_data(all_items)

        # --- 任务3: 点击并采集所有评价 ---
        print("      [采集阶段] 开始采集该用户的评价列表...")
        rating_tab_locator = page.locator("//div[text()='信用及评价']/ancestor::li")
        if await rating_tab_locator.count() > 0:
            await rating_tab_locator.click()
            await random_sleep(3, 5)  # 等待第一页评价API完成

            while not stop_rating_scrolling.is_set():
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                try:
                    await asyncio.wait_for(stop_rating_scrolling.wait(), timeout=8)
                except asyncio.TimeoutError:
                    print("      [滚动超时] 评价列表可能已加载完毕。")
                    break

            profile_data["卖家收到的评价列表"] = await parse_ratings_data(all_ratings)
            reputation_stats = await calculate_reputation_from_ratings(all_ratings)
            profile_data.update(reputation_stats)
        else:
            print("      [警告] 未找到评价选项卡，跳过评价采集。")

    except Exception as e:
        print(f"   [错误] 采集用户 {user_id} 信息时发生错误: {e}")
    finally:
        page.remove_listener("response", handle_response)
        await page.close()
        print(f"   -> 用户 {user_id} 信息采集完成。")

    return profile_data


async def scrape_xianyu(task_config: dict, debug_limit: int = 0, progress_callback=None, task_id: int = None):
    """
    【核心执行器】
    根据单个任务配置，异步爬取闲鱼商品数据，并对每个新发现的商品进行实时的、独立的AI分析和通知。
    支持 continuous 模式：循环执行多个批次，每批次之间等待指定时间。
    """
    keyword = task_config["keyword"]
    max_pages = task_config.get("max_pages", 1)
    personal_only = task_config.get("personal_only", False)
    min_price = task_config.get("min_price")
    max_price = task_config.get("max_price")
    ai_prompt_text = task_config.get("ai_prompt_text", "")
    analyze_images = _should_analyze_images(task_config)
    decision_mode = str(task_config.get("decision_mode", "ai")).strip().lower()
    if decision_mode not in {"ai", "keyword"}:
        decision_mode = "ai"
    keyword_rules = task_config.get("keyword_rules") or []
    free_shipping = task_config.get("free_shipping", False)
    raw_new_publish = task_config.get("new_publish_option") or ""
    new_publish_option = raw_new_publish.strip()
    if new_publish_option == "__none__":
        new_publish_option = ""
    region_filter = (task_config.get("region") or "").strip()

    # 初始化：默认从第1页开始，后续如果能从数据库读取会被覆盖
    resume_from_page = 1

    # 从task_config读取配置（默认值为任务表定义的180/300）
    execution_mode = str(task_config.get("execution_mode", "periodic")).strip().lower()
    max_pages = int(task_config.get("max_pages", 1))
    sleep_interval_min = int(task_config.get("sleep_interval_min", 180))
    sleep_interval_max = int(task_config.get("sleep_interval_max", 300))
    max_page_limit = int(task_config.get("max_page_limit", 0)) if task_config.get("max_page_limit") else 0

    log_time(f"[任务配置] sleep_interval={sleep_interval_min}-{sleep_interval_max}s, max_pages={max_pages}, max_page_limit={max_page_limit}, execution_mode={execution_mode}")

    log_time(f"[断点续传] initial resume_from_page={resume_from_page}")

    # 如果提供了任务ID，初始化进度报告器
    if task_id is not None:
        from src.progress_reporter import set_progress_reporter, ProgressReporter
        reporter = ProgressReporter(task_id, task_config.get("task_name", "Untitled Task"))
        set_progress_reporter(reporter)

    from src.infrastructure.persistence.sqlite_task_progress_repository import SqliteTaskProgressRepository
    task_progress_service = (
        TaskService(SqliteTaskRepository(), SqliteTaskProgressRepository()) if task_id is not None else None
    )

    # 断点续传: 仅 continuous 模式支持跨次执行续传，periodic 模式每次从 start_page 开始
    if task_progress_service:
        try:
            # 先从数据库获取任务配置（包括start_page和current_page）
            sql_repo = SqliteTaskRepository()
            db_task = await sql_repo.find_by_id(task_id)

            # 获取起始页配置（用户配置的起始页）
            configured_start_page = db_task.start_page if db_task and db_task.start_page else 1

            if execution_mode == "continuous":
                # continuous 模式：从上次停的页码继续
                history_progress = await task_progress_service.get_task_progress(task_id)
                if history_progress and history_progress.current_page and history_progress.current_page >= configured_start_page:
                    resume_from_page = history_progress.current_page + 1
                    log_time(f"[断点续传] continuous模式，历史记录第 {history_progress.current_page} 页，继续从第 {resume_from_page} 页开始...")
                elif db_task and db_task.current_page and db_task.current_page > 0:
                    resume_from_page = db_task.current_page + 1
                    log_time(f"[断点续传] continuous模式，任务表记录第 {db_task.current_page} 页，从第 {resume_from_page} 页开始...")
                elif configured_start_page > 1:
                    resume_from_page = configured_start_page
                    log_time(f"[断点续传] continuous模式，使用配置的起始页 {configured_start_page}...")
                else:
                    log_time(f"[断点续传] continuous模式，无历史记录，从第1页开始...")
            else:
                # periodic 模式：每次执行从配置的起始页重新开始，不跨次续传
                resume_from_page = configured_start_page if configured_start_page > 1 else 1
                log_time(f"[断点续传] periodic模式，每次从第 {resume_from_page} 页重新开始...")
        except Exception as e:
            print(f"获取断点进度失败: {e}")
            resume_from_page = 1

    async def _record_task_progress(
        current_page: int,
        total_items_found: int,
        items_processed: int,
        estimated_remaining_items: Optional[int] = None,
    ) -> None:
        """
        记录任务进度。

        页码定义：
        - current_page: 已抓取的最后一个页面号（从 start_page 开始计数）
        - 例如：start_page=4, 已处理第 4、5、6 页，则 current_page=6
        - 下次 resume_from_page = current_page + 1 = 7
        """
        if task_progress_service is None or task_id is None:
            log_time(f"[进度跳过] task_id={task_id}, task_progress_service={task_progress_service is not None}")
            return
        try:
            # 计算已处理的页数（用于显示）
            pages_processed = current_page - resume_from_page + 1 if resume_from_page > 0 else current_page
            log_time(f"[进度记录] 当前页={current_page}, 已处理页数={pages_processed}, 发现商品={total_items_found}, 已处理={items_processed}")

            # 第一步：更新数据库中的任务进度
            await task_progress_service.update_task_progress(
                task_id,
                current_page,
                total_items_found,
                items_processed,
                estimated_remaining_items,
            )

            # 第二步：通过 HTTP 调用主进程的 API 来推送 WebSocket 消息
            # 这是为了解决子进程无法访问主进程全局变量的问题
            try:
                import httpx
                from datetime import datetime

                # 计算进度百分比
                total_expected = total_items_found + (estimated_remaining_items or 0)
                progress_percentage = (items_processed / total_expected * 100) if total_expected > 0 else 0.0

                progress_data = {
                    "task_id": task_id,
                    "current_page": current_page,
                    "total_items_found": total_items_found,
                    "items_processed": items_processed,
                    "estimated_remaining_items": estimated_remaining_items or 0,
                    "progress_percentage": round(progress_percentage, 2),
                    "last_crawl_time": datetime.now().isoformat(),
                    "status": "running"
                }

                # 通过 HTTP POST 调用主进程的 WebSocket 推送 API
                # 使用 127.0.0.1 而不是 localhost（某些环境中 localhost 解析可能有问题）
                server_host = os.getenv('SERVER_HOST', '127.0.0.1')
                server_port = os.getenv('SERVER_PORT', '8000')
                api_url = f"http://{server_host}:{server_port}/api/internal/broadcast-task-progress/{task_id}"

                log_time(f"[WebSocket推送] 准备发送请求到: {api_url}")

                async with httpx.AsyncClient(timeout=5) as client:
                    try:
                        response = await client.post(api_url, json=progress_data)
                        if response.status_code == 200:
                            log_time(f"[WebSocket推送] ✅ 成功推送进度更新 (status={response.status_code})")
                        else:
                            log_time(f"[WebSocket推送] ⚠️ 推送返回异常状态码 {response.status_code}")
                            log_time(f"[WebSocket推送] 响应内容: {response.text[:200]}")
                    except httpx.ConnectError as ce:
                        log_time(f"[WebSocket推送] ❌ 无法连接到 {server_host}:{server_port} - {ce}")
                    except httpx.TimeoutException as te:
                        log_time(f"[WebSocket推送] ⏱️ 请求超时: {te}")
                    except Exception as http_err:
                        log_time(f"[WebSocket推送] ❌ HTTP 错误: {http_err}")
            except Exception as ws_err:
                # WebSocket 推送失败不应该影响进度记录，只记录日志
                import traceback
                log_time(f"[WebSocket推送] ⚠️ 推送异常: {ws_err}")
                log_time(f"[WebSocket推送] 错误堆栈:\n{traceback.format_exc()}")

        except Exception as progress_err:
            import traceback
            print(f"[进度错误] 记录任务进度失败: {progress_err}")
            traceback.print_exc()  # 打印完整的错误堆栈

    processed_links = set()
    history_run_id = datetime.now().strftime("%Y%m%d%H%M%S")
    history_seen_item_ids: set[str] = set()
    historical_snapshots = load_price_snapshots(keyword)
    result_filename = build_result_filename(keyword)
    processed_links = load_processed_link_keys(keyword)
    if processed_links:
        print(f"LOG: 发现已存在结果集 {result_filename}，已加载 {len(processed_links)} 个历史商品用于去重。")
    else:
        print(f"LOG: 结果集 {result_filename} 当前为空，将写入新记录。")

    rotation_settings = _get_rotation_settings(task_config)
    account_items = load_state_files(rotation_settings["account_state_dir"])
    runtime_plan = resolve_account_runtime_plan(
        strategy=task_config.get("account_strategy"),
        account_state_file=task_config.get("account_state_file"),
        has_root_state_file=os.path.exists(STATE_FILE),
        available_account_files=account_items,
    )
    forced_account = runtime_plan["forced_account"]
    if runtime_plan["prefer_root_state"]:
        account_items = [STATE_FILE]
        rotation_settings["account_enabled"] = False
    elif runtime_plan["use_account_pool"]:
        rotation_settings["account_enabled"] = True
    else:
        rotation_settings["account_enabled"] = False

    account_pool = RotationPool(
        account_items, rotation_settings["account_blacklist_ttl"], "account"
    )
    proxy_pool = RotationPool(
        parse_proxy_pool(rotation_settings["proxy_pool"]),
        rotation_settings["proxy_blacklist_ttl"],
        "proxy",
    )

    selected_account: Optional[RotationItem] = None
    selected_proxy: Optional[RotationItem] = None

    def _select_account(force_new: bool = False) -> Optional[RotationItem]:
        nonlocal selected_account
        if forced_account:
            return RotationItem(value=forced_account)
        if not rotation_settings["account_enabled"]:
            if os.path.exists(STATE_FILE):
                return RotationItem(value=STATE_FILE)
            return None
        if (
            rotation_settings["account_mode"] == "per_task"
            and selected_account
            and not force_new
        ):
            return selected_account
        picked = account_pool.pick_random()
        return picked or selected_account

    def _select_proxy(force_new: bool = False) -> Optional[RotationItem]:
        nonlocal selected_proxy
        if not rotation_settings["proxy_enabled"]:
            return None
        if (
            rotation_settings["proxy_mode"] == "per_task"
            and selected_proxy
            and not force_new
        ):
            return selected_proxy
        picked = proxy_pool.pick_random()
        return picked or selected_proxy

    async def _run_scrape_attempt(state_file: str, proxy_server: Optional[str]) -> int:
        processed_item_count = 0
        progress_total_items_found = 0
        progress_items_processed = 0
        stop_scraping = False

        # 不要在这里写入0，会覆盖之前的进度
        # await _record_task_progress(0, 0, 0, None)

        if not os.path.exists(state_file):
            raise FileNotFoundError(f"登录状态文件不存在: {state_file}")

        snapshot_data = None
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                snapshot_data = json.load(f)
        except Exception as e:
            print(f"警告：读取登录状态文件失败，将直接按路径使用: {e}")

        async with async_playwright() as p:
            # 反检测启动参数
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ]

            launch_kwargs = {"headless": RUN_HEADLESS, "args": launch_args}
            if proxy_server:
                launch_kwargs["proxy"] = {"server": proxy_server}

            launch_kwargs["channel"] = _resolve_browser_channel()

            browser = await p.chromium.launch(**launch_kwargs)

            context_kwargs = _default_context_options()
            storage_state_arg = state_file
            analysis_dispatcher: Optional[ItemAnalysisDispatcher] = None

            if isinstance(snapshot_data, dict):
                # 新版扩展导出的增强快照，包含环境和Header
                if any(
                    key in snapshot_data
                    for key in ("env", "headers", "page", "storage")
                ):
                    print(f"检测到增强浏览器快照，应用环境参数: {state_file}")
                    storage_state_arg = {"cookies": snapshot_data.get("cookies", [])}
                    context_kwargs.update(_build_context_overrides(snapshot_data))
                    extra_headers = _build_extra_headers(snapshot_data.get("headers"))
                    if extra_headers:
                        context_kwargs["extra_http_headers"] = extra_headers
                else:
                    storage_state_arg = snapshot_data

            context_kwargs = _clean_kwargs(context_kwargs)
            context = await browser.new_context(
                storage_state=storage_state_arg, **context_kwargs
            )
            seller_profile_cache = SellerProfileCache(
                ttl_seconds=_get_seller_profile_cache_ttl(task_config)
            )
            analysis_dispatcher = ItemAnalysisDispatcher(
                concurrency=_get_ai_analysis_concurrency(task_config),
                skip_ai_analysis=SKIP_AI_ANALYSIS,
                seller_loader=lambda user_id: seller_profile_cache.get_or_load(
                    str(user_id),
                    lambda seller_key: scrape_user_profile(context, seller_key),
                ),
                image_downloader=download_all_images,
                ai_analyzer=get_ai_analysis,
                notifier=send_ntfy_notification,
                saver=save_to_jsonl,
            )

            # 增强反检测脚本（模拟真实移动设备）
            await context.add_init_script("""
                // 移除webdriver标识
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

                // 模拟真实移动设备的navigator属性
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en-US', 'en']});

                // 添加chrome对象
                window.chrome = {runtime: {}, loadTimes: function() {}, csi: function() {}};

                // 模拟触摸支持
                Object.defineProperty(navigator, 'maxTouchPoints', {get: () => 5});

                // 覆盖permissions查询（避免暴露自动化）
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({state: Notification.permission}) :
                        originalQuery(parameters)
                );
            """)

            page = await context.new_page()

            try:
                # 步骤 0 - 模拟真实用户：先访问首页（重要的反检测措施）
                log_time("步骤 0 - 模拟真实用户访问首页...")
                await page.goto(
                    "https://www.goofish.com/",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                log_time("[反爬] 在首页停留，模拟浏览...")
                await random_sleep(1, 2)

                # 模拟随机滚动（移动设备的触摸滚动）
                await page.evaluate("window.scrollBy(0, Math.random() * 500 + 200)")
                await random_sleep(1, 2)

                log_time("步骤 1 - 导航到搜索结果页...")
                # 使用 'q' 参数构建正确的搜索URL，并进行URL编码
                params = {"q": keyword}
                search_url = f"https://www.goofish.com/search?{urlencode(params)}"
                log_time(f"目标URL: {search_url}")

                # 先监听搜索接口响应，再执行导航，避免错过首次请求
                async with page.expect_response(
                    is_search_results_response, timeout=30000
                ) as initial_response_info:
                    await page.goto(
                        search_url, wait_until="domcontentloaded", timeout=60000
                    )
                if _is_login_url(page.url):
                    raise LoginRequiredError(
                        f"Login required: redirected to {page.url} (cookies/state likely expired)"
                    )

                # 捕获初始搜索的API数据
                initial_response = await initial_response_info.value

                # 等待页面加载出关键筛选元素，以确认已成功进入搜索结果页
                try:
                    await page.wait_for_selector("text=新发布", timeout=15000)
                except PlaywrightTimeoutError as e:
                    if _is_login_url(page.url):
                        raise LoginRequiredError(
                            f"Login required: redirected to {page.url} (cookies/state likely expired)"
                        ) from e
                    raise

                # 模拟真实用户行为：页面加载后的初始停留和浏览
                log_time("[反爬] 模拟用户查看页面...")
                await random_sleep(1, 3)

                # --- 新增：检查是否存在验证弹窗 ---
                baxia_dialog = page.locator("div.baxia-dialog-mask")
                middleware_widget = page.locator("div.J_MIDDLEWARE_FRAME_WIDGET")
                try:
                    # 等待弹窗在2秒内出现。如果出现，则执行块内代码。
                    await baxia_dialog.wait_for(state="visible", timeout=2000)
                    print(
                        "\n==================== CRITICAL BLOCK DETECTED ===================="
                    )
                    print("检测到闲鱼反爬虫验证弹窗 (baxia-dialog)，无法继续操作。")
                    print("这通常是因为操作过于频繁或被识别为机器人。")
                    print("建议：")
                    print("1. 停止脚本一段时间再试。")
                    print(
                        "2. (推荐) 在 .env 文件中设置 RUN_HEADLESS=false，以非无头模式运行，这有助于绕过检测。"
                    )
                    print(f"任务 '{keyword}' 将在此处中止。")
                    print(
                        "==================================================================="
                    )
                    raise RiskControlError("baxia-dialog")
                except PlaywrightTimeoutError:
                    # 2秒内弹窗未出现，这是正常情况，继续执行
                    pass

                # 检查是否有J_MIDDLEWARE_FRAME_WIDGET覆盖层
                try:
                    await middleware_widget.wait_for(state="visible", timeout=2000)
                    print(
                        "\n==================== CRITICAL BLOCK DETECTED ===================="
                    )
                    print(
                        "检测到闲鱼反爬虫验证弹窗 (J_MIDDLEWARE_FRAME_WIDGET)，无法继续操作。"
                    )
                    print("这通常是因为操作过于频繁或被识别为机器人。")
                    print("建议：")
                    print("1. 停止脚本一段时间再试。")
                    print("2. (推荐) 更新登录状态文件，确保登录状态有效。")
                    print("3. 降低任务执行频率，避免被识别为机器人。")
                    print(f"任务 '{keyword}' 将在此处中止。")
                    print(
                        "==================================================================="
                    )
                    raise RiskControlError("J_MIDDLEWARE_FRAME_WIDGET")
                except PlaywrightTimeoutError:
                    # 2秒内弹窗未出现，这是正常情况，继续执行
                    pass
                # --- 结束新增 ---

                try:
                    await page.click("div[class*='closeIconBg']", timeout=3000)
                    print("LOG: 已关闭广告弹窗。")
                except PlaywrightTimeoutError:
                    print("LOG: 未检测到广告弹窗。")

                final_response = None
                log_time("步骤 2 - 应用筛选条件...")
                if new_publish_option:
                    try:
                        await page.click("text=新发布")
                        await random_sleep(1, 2)  # 原来是 (1.5, 2.5)
                        async with page.expect_response(
                            is_search_results_response, timeout=20000
                        ) as response_info:
                            await page.click(f"text={new_publish_option}")
                            # --- 修改: 增加排序后的等待时间 ---
                            await random_sleep(2, 4)  # 原来是 (3, 5)
                        final_response = await response_info.value
                    except PlaywrightTimeoutError:
                        log_time(
                            f"新发布筛选 '{new_publish_option}' 请求超时，继续执行。"
                        )
                    except Exception as e:
                        print(f"LOG: 应用新发布筛选失败: {e}")

                if personal_only:
                    async with page.expect_response(
                        is_search_results_response, timeout=20000
                    ) as response_info:
                        await page.click("text=个人闲置")
                        # --- 修改: 将固定等待改为随机等待，并加长 ---
                        await random_sleep(2, 4)  # 原来是 asyncio.sleep(5)
                    final_response = await response_info.value

                if free_shipping:
                    try:
                        async with page.expect_response(
                            is_search_results_response, timeout=20000
                        ) as response_info:
                            await page.click("text=包邮")
                            await random_sleep(2, 4)
                        final_response = await response_info.value
                    except PlaywrightTimeoutError:
                        log_time("包邮筛选请求超时，继续执行。")
                    except Exception as e:
                        print(f"LOG: 应用包邮筛选失败: {e}")

                if region_filter:
                    try:
                        area_trigger = page.get_by_text("区域", exact=True)
                        if await area_trigger.count():
                            await area_trigger.first.click()
                            await random_sleep(1.5, 2)
                            popover_candidates = page.locator("div.ant-popover")
                            popover = popover_candidates.filter(
                                has=page.locator(
                                    ".areaWrap--FaZHsn8E, [class*='areaWrap']"
                                )
                            ).last
                            if not await popover.count():
                                popover = popover_candidates.filter(
                                    has=page.get_by_text("重新定位")
                                ).last
                            if not await popover.count():
                                popover = popover_candidates.filter(
                                    has=page.get_by_text("查看")
                                ).last
                            if not await popover.count():
                                print("LOG: 未找到区域弹窗，跳过区域筛选。")
                                raise PlaywrightTimeoutError("region-popover-not-found")
                            await popover.wait_for(state="visible", timeout=5000)

                            # 列表容器：第一层 children 即省/市/区三列，不再强依赖具体类名，提升鲁棒性
                            area_wrap = popover.locator(
                                ".areaWrap--FaZHsn8E, [class*='areaWrap']"
                            ).first
                            await area_wrap.wait_for(state="visible", timeout=3000)
                            columns = area_wrap.locator(":scope > div")
                            col_prov = columns.nth(0)
                            col_city = columns.nth(1)
                            col_dist = columns.nth(2)

                            region_parts = [
                                p.strip() for p in region_filter.split("/") if p.strip()
                            ]

                            async def _click_in_column(
                                column_locator, text_value: str, desc: str
                            ) -> None:
                                option = column_locator.locator(
                                    ".provItem--QAdOx8nD", has_text=text_value
                                ).first
                                if await option.count():
                                    await option.click()
                                    await random_sleep(1.5, 2)
                                    try:
                                        await option.wait_for(
                                            state="attached", timeout=1500
                                        )
                                        await option.wait_for(
                                            state="visible", timeout=1500
                                        )
                                    except PlaywrightTimeoutError:
                                        pass
                                else:
                                    print(f"LOG: 未找到{desc} '{text_value}'，跳过。")

                            if len(region_parts) >= 1:
                                await _click_in_column(
                                    col_prov, region_parts[0], "省份"
                                )
                                await random_sleep(1, 2)
                            if len(region_parts) >= 2:
                                await _click_in_column(
                                    col_city, region_parts[1], "城市"
                                )
                                await random_sleep(1, 2)
                            if len(region_parts) >= 3:
                                await _click_in_column(
                                    col_dist, region_parts[2], "区/县"
                                )
                                await random_sleep(1, 2)

                            search_btn = popover.locator(
                                "div.searchBtn--Ic6RKcAb"
                            ).first
                            if await search_btn.count():
                                try:
                                    async with page.expect_response(
                                        is_search_results_response,
                                        timeout=20000,
                                    ) as response_info:
                                        await search_btn.click()
                                        await random_sleep(2, 3)
                                    final_response = await response_info.value
                                except PlaywrightTimeoutError:
                                    log_time("区域筛选提交超时，继续执行。")
                            else:
                                print(
                                    "LOG: 未找到区域弹窗的“查看XX件宝贝”按钮，跳过提交。"
                                )
                        else:
                            print("LOG: 未找到区域筛选触发器。")
                    except PlaywrightTimeoutError:
                        log_time(f"区域筛选 '{region_filter}' 请求超时，继续执行。")
                    except Exception as e:
                        print(f"LOG: 应用区域筛选 '{region_filter}' 失败: {e}")

                if min_price or max_price:
                    price_container = page.locator(
                        'div[class*="search-price-input-container"]'
                    ).first
                    if await price_container.is_visible():
                        if min_price:
                            await price_container.get_by_placeholder("¥").first.fill(
                                min_price
                            )
                            # --- 修改: 将固定等待改为随机等待 ---
                            await random_sleep(1, 2.5)  # 原来是 asyncio.sleep(5)
                        if max_price:
                            await (
                                price_container.get_by_placeholder("¥")
                                .nth(1)
                                .fill(max_price)
                            )
                            # --- 修改: 将固定等待改为随机等待 ---
                            await random_sleep(1, 2.5)  # 原来是 asyncio.sleep(5)

                        async with page.expect_response(
                            is_search_results_response, timeout=20000
                        ) as response_info:
                            await page.keyboard.press("Tab")
                            # --- 修改: 增加确认价格后的等待时间 ---
                            await random_sleep(2, 4)  # 原来是 asyncio.sleep(5)
                        final_response = await response_info.value
                    else:
                        print("LOG: 警告 - 未找到价格输入容器。")

                log_time("所有筛选已完成，开始处理商品列表...")

                current_response = (
                    final_response
                    if final_response and final_response.ok
                    else initial_response
                )
                # 断点续传: 从上次停的页码开始
                resume_offset = resume_from_page - 1
                start_page = 1 + resume_offset
                end_page = max_pages + resume_offset

                # 如果需要跳过前面的页，直接翻到起始页
                # 注意: advance_search_page 的 page_num 只用于日志，不控制目标页码
                # 翻页循环只负责让浏览器前进，不能修改 start_page/end_page
                if resume_from_page > 1:
                    log_time(f"[断点] 需要跳到第 {resume_from_page} 页，先翻页...")
                    for nav_step in range(resume_offset):
                        page_advance_result = await advance_search_page(
                            page=page, page_num=nav_step + 2,  # 仅用于日志: 2,3,4...
                        )
                        if not page_advance_result.advanced:
                            log_time("翻页失败，从头开始")
                            start_page = 1
                            end_page = max_pages
                            break
                        current_response = page_advance_result.response
                    else:
                        log_time(f"[断点] 已成功翻到第 {resume_from_page} 页，开始处理...")

                for page_num in range(start_page, end_page + 1):
                    if stop_scraping:
                        break
                    log_time(f"开始处理第 {page_num}/{end_page} 页 ...")

                    if page_num > 1:
                        page_advance_result = await advance_search_page(
                            page=page,
                            page_num=page_num,
                        )
                        if not page_advance_result.advanced:
                            # 翻页失败处理
                            log_time(f"❌ 无法翻页到第 {page_num} 页")

                            # 对于 continuous 模式，翻页失败可能表示已到最后一页或网络问题
                            if execution_mode == "continuous":
                                # 如果已处理的页数接近或达到 max_page_limit，视为正常完成
                                if max_page_limit > 0 and total_pages_processed >= max_page_limit - 1:
                                    log_time(f"[Continuous] 已接近最大页数限制，视为完成")
                                    break
                                else:
                                    # 记录更清晰的错误信息
                                    error_msg = (
                                        f"已处理{total_pages_processed}页，"
                                        f"当前商品查询结果仅有{total_pages_processed}页，"
                                        f"无法翻页至第{page_num}页，"
                                        f"请人工核对商品页数或网络连接"
                                    )
                                    if max_page_limit > 0:
                                        error_msg += f"（设置限制：{max_page_limit}页）"
                                    last_error = error_msg
                                    log_time(f"[错误] {last_error}")
                                    break
                            else:
                                # periodic 模式：从头开始
                                log_time("翻页失败，从头开始")
                                start_page = 1
                                end_page = max_pages
                                break
                        current_response = page_advance_result.response

                    if not (current_response and current_response.ok):
                        log_time(f"第 {page_num} 页响应无效，跳过。")
                        continue

                    basic_items = await _parse_search_results_json(
                        await current_response.json(), f"第 {page_num} 页"
                    )
                    if not basic_items:
                        break
                    historical_snapshots.extend(
                        record_market_snapshots(
                            keyword=keyword,
                            task_name=task_config.get("task_name", "Untitled Task"),
                            items=basic_items,
                            run_id=history_run_id,
                            snapshot_time=datetime.now().isoformat(),
                            seen_item_ids=history_seen_item_ids,
                        )
                    )

                    total_items_on_page = len(basic_items)
                    progress_total_items_found += total_items_on_page

                    # 计算剩余页数和剩余商品数
                    remaining_pages = max_page_limit - page_num if max_page_limit > 0 else max_pages - page_num
                    estimated_remaining_items = max(0, remaining_pages * total_items_on_page)

                    await _record_task_progress(
                        page_num,
                        progress_total_items_found,
                        progress_items_processed,
                        estimated_remaining_items,
                    )
                    for i, item_data in enumerate(basic_items, 1):
                        if debug_limit > 0 and processed_item_count >= debug_limit:
                            log_time(
                                f"已达到调试上限 ({debug_limit})，停止获取新商品。"
                            )
                            # 立即写入进度记录
                            if task_progress_service and task_id and page_num > 0:
                                try:
                                    await task_progress_service.update_task_progress(
                                        task_id,
                                        page_num,
                                        progress_total_items_found,
                                        progress_items_processed,
                                        estimated_remaining_items,
                                    )
                                    log_time(f"[调试上限进度记录] page={page_num}, items={progress_items_processed}")
                                except Exception as e:
                                    print(f"记录进度失败: {e}")
                            stop_scraping = True
                            break

                        progress_items_processed += 1
                        remaining_in_batch = max_pages - page_num
                        estimated_remaining_items = max(
                            0,
                            (total_items_on_page - i) + remaining_in_batch * total_items_on_page,
                        )

                        unique_key = get_link_unique_key(item_data["商品链接"])
                        if unique_key in processed_links:
                            log_time(
                                f"[页内进度 {i}/{total_items_on_page}] 商品 '{item_data['商品标题'][:20]}...' 已存在，跳过。"
                            )
                            await _record_task_progress(
                                page_num,
                                progress_total_items_found,
                                progress_items_processed,
                                estimated_remaining_items,
                            )
                            continue

                        log_time(
                            f"[页内进度 {i}/{total_items_on_page}] 发现新商品，获取详情: {item_data['商品标题'][:30]}..."
                        )
                        # --- 修改: 访问详情页前的等待时间，模拟用户在列表页上看了一会儿 ---
                        await random_sleep(2, 4)  # 原来是 (2, 4)

                        detail_page = await context.new_page()
                        try:
                            async with detail_page.expect_response(
                                lambda r: DETAIL_API_URL_PATTERN in r.url, timeout=25000
                            ) as detail_info:
                                await detail_page.goto(
                                    item_data["商品链接"],
                                    wait_until="domcontentloaded",
                                    timeout=25000,
                                )

                            detail_response = await detail_info.value
                            if detail_response.ok:
                                detail_json = await detail_response.json()

                                ret_string = str(
                                    await safe_get(detail_json, "ret", default=[])
                                )
                                if "FAIL_SYS_USER_VALIDATE" in ret_string:
                                    print(
                                        "\n==================== CRITICAL BLOCK DETECTED ===================="
                                    )
                                    print(
                                        "检测到闲鱼反爬虫验证 (FAIL_SYS_USER_VALIDATE)，程序将终止。"
                                    )
                                    long_sleep_duration = random.randint(3, 60)
                                    print(
                                        f"为避免账户风险，将执行一次长时间休眠 ({long_sleep_duration} 秒) 后再退出..."
                                    )
                                    await asyncio.sleep(long_sleep_duration)
                                    print("长时间休眠结束，现在将安全退出。")
                                    print(
                                        "==================================================================="
                                    )
                                    raise RiskControlError("FAIL_SYS_USER_VALIDATE")

                                # 解析商品详情数据并更新 item_data
                                item_do = await safe_get(
                                    detail_json, "data", "itemDO", default={}
                                )
                                seller_do = await safe_get(
                                    detail_json, "data", "sellerDO", default={}
                                )

                                reg_days_raw = await safe_get(
                                    seller_do, "userRegDay", default=0
                                )
                                registration_duration_text = format_registration_days(
                                    reg_days_raw
                                )

                                # --- START: 新增代码块 ---

                                # 从商品详情 API 中提取 SKU 信息
                                import re as re_module
                                try:
                                    # 打印一些关键的 API 数据字段供调试
                                    data_obj = detail_json.get("data", {})
                                    top_level_keys = list(data_obj.keys())

                                    # 尝试从 b2cItemDO 或 itemDO 中获取价格
                                    for item_key in ["b2cItemDO", "itemDO"]:
                                        item_info = data_obj.get(item_key, {})
                                        if item_info:
                                            # 尝试多种可能的价格字段
                                            for price_key in ["originalPrice", "price", "currentPrice", "showPrice", "priceInfo", "priceWap", "lowPrice", "highPrice", "priceText"]:
                                                main_price = item_info.get(price_key)
                                                if main_price and str(main_price) != "0" and str(main_price) != "":
                                                    item_data["_extracted_price"] = str(main_price)
                                                    print(f"      从 {item_key}.{price_key} 提取主价格: {main_price}")
                                                    break
                                            # 也尝试从 picDetailDO 获取价格
                                    pic_info = data_obj.get("picDetailDO", {})
                                    if pic_info and not item_data.get("_extracted_price"):
                                        for price_key in ["price", "originalPrice", "currentPrice"]:
                                            price_val = pic_info.get(price_key)
                                            if price_val and str(price_val) != "0":
                                                item_data["_extracted_price"] = str(price_val)
                                                print(f"      从 picDetailDO.{price_key} 提取主价格: {price_val}")
                                                break

                                    if not item_data.get("_extracted_price"):
                                        print(f"      b2cItemDO 字段: {list(item_info.keys())[:8]}")
                                except Exception as sku_api_err:
                                    print(f"      API SKU 提取失败: {sku_api_err}")

                                # 1. 提取卖家的芝麻信用信息
                                zhima_credit_text = await safe_get(
                                    seller_do, "zhimaLevelInfo", "levelName"
                                )

                                # 2. 提取该商品的完整图片列表
                                image_infos = await safe_get(
                                    item_do, "imageInfos", default=[]
                                )
                                if image_infos:
                                    # 使用列表推导式获取所有有效的图片URL
                                    all_image_urls = [
                                        img.get("url")
                                        for img in image_infos
                                        if img.get("url")
                                    ]
                                    if all_image_urls:
                                        # 用新的字段存储图片列表，替换掉旧的单个链接
                                        item_data["商品图片列表"] = all_image_urls
                                        # (可选) 仍然保留主图链接，以防万一
                                        item_data["商品主图链接"] = all_image_urls[0]

                                # --- END: 新增代码块 ---

                                # 把从 API 提取的主价格合并到商品信息中
                                extracted_price = item_data.pop("_extracted_price", None)
                                if extracted_price and extracted_price != "0":
                                    # 确保当前售价也有值
                                    if not item_data.get("当前售价"):
                                        item_data["当前售价"] = extracted_price
                                    # 把主价格添加到 SKU 列表中
                                    sku_basic = None
                                    if item_data.get("SKU列表") and len(item_data["SKU列表"]) > 0:
                                        # 已经有 SKU 信息，给每个添加价格
                                        for sku in item_data["SKU列表"]:
                                            if not sku.get("sku_price"):
                                                sku["sku_price"] = extracted_price
                                    else:
                                        # 没有 SKU，创建默认的一个
                                        item_data["SKU列表"] = [{
                                            "sku_name": "默认",
                                            "sku_price": extracted_price,
                                            "sku_type": "api_main_price"
                                        }]
                                elif extracted_price:
                                    # 即使价格是 0 也设置（虽然是免费的）
                                    if not item_data.get("当前售价"):
                                        item_data["当前售价"] = extracted_price

                                item_data["“想要”人数"] = await safe_get(
                                    item_do,
                                    "wantCnt",
                                    default=item_data.get("“想要”人数", "NaN"),
                                )
                                item_data["浏览量"] = await safe_get(
                                    item_do, "browseCnt", default="-"
                                )
                                # ...[此处可添加更多从详情页解析出的商品信息]...

                                user_id = await safe_get(seller_do, "sellerId")

                                # 构建基础记录
                                final_record = {
                                    "爬取时间": datetime.now().isoformat(),
                                    "搜索关键字": keyword,
                                    "任务名称": task_config.get(
                                        "task_name", "Untitled Task"
                                    ),
                                    "商品信息": item_data,
                                    "卖家信息": {},
                                }
                                price_reference = build_market_reference(
                                    keyword=keyword,
                                    item=item_data,
                                    current_market_items=basic_items,
                                    historical_snapshots=historical_snapshots,
                                )
                                final_record["价格参考"] = price_reference
                                final_record["price_insight"] = price_reference.get(
                                    "本商品价格位置", {}
                                )

                                # 提交分析任务
                                analysis_dispatcher.submit(
                                    ItemAnalysisJob(
                                        keyword=keyword,
                                        task_name=task_config.get(
                                            "task_name", "Untitled Task"
                                        ),
                                        decision_mode=decision_mode,
                                        analyze_images=analyze_images,
                                        prompt_text=ai_prompt_text,
                                        keyword_rules=tuple(keyword_rules or []),
                                        final_record=final_record,
                                        seller_id=str(user_id) if user_id else None,
                                        zhima_credit_text=zhima_credit_text,
                                        registration_duration_text=registration_duration_text,
                                    )
                                )

                                processed_links.add(unique_key)
                                processed_item_count += 1
                                log_time(
                                    f"商品已提交后台分析。累计处理 {processed_item_count} 个新商品。"
                                )

                                # --- 修改: 增加单个商品处理后的主要延迟 ---
                                log_time(
                                    "[反爬] 执行一次主要的随机延迟以模拟用户浏览间隔..."
                                )
                                await random_sleep(5, 10)
                            else:
                                print(
                                    f"   错误: 获取商品详情API响应失败，状态码: {detail_response.status}"
                                )
                                if AI_DEBUG_MODE:
                                    print(
                                        f"--- [DETAIL DEBUG] FAILED RESPONSE from {item_data['商品链接']} ---"
                                    )
                                    try:
                                        print(await detail_response.text())
                                    except Exception as e:
                                        print(f"无法读取响应内容: {e}")
                                    print(
                                        "----------------------------------------------------"
                                    )

                        except PlaywrightTimeoutError:
                            print(f"   错误: 访问商品详情页或等待API响应超时。")
                        except Exception as e:
                            print(f"   错误: 处理商品详情时发生未知错误: {e}")
                        finally:
                            await detail_page.close()
                            # --- 修改: 增加关闭页面后的短暂整理时间 ---
                            await random_sleep(2, 4)  # 原来是 (1, 2.5)

                        await _record_task_progress(
                            page_num,
                            progress_total_items_found,
                            progress_items_processed,
                            estimated_remaining_items,
                        )

                    # --- 新增: 在处理完一页所有商品后，翻页前，增加一个更长的“休息”时间 ---
                    if not stop_scraping and page_num < max_pages:
                        print(
                            f"--- 第 {page_num} 页处理完毕，准备翻页。执行一次页面间的长时休息... ---"
                        )
                        await random_sleep(10, 15)

            except PlaywrightTimeoutError as e:
                if _is_login_url(page.url):
                    raise LoginRequiredError(
                        f"Login required: redirected to {page.url} (cookies/state likely expired)"
                    ) from e
                print(f"\n操作超时错误: 页面元素或网络响应未在规定时间内出现。\n{e}")
                raise
            except asyncio.CancelledError:
                log_time("收到取消信号，正在终止当前爬虫任务...")
                raise
            except Exception as e:
                # Continuous 模式下，让 TargetClosedError 继续传播以便执行下一批
                if type(e).__name__ == "TargetClosedError" and execution_mode == "continuous":
                    log_time("[Continuous] 浏览器会话结束，准备启动新一轮...")
                    # 写入这一轮的最终记录
                    await _record_task_progress(
                        page_num,
                        progress_total_items_found,
                        progress_items_processed,
                        estimated_remaining_items,
                    )
                    return processed_item_count
                if type(e).__name__ == "TargetClosedError":
                    log_time("浏览器已关闭，忽略后续异常（可能是任务被停止）。")
                    return processed_item_count
                if "passport.goofish.com" in str(e):
                    raise LoginRequiredError(
                        f"Login required: redirected to passport flow ({e})"
                    ) from e
                print(f"\n爬取过程中发生未知错误: {e}")

                # 如果有任务ID，记录错误信息到数据库
                if task_id is not None:
                    try:
                        repository = SqliteTaskRepository()
                        task_service = TaskService(repository)
                        error_msg = f"爬取过程中发生未知错误: {str(e)}"
                        await task_service.record_task_error(task_id, error_msg)
                        print(f"记录任务错误到数据库: {error_msg}")
                    except Exception as db_err:
                        print(f"记录错误到数据库失败: {db_err}")

                raise
            finally:
                if analysis_dispatcher is not None:
                    log_time("等待后台分析任务完成...")
                    await analysis_dispatcher.join()
                log_time("任务执行完毕，浏览器将在5秒后自动关闭...")

                # 任务结束时写入最终进度（确保current_page被更新）
                if task_progress_service and task_id and page_num > 0:
                    try:
                        await task_progress_service.update_task_progress(
                            task_id,
                            page_num,
                            progress_total_items_found,
                            progress_items_processed,
                            estimated_remaining_items,
                        )
                        log_time(f"[最终进度记录] page={page_num}, items={progress_items_processed}")
                    except Exception as e:
                        print(f"记录最终进度失败: {e}")

                await asyncio.sleep(5)
                if debug_limit:
                    input("按回车键关闭浏览器...")
                await browser.close()

        return processed_item_count

    processed_item_count = 0
    attempt_limit = max(
        rotation_settings["account_retry_limit"],
        rotation_settings["proxy_retry_limit"],
        1,
    )
    last_error = ""
    last_state_path: Optional[str] = None

    # 初始化进度跟踪变量（用于 continuous 模式的批次间循环）
    progress_total_items_found = 0
    progress_items_processed = 0
    estimated_remaining_items = 0

    # If this task is already in a paused state, skip immediately.
    task_name_for_guard = task_config.get("task_name", "未命名任务")
    pause_cookie_path = None
    if (
        isinstance(task_config.get("account_state_file"), str)
        and task_config.get("account_state_file").strip()
    ):
        pause_cookie_path = task_config.get("account_state_file").strip()
    elif os.path.exists(STATE_FILE):
        pause_cookie_path = STATE_FILE

    decision = FAILURE_GUARD.should_skip_start(
        task_name_for_guard, cookie_path=pause_cookie_path
    )
    if decision.skip:
        print(
            f"[FailureGuard] 任务 '{task_name_for_guard}' 已暂停重试 (连续失败 {decision.consecutive_failures}/{FAILURE_GUARD.threshold})"
        )
        if decision.should_notify:
            try:
                await send_ntfy_notification(
                    {
                        "商品标题": f"[任务暂停] {task_name_for_guard}",
                        "当前售价": "N/A",
                        "商品链接": "#",
                    },
                    "任务处于暂停状态，将跳过执行。\n"
                    f"原因: {decision.reason}\n"
                    f"连续失败: {decision.consecutive_failures}/{FAILURE_GUARD.threshold}\n"
                    f"暂停到: {decision.paused_until.strftime('%Y-%m-%d %H:%M:%S') if decision.paused_until else 'N/A'}\n"
                    "修复方法: 更新登录态/cookies文件后会自动恢复。",
                )
            except Exception as e:
                print(f"发送任务暂停通知失败: {e}")

        cleanup_task_images(task_config.get("task_name", "default"))
        return 0

    for attempt in range(1, attempt_limit + 1):
        if attempt == 1:
            selected_account = _select_account()
            selected_proxy = _select_proxy()
        else:
            if (
                rotation_settings["account_enabled"]
                and rotation_settings["account_mode"] == "on_failure"
            ):
                account_pool.mark_bad(selected_account, last_error)
                selected_account = _select_account(force_new=True)
            if (
                rotation_settings["proxy_enabled"]
                and rotation_settings["proxy_mode"] == "on_failure"
            ):
                proxy_pool.mark_bad(selected_proxy, last_error)
                selected_proxy = _select_proxy(force_new=True)

        if rotation_settings["account_enabled"] and not selected_account:
            last_error = "未找到可用的登录状态文件，无法继续执行任务。"
            print(last_error)
            break
        if not rotation_settings["account_enabled"] and not selected_account:
            last_error = "未找到可用的登录状态文件，无法继续执行任务。"
            print(last_error)
            break
        if rotation_settings["proxy_enabled"] and not selected_proxy:
            last_error = "未找到可用的代理地址，无法继续执行任务。"
            print(last_error)
            break

        state_path = selected_account.value if selected_account else STATE_FILE
        last_state_path = state_path
        proxy_server = selected_proxy.value if selected_proxy else None
        if rotation_settings["account_enabled"]:
            print(f"账号轮换：使用登录状态 {state_path}")
        if rotation_settings["proxy_enabled"] and proxy_server:
            print(f"IP 轮换：使用代理 {proxy_server}")

        try:
            processed_item_count += await _run_scrape_attempt(state_path, proxy_server)
            last_error = ""
            FAILURE_GUARD.record_success(task_name_for_guard)

            # ===== Continuous 模式：批次间循环 =====
            if execution_mode == "continuous" and not last_error:
                # 初始化 task_run 管理
                task_run_repo = SqliteTaskRunRepository()
                batch_number = 1  # 第一批为 batch_number=1

                # 为第一批创建 task_run 记录
                try:
                    first_batch_run = await task_run_repo.create_run(
                        task_id=task_id,
                        execution_mode="continuous",
                        batch_number=batch_number
                    )
                    first_batch_run_id = first_batch_run.id
                    log_time(f"[Task Run] 创建第一批 task_run 记录: id={first_batch_run_id}, batch_number={batch_number}")
                except Exception as run_err:
                    log_time(f"[Task Run] 创建第一批 task_run 记录失败: {run_err}")
                    first_batch_run_id = None

                # 计算第一批处理的总页数
                # resume_from_page 是起始页，max_pages 是本批处理的页数
                # 所以已处理页数 = resume_from_page - 1 + max_pages
                total_pages_processed = resume_from_page - 1 + max_pages
                log_time(f"[Continuous] 第一批完成，估计已处理 {total_pages_processed} 页")

                # 如果数据库有更准确的数据，使用数据库值
                if task_progress_service:
                    try:
                        current_progress = await task_progress_service.get_task_progress(task_id)
                        if current_progress and current_progress.current_page:
                            db_current_page = current_progress.current_page
                            # 只有当数据库值大于估算值时才使用数据库值（说明有更新的进度）
                            if db_current_page > total_pages_processed:
                                total_pages_processed = db_current_page
                                log_time(f"[Continuous] 从数据库读取更新的进度: page={total_pages_processed}")
                    except Exception as e:
                        log_time(f"[Continuous] 读取数据库进度失败: {e}，继续使用估算值")

                # 完成第一批的 task_run 记录
                if first_batch_run_id and task_progress_service and task_id:
                    try:
                        current_progress = await task_progress_service.get_task_progress(task_id)
                        if current_progress:
                            await task_run_repo.finish_run(
                                run_id=first_batch_run_id,
                                status="completed",
                                pages_crawled=max_pages,
                                items_found=current_progress.total_items_found or 0,
                                items_processed=current_progress.items_processed or 0,
                                error_message=None,
                            )
                            log_time(f"[Task Run] 完成第一批 task_run 记录: id={first_batch_run_id}, pages={max_pages}")
                    except Exception as run_err:
                        log_time(f"[Task Run] 完成第一批 task_run 记录失败: {run_err}")

                # 计算剩余页数
                remaining_pages = max_page_limit - total_pages_processed if max_page_limit > 0 else float('inf')

                # 继续执行直到达到 max_page_limit
                while remaining_pages > 0 and not last_error:
                    # 检查是否还有机会执行
                    if max_page_limit > 0 and total_pages_processed >= max_page_limit:
                        log_time(f"[Continuous] 已达到最大页数限制 {max_page_limit}，停止执行")
                        break

                    # 计算这一批要执行的页数
                    pages_this_batch = min(max_pages, int(remaining_pages)) if max_page_limit > 0 else max_pages

                    log_time(f"[Continuous] 批次间休眠 {sleep_interval_min}-{sleep_interval_max}s...")

                    # 批次开始前写入记录（只有已处理过页面才写入，避免写入 page=0 覆盖进度）
                    if task_progress_service and task_id and total_pages_processed > 0:
                        await task_progress_service.update_task_progress(
                            task_id,
                            total_pages_processed,
                            progress_total_items_found,
                            progress_items_processed,
                            estimated_remaining_items or 0,
                        )
                        log_time(f"[进度记录] 批次开始前: page={total_pages_processed}")

                    await random_sleep(sleep_interval_min, sleep_interval_max)

                    # 设置每批的页数为 max_pages（临时修改）
                    original_max_pages = max_pages
                    max_pages = pages_this_batch

                    # 更新 resume_from_page 为下一批的起始页
                    resume_from_page = total_pages_processed + 1

                    log_time(f"[Continuous] 开始执行第 {total_pages_processed + 1}-{total_pages_processed + pages_this_batch} 页...")
                    log_time(f"[Continuous] 📍 resume_from_page 已设置为 {resume_from_page}（下一批的起始页）")

                    # 创建本批次的 task_run 记录
                    try:
                        current_task_run = await task_run_repo.create_run(
                            task_id=task_id,
                            execution_mode="continuous",
                            batch_number=batch_number
                        )
                        current_task_run_id = current_task_run.id
                        log_time(f"[Task Run] 创建 task_run 记录: id={current_task_run_id}, batch_number={batch_number}")
                    except Exception as run_err:
                        log_time(f"[Task Run] 创建 task_run 记录失败: {run_err}")
                        current_task_run_id = None

                    try:
                        batch_count = await _run_scrape_attempt(state_path, proxy_server)
                        processed_item_count += batch_count
                        total_pages_processed += pages_this_batch

                        remaining_pages = max_page_limit - total_pages_processed if max_page_limit > 0 else float('inf')

                        # 批次结束后从数据库读取最新的进度（确保获得准确的数据）
                        if task_progress_service and task_id:
                            try:
                                current_progress = await task_progress_service.get_task_progress(task_id)
                                if current_progress:
                                    progress_total_items_found = current_progress.total_items_found or 0
                                    progress_items_processed = current_progress.items_processed or 0

                                    # 重新计算预估剩余商品数（基于全局进度而不是旧值）
                                    # 防止 estimated_remaining_items 在批次结束时变 0 后就一直是 0
                                    if max_page_limit > 0 and progress_total_items_found > 0 and total_pages_processed > 0:
                                        # 基于已抓取的页数计算平均每页商品数
                                        avg_items_per_page = progress_total_items_found // total_pages_processed
                                        remaining_pages = max_page_limit - total_pages_processed
                                        estimated_remaining_items = max(0, remaining_pages * avg_items_per_page)
                                        log_time(f"[进度估算] 重算 estimated_remaining_items={estimated_remaining_items} (剩余页:{remaining_pages}, 平均每页:{avg_items_per_page})")
                                    else:
                                        estimated_remaining_items = current_progress.estimated_remaining_items or 0

                                    log_time(f"[进度记录] 批次结束后: page={total_pages_processed}, items={progress_items_processed}, 剩余={estimated_remaining_items}")

                                    # 完成本批次的 task_run 记录
                                    if current_task_run_id:
                                        try:
                                            # 如果有错误，标记为失败；否则标记为完成
                                            if last_error:
                                                await task_run_repo.finish_run(
                                                    run_id=current_task_run_id,
                                                    status="failed",
                                                    pages_crawled=pages_this_batch,
                                                    items_found=progress_total_items_found,
                                                    items_processed=progress_items_processed,
                                                    error_message=last_error,
                                                )
                                                log_time(f"[Task Run] 标记 task_run 记录为失败: id={current_task_run_id}, error={last_error}")
                                            else:
                                                await task_run_repo.finish_run(
                                                    run_id=current_task_run_id,
                                                    status="completed",
                                                    pages_crawled=pages_this_batch,
                                                    items_found=progress_total_items_found,
                                                    items_processed=progress_items_processed,
                                                    error_message=None,
                                                )
                                                log_time(f"[Task Run] 完成 task_run 记录: id={current_task_run_id}, pages={pages_this_batch}, items={progress_items_processed}")
                                            batch_number += 1  # 准备下一批
                                        except Exception as run_complete_err:
                                            log_time(f"[Task Run] 完成 task_run 记录失败: {run_complete_err}")
                            except Exception as prog_err:
                                log_time(f"[进度读取] 读取进度失败: {prog_err}")
                    except Exception as cont_err:
                        log_time(f"[Continuous] 批次执行出错: {cont_err}")
                        last_error = str(cont_err)

                        # 批次执行失败，标记 task_run 记录为失败
                        if current_task_run_id:
                            try:
                                await task_run_repo.finish_run(
                                    run_id=current_task_run_id,
                                    status="failed",
                                    pages_crawled=0,
                                    items_found=0,
                                    items_processed=0,
                                    error_message=last_error,
                                )
                                log_time(f"[Task Run] 标记 task_run 记录为失败: id={current_task_run_id}")
                            except Exception as run_err:
                                log_time(f"[Task Run] 标记 task_run 失败状态失败: {run_err}")
                    finally:
                        # 恢复原始的 max_pages
                        max_pages = original_max_pages

                if remaining_pages <= 0 or last_error:
                    log_time(f"[Continuous] 执行完成，已处理 {total_pages_processed} 页")

                    # 如果有错误，保存到数据库
                    if last_error and task_id is not None:
                        try:
                            repository = SqliteTaskRepository()
                            task_service = TaskService(repository)
                            await task_service.record_task_error(task_id, last_error)
                            log_time(f"[错误记录] 已保存错误信息到数据库: {last_error}")
                        except Exception as db_err:
                            print(f"记录错误到数据库失败: {db_err}")

                    break

            break
        except LoginRequiredError as e:
            last_error = str(e)
            print(f"检测到登录失效/重定向: {e}")

            # 如果有任务ID，记录错误信息到数据库
            if task_id is not None:
                try:
                    repository = SqliteTaskRepository()
                    task_service = TaskService(repository)
                    error_msg = f"检测到登录失效/重定向: {str(e)}"
                    await task_service.record_task_error(task_id, error_msg)
                    print(f"记录任务错误到数据库: {error_msg}")
                except Exception as db_err:
                    print(f"记录错误到数据库失败: {db_err}")

            break
        except RiskControlError as e:
            last_error = str(e)
            print(f"检测到风控或验证触发: {e}")
            # 风控验证通常不是简单轮换能解决的，避免无意义重试。

            # 如果有任务ID，记录错误信息到数据库
            if task_id is not None:
                try:
                    repository = SqliteTaskRepository()
                    task_service = TaskService(repository)
                    error_msg = f"检测到风控或验证触发: {str(e)}"
                    await task_service.record_task_error(task_id, error_msg)
                    print(f"记录任务错误到数据库: {error_msg}")
                except Exception as db_err:
                    print(f"记录错误到数据库失败: {db_err}")

            break
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            print(f"本次尝试失败: {last_error}")
            if attempt < attempt_limit:
                print("将尝试轮换账号/IP 后重试...")

    if last_error:
        await _notify_task_failure(task_config, last_error, cookie_path=last_state_path)

    # 清理任务图片目录
    cleanup_task_images(task_config.get("task_name", "default"))

    return processed_item_count
