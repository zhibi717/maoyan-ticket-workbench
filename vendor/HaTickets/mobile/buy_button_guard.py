# -*- coding: UTF-8 -*-
"""
Safety guard that prevents accidental clicks on reservation ("预约抢票") buttons.

The Damai app shows "预约抢票" before sale opens and "立即购票" after.
Clicking the reservation button enters the WRONG flow. This module ensures
only purchase-ready button texts are treated as safe to click.
"""

import time
from typing import Optional, Tuple

try:
    from mobile.logger import get_logger
except ImportError:
    from logger import get_logger

logger = get_logger(__name__)

SAFE_TEXTS = frozenset(
    {
        "立即购买",
        "立即购票",
        "立即抢票",
        "立即预定",
        "立即预订",  # 大麦 2026-04 后新增（issue #29）
        "Book Now",  # 国际化场景兜底（issue #29）
        "选座购买",
        "购买",
        "抢票",
        "预定",
    }
)

BLOCKED_TEXTS = frozenset(
    {
        "预约抢票",
        "预约",
        "预售",
        "即将开抢",
        "待开售",
        "未开售",
        "提交抢票预约",
    }
)

# CTA 候选 resource-id（issue #41）：
# - v8.x 传统购买按钮 btn_buy_view（带文案，可做安全文案校验），旧 ID 在前保兼容；
# - 大麦 ≥9.0.2x 详情页移除了 btn_buy_view，CTA 变为 Canvas 自绘，仅剩底部购买栏
#   容器 trade_project_detail_purchase_status_bar_container_fl（无任何文案节点）。
_BUY_BUTTON_RESOURCE_IDS = (
    "cn.damai:id/btn_buy_view",
    "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl",
)

# 兼容别名：保留旧常量名，防外部引用破坏（issue #41）
_BUY_BUTTON_RESOURCE_ID = _BUY_BUTTON_RESOURCE_IDS[0]


class BuyButtonGuard:
    """Guards against accidental clicks on reservation/pre-sale buttons.

    Args:
        device: A uiautomator2 device instance.
    """

    def __init__(self, device):
        self._device = device
        # 最近一次命中的候选 resource-id（None 表示未命中），供调用方结构化日志使用
        self._last_matched_resource_id: Optional[str] = None

    def is_safe_to_click(self, button_text: Optional[str]) -> bool:
        """Return True only if button_text is a known safe purchase text.

        Empty, None, unknown, or blocked texts all return False.
        """
        if not button_text:
            return False
        safe = button_text in SAFE_TEXTS
        if not safe and button_text in BLOCKED_TEXTS:
            logger.warning("Blocked unsafe button text: %s", button_text)
        elif not safe:
            logger.warning("Unknown button text rejected: %s", button_text)
        return safe

    def _find_buy_button(self):
        """Find the buy button element by candidate resource IDs.

        依序遍历 :data:`_BUY_BUTTON_RESOURCE_IDS`（旧 ID 在前，保 v8.x 兼容），
        返回首个存在的元素，并将命中的 ID 记入 ``self._last_matched_resource_id``。

        Returns:
            The UI element if found, or None.
        """
        for rid in _BUY_BUTTON_RESOURCE_IDS:
            try:
                el = self._device(resourceId=rid)
                if el.exists:
                    self._last_matched_resource_id = rid
                    return el
            except Exception:
                logger.debug("Failed to find buy button element (resource-id=%s)", rid)
        self._last_matched_resource_id = None
        return None

    def get_current_text(self) -> Optional[str]:
        """Read current button text without clicking.

        Returns:
            The button text string, or None if the button is not found.
        """
        el = self._find_buy_button()
        if el is None:
            return None
        try:
            text = el.get_text()
            return text if text else None
        except Exception:
            logger.debug("Failed to read buy button text")
            return None

    def get_cta_center_coords(self) -> Optional[Tuple[int, int]]:
        """定位 CTA（购买按钮/购买栏容器）的中心坐标（issue #41）。

        大麦 ≥9.0.2x 详情页 CTA 为 Canvas 自绘、读不到任何文案，此方法按候选
        resource-id 取首个存在元素的 bounds 中心，供下游坐标兜底点击。

        Returns:
            (x, y) 中心坐标；候选 ID 均不存在或读取失败时返回 None。
        """
        for rid in _BUY_BUTTON_RESOURCE_IDS:
            try:
                el = self._device(resourceId=rid)
                if el.exists:
                    bounds = el.info["bounds"]
                    x = (int(bounds["left"]) + int(bounds["right"])) // 2
                    y = (int(bounds["top"]) + int(bounds["bottom"])) // 2
                    self._last_matched_resource_id = rid
                    return (x, y)
            except Exception:
                logger.debug("Failed to read CTA bounds (resource-id=%s)", rid)
        return None

    def wait_until_safe(self, timeout_s: float = 10.0, poll_ms: int = 50) -> bool:
        """Poll button text until a safe text is detected or timeout expires.

        Args:
            timeout_s: Maximum seconds to wait.
            poll_ms: Milliseconds between polls.

        Returns:
            True if a safe text was detected before timeout, False otherwise.
        """
        deadline = time.time() + timeout_s
        poll_s = poll_ms / 1000.0

        while True:
            text = self.get_current_text()
            if text and self.is_safe_to_click(text):
                logger.info("Safe button text detected: %s", text)
                return True

            if time.time() >= deadline:
                logger.warning(
                    "Timed out waiting for safe button text (last seen: %s)",
                    text,
                )
                return False

            time.sleep(poll_s)
