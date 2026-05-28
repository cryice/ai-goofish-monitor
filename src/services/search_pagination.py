import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from src.utils import log_time, random_sleep

NEXT_PAGE_SELECTOR = (
    "button[class*='search-pagination-arrow-container']"
    ":has([class*='search-pagination-arrow-right'])"
    ":not([disabled])"
)
PAGE_INPUT_SELECTOR = "input[class*='search-pagination-to-page-input']"
GO_BUTTON_SELECTOR = "button[class*='search-pagination-to-page-confirm-button']"
SEARCH_RESULTS_API_FRAGMENT = "/h5/mtop.taobao.idlemtopsearch.pc.search/1.0/"
PAGE_REQUEST_TIMEOUT_MS = 20_000
PAGE_CLICK_TIMEOUT_MS = 10_000
PAGE_RETRY_DELAY_SECONDS = 5
PAGE_RETRY_COUNT = 2
PAGE_CLICK_SLEEP_MIN_SECONDS = 2
PAGE_CLICK_SLEEP_MAX_SECONDS = 5


@dataclass(frozen=True)
class PageAdvanceResult:
    advanced: bool
    response: Optional[Any] = None
    stop_reason: Optional[str] = None


def is_search_results_response(
    response: Any,
    api_url_fragment: str = SEARCH_RESULTS_API_FRAGMENT,
) -> bool:
    request = getattr(response, "request", None)
    request_method = getattr(request, "method", None)
    response_url = getattr(response, "url", "")
    return api_url_fragment in response_url and request_method == "POST"


async def jump_to_page(
    *,
    page: Any,
    target_page: int,
    logger: Callable[[str], None] = log_time,
    wait_after_jump: Callable[[float, float], Awaitable[None]] = random_sleep,
) -> PageAdvanceResult:
    if target_page <= 1:
        logger(f"目标页码 {target_page} <= 1，无需跳转。")
        return PageAdvanceResult(advanced=False, stop_reason="already_at_first_page")

    page_input = page.locator(PAGE_INPUT_SELECTOR).first
    if not await page_input.count():
        logger(f"未找到页码输入框，尝试逐页翻页到第 {target_page} 页...")
        last_response = None
        for page_num in range(2, target_page + 1):
            result = await advance_search_page(
                page=page,
                page_num=page_num,
                logger=logger,
                wait_after_click=wait_after_jump,
            )
            if not result.advanced:
                return PageAdvanceResult(advanced=False, stop_reason="no_pagination_input")
            last_response = result.response
            if page_num < target_page:
                await wait_after_jump(PAGE_CLICK_SLEEP_MIN_SECONDS, PAGE_CLICK_SLEEP_MAX_SECONDS)
        if last_response:
            return PageAdvanceResult(advanced=True, response=last_response)
        return PageAdvanceResult(advanced=True)

    go_button = page.locator(GO_BUTTON_SELECTOR).first

    for attempt in range(3):
        try:
            await page_input.scroll_into_view_if_needed()
            await random_sleep(0.5, 1)
            await page_input.click()
            await page_input.fill("")
            await page_input.type(str(target_page), delay=100)
            await random_sleep(0.3, 0.5)

            async with page.expect_response(
                is_search_results_response,
                timeout=PAGE_REQUEST_TIMEOUT_MS,
            ) as response_info:
                if await go_button.count():
                    await go_button.scroll_into_view_if_needed()
                    await random_sleep(0.2, 0.3)
                    await go_button.click(timeout=PAGE_CLICK_TIMEOUT_MS)
                else:
                    await page_input.press("Enter")
                await wait_after_jump(PAGE_CLICK_SLEEP_MIN_SECONDS, PAGE_CLICK_SLEEP_MAX_SECONDS)

            response = await response_info.value
            if response and response.ok:
                logger(f"成功跳转到第 {target_page} 页。")
                return PageAdvanceResult(advanced=True, response=response)
            else:
                logger(f"跳转后响应无效，尝试重试...")
                await wait_after_jump(PAGE_RETRY_DELAY_SECONDS, PAGE_RETRY_DELAY_SECONDS)
        except PlaywrightTimeoutError:
            if attempt < 2:
                logger(f"跳转第 {target_page} 页超时，重试中...")
                await wait_after_jump(PAGE_RETRY_DELAY_SECONDS, PAGE_RETRY_DELAY_SECONDS)
                continue
            logger(f"跳转第 {target_page} 页失败。")
            return PageAdvanceResult(advanced=False, stop_reason="jump_timeout")

    return PageAdvanceResult(advanced=False, stop_reason="jump_failed")


async def advance_search_page(
    *,
    page: Any,
    page_num: int,
    logger: Callable[[str], None] = log_time,
    wait_after_click: Callable[[float, float], Awaitable[None]] = random_sleep,
    retry_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    max_retries: int = PAGE_RETRY_COUNT,
) -> PageAdvanceResult:
    next_button = page.locator(NEXT_PAGE_SELECTOR).first
    if not await next_button.count():
        logger("已到达最后一页，未找到可用的'下一页'按钮，停止翻页。")
        return PageAdvanceResult(advanced=False, stop_reason="no_next_button")

    for retry_index in range(max_retries):
        try:
            await next_button.scroll_into_view_if_needed()
            async with page.expect_response(
                is_search_results_response,
                timeout=PAGE_REQUEST_TIMEOUT_MS,
            ) as response_info:
                try:
                    await next_button.click(timeout=PAGE_CLICK_TIMEOUT_MS)
                except PlaywrightTimeoutError:
                    logger(f"第 {page_num} 页下一页按钮点击超时，停止翻页。")
                    return PageAdvanceResult(
                        advanced=False,
                        stop_reason="click_timeout",
                    )
            await wait_after_click(
                PAGE_CLICK_SLEEP_MIN_SECONDS,
                PAGE_CLICK_SLEEP_MAX_SECONDS,
            )
            return PageAdvanceResult(
                advanced=True,
                response=await response_info.value,
            )
        except PlaywrightTimeoutError:
            if retry_index < max_retries - 1:
                logger(
                    f"等待第 {page_num} 页搜索响应超时，"
                    f"{PAGE_RETRY_DELAY_SECONDS}秒后重试..."
                )
                await retry_sleep(PAGE_RETRY_DELAY_SECONDS)
                continue

            logger(f"等待第 {page_num} 页搜索响应超时 {max_retries} 次，停止翻页。")
            return PageAdvanceResult(advanced=False, stop_reason="response_timeout")

    return PageAdvanceResult(advanced=False, stop_reason="unknown")