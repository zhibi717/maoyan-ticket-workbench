# -*- coding: UTF-8 -*-
"""Natural-language prompt parsing for the mobile Damai workflow."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

try:
    from mobile.date_utils import normalize_date as _normalize_date_external
    from mobile.item_resolver import KNOWN_CITY_TOKENS, normalize_text
except ImportError:
    from item_resolver import KNOWN_CITY_TOKENS, normalize_text  # type: ignore[no-redef]


_CHINESE_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

# issue #51+#50：城市 token 全集迁移至 mobile/item_resolver.KNOWN_CITY_TOKENS
# （event_navigator 的城市冲突 veto 也要用）。保留旧名别名，兼容既有 import。
_KNOWN_CITY_TOKENS = KNOWN_CITY_TOKENS

_REQUEST_STOPWORDS = (
    "帮我",
    "帮忙",
    "抢票",
    "抢一张",
    "抢两张",
    "抢",
    "买",
    "订",
    "门票",
    "票",
    "演出票",
    "给我",
    "给",
    "一下",
    "尽快",
    "尽量",
    "麻烦",
    "我要",
    "想要",
    "请",
    "能不能",
    "可以",
    "帮",
    "票价",
    "价位",
)

_LOW_SIGNAL_KEYWORD_TOKENS = {"演唱会", "音乐会", "演出", "巡演", "live", "门票"}

_SEAT_HINTS = ("内场", "看台", "VIP", "vip", "至尊", "前排", "后排", "看台区", "包厢")

_UNAVAILABLE_TAGS = {"无票", "缺货", "售罄", "已售罄", "不可选", "暂不可售"}

_ATTENDEE_NAME_PATTERN = r"[\u4e00-\u9fffA-Za-z·•]{2,16}"
_ATTENDEE_SPLIT_PATTERN = re.compile(r"\s*(?:、|，|,|和|及|与)\s*")
_ATTENDEE_PATTERNS = (
    re.compile(
        rf"(?:帮|给|替)(?P<names>{_ATTENDEE_NAME_PATTERN}(?:\s*(?:、|，|,|和|及|与)\s*{_ATTENDEE_NAME_PATTERN}){{0,4}})"
        rf"(?=(?:抢|买|订))"
    ),
    re.compile(
        rf"观演人(?:是|为)?(?P<names>{_ATTENDEE_NAME_PATTERN}(?:\s*(?:、|，|,|和|及|与)\s*{_ATTENDEE_NAME_PATTERN}){{0,4}})"
        rf"(?:[，,。；;]|$)"
    ),
)


@dataclass
class PromptIntent:
    raw_prompt: str
    quantity: int = 1
    quantity_explicit: bool = False
    attendee_names: list[str] = field(default_factory=list)
    date: Optional[str] = None
    city: Optional[str] = None
    artist: Optional[str] = None
    search_keyword: Optional[str] = None
    candidate_keywords: list[str] = field(default_factory=list)
    price_hint: Optional[str] = None
    seat_hint: Optional[str] = None
    numeric_price_hint: Optional[int] = None
    numeric_price_min: Optional[int] = None
    numeric_price_max: Optional[int] = None
    notes: list[str] = field(default_factory=list)


@dataclass
class ParseResult:
    """诊断框架：parse_prompt 的可观测返回值。

    保留向后兼容：调用方通过属性转发仍可直接读取 intent 字段
    （如 result.attendee_names 等同 result.intent.attendee_names）。
    """

    intent: "PromptIntent"
    matched_item: object = None
    matched_session: Optional[str] = None
    matched_price: Optional[str] = None
    diagnostics: list[str] = field(default_factory=list)
    confidence: float = 0.0

    _ACTIONABLE_THRESHOLD: ClassVar[float] = 0.6

    def is_actionable(self) -> bool:
        return (
            self.confidence >= self._ACTIONABLE_THRESHOLD
            and self.matched_item is not None
        )

    def __getattr__(self, name: str):
        # 仅当常规属性查找失败时才转发到 intent；避免无穷递归
        if name.startswith("_") or name in {"intent"}:
            raise AttributeError(name)
        intent = self.__dict__.get("intent")
        if intent is None:
            raise AttributeError(name)
        return getattr(intent, name)


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _parse_chinese_int(token: str) -> Optional[int]:
    token = (token or "").strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    if token in _CHINESE_DIGITS:
        return _CHINESE_DIGITS[token]
    if len(token) == 2 and token[0] == "十" and token[1] in _CHINESE_DIGITS:
        return 10 + _CHINESE_DIGITS[token[1]]
    if len(token) == 2 and token[1] == "十" and token[0] in _CHINESE_DIGITS:
        return _CHINESE_DIGITS[token[0]] * 10
    if (
        len(token) == 3
        and token[1] == "十"
        and token[0] in _CHINESE_DIGITS
        and token[2] in _CHINESE_DIGITS
    ):
        return _CHINESE_DIGITS[token[0]] * 10 + _CHINESE_DIGITS[token[2]]
    return None


def _parse_quantity(prompt: str) -> int:
    prompt_without_dates = re.sub(r"\d{1,2}\s*月\s*\d{1,2}\s*[号日好]?", " ", prompt)
    prompt_without_dates = re.sub(r"\d{1,2}[./-]\d{1,2}", " ", prompt_without_dates)
    match = re.search(r"([0-9零一二两三四五六七八九十]+)\s*张", prompt_without_dates)
    if not match:
        return 1

    value = _parse_chinese_int(match.group(1))
    return value if value and value > 0 else 1


def _parse_quantity_with_explicit(prompt: str) -> tuple[int, bool]:
    prompt_without_dates = re.sub(r"\d{1,2}\s*月\s*\d{1,2}\s*[号日好]?", " ", prompt)
    prompt_without_dates = re.sub(r"\d{1,2}[./-]\d{1,2}", " ", prompt_without_dates)
    match = re.search(r"([0-9零一二两三四五六七八九十]+)\s*张", prompt_without_dates)
    if not match:
        return 1, False

    value = _parse_chinese_int(match.group(1))
    quantity = value if value and value > 0 else 1
    return quantity, True


def _parse_date(prompt: str) -> Optional[str]:
    """委托给 ``mobile.date_utils.normalize_date``，统一为 ``MM.DD``。"""
    return _normalize_date_external(prompt)


def _parse_city(prompt: str) -> Optional[str]:
    for city in _KNOWN_CITY_TOKENS:
        if city in prompt:
            return city
    match = re.search(r"([\u4e00-\u9fff]{2,4})站", prompt)
    if match:
        return match.group(1)
    return None


def _parse_attendee_names(prompt: str) -> list[str]:
    for pattern in _ATTENDEE_PATTERNS:
        match = pattern.search(prompt)
        if not match:
            continue

        raw_names = match.group("names")
        names = []
        seen = set()
        for part in _ATTENDEE_SPLIT_PATTERN.split(raw_names):
            candidate = _normalize_whitespace(part)
            if not candidate:
                continue
            normalized = normalize_text(candidate)
            if not normalized or normalized in seen:
                continue
            names.append(candidate)
            seen.add(normalized)
        if names:
            return names

    return []


def _clean_prompt_for_keyword(
    prompt: str, removable_tokens: Optional[Iterable[str]] = None
) -> str:
    cleaned = prompt
    cleaned = re.sub(r"https?://\S+", " ", cleaned)
    cleaned = re.sub(r"\d{1,2}\s*月\s*\d{1,2}\s*[号日好]?", " ", cleaned)
    cleaned = re.sub(r"\d{1,2}[./-]\d{1,2}", " ", cleaned)
    cleaned = re.sub(r"[0-9零一二两三四五六七八九十]+\s*张", " ", cleaned)
    for word in _REQUEST_STOPWORDS:
        cleaned = cleaned.replace(word, " ")
    ordered_tokens = sorted(
        {token for token in (removable_tokens or ()) if token},
        key=len,
        reverse=True,
    )
    for token in ordered_tokens:
        if token:
            cleaned = cleaned.replace(token, " ")
    cleaned = re.sub(r"\b\d+\s*元?\b", " ", cleaned)
    cleaned = re.sub(r"[，,。！？!?:：；;~～（）()【】\[\]“”\"'`]+", " ", cleaned)
    cleaned = cleaned.replace("的", " ")
    return _normalize_whitespace(cleaned)


def _is_low_signal_candidate(value: str) -> bool:
    tokens = [part for part in re.split(r"\s+", value or "") if part]
    meaningful = []
    for token in tokens:
        normalized = normalize_text(token)
        if not normalized:
            continue
        if len(normalized) == 1 and normalized not in {"x", "X"}:
            continue
        meaningful.append(normalized)
    if not meaningful:
        return True
    return all(token in _LOW_SIGNAL_KEYWORD_TOKENS for token in meaningful)


def _compact_keyword_phrase(value: str) -> str:
    compact_tokens = []
    for part in re.split(r"\s+", value or ""):
        normalized = normalize_text(part)
        if not normalized:
            continue
        if len(normalized) <= 1 and normalized not in {"x", "X"}:
            continue
        compact_tokens.append(part)
    return _normalize_whitespace(" ".join(compact_tokens))


def _parse_artist_and_keyword(
    prompt: str,
    removable_tokens: Optional[Iterable[str]] = None,
    city: Optional[str] = None,
) -> tuple[Optional[str], Optional[str], list[str]]:
    cleaned = _clean_prompt_for_keyword(prompt, removable_tokens=removable_tokens)

    artist = None
    artist_match = re.search(
        r"([\u4e00-\u9fffA-Za-z0-9·•]+?)(?:的)?(?:演唱会|音乐会|演出|live|LIVE|巡演)",
        cleaned,
    )
    if artist_match:
        artist = artist_match.group(1).strip(" ，,。！？")
    else:
        tail_artist = re.search(r"([\u4e00-\u9fffA-Za-z0-9·•]{2,12})", cleaned)
        if tail_artist:
            artist = tail_artist.group(1)

    candidates = []
    if artist:
        candidates.extend([f"{artist} 演唱会", artist])

    # issue #51：城市全局 replace 会把「龙拳·北京」这类标题自带前缀拦腰挖成
    # 「龙拳·」。追加一个「保留城市版」完整短语候选（removable_tokens 中剔除
    # 城市与「城市+站」后重新清洗），插入 index=2——search_keyword 与
    # candidate_keywords[:2] 保持不变，不破坏既有顺序锁定。
    if city and artist:
        city_tokens = {city, f"{city}站"}
        tokens_without_city = [
            token for token in (removable_tokens or ()) if token not in city_tokens
        ]
        city_preserving = _clean_prompt_for_keyword(
            prompt, removable_tokens=tokens_without_city
        )
        if city_preserving:
            candidates.insert(min(2, len(candidates)), city_preserving)

    if cleaned:
        candidates.append(cleaned)

    deduped = []
    seen = set()
    for candidate in candidates:
        value = _compact_keyword_phrase(candidate)
        normalized = normalize_text(value)
        if (
            not value
            or not normalized
            or normalized in seen
            or _is_low_signal_candidate(value)
        ):
            continue
        deduped.append(value)
        seen.add(normalized)

    return artist, (deduped[0] if deduped else None), deduped


# ---------------------------------------------------------------------------
# issue #45：票价解析前的日程片段剥离
#
# 旧版宽松价格正则「([1-9]\d{1,4})\s*元?」中「元」可选且取首个匹配，导致
# 「5月30号」的日份、「23点/23:00」的开抢时间、「抢12张」的张数等数字
# 抢先被当成票价（如「5月30号…票价1380元」误判为 30 元）。
# 修复思路与 _parse_quantity / _clean_prompt_for_keyword 的既有惯例对齐：
# 先剥离日期/时间/张数片段，再做价格匹配；日期剥离形态与
# mobile/date_utils._PATTERNS 保持一致，保证「日期能识别的片段价格一定不误食」。
# ---------------------------------------------------------------------------

_SCHEDULE_FRAGMENT_PATTERNS = (
    # 5月30号 / 4 月 6 日 / 04月06日
    re.compile(r"\d{1,2}\s*月\s*\d{1,2}\s*[号日好]?"),
    # 2026-04-06 / 2026/04/06 / 2026.04.06（带年份的完整日期）
    re.compile(r"(?<![\d.])\d{4}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{1,2}(?![\d.])"),
    # 2026年
    re.compile(r"\d{4}\s*年"),
    # 23:00 / 23：00（开抢时间）
    re.compile(r"(?<!\d)\d{1,2}\s*[:：]\s*\d{2}(?!\d)"),
    # 23点 / 19点30分（(?<!\d) 防止「1380点」被截出残数「13」）
    re.compile(r"(?<!\d)\d{1,2}\s*[点时](?:\s*\d{1,2}\s*分?)?"),
)

# 4/18 / 12-15 / 4.6 这类无年份短日期：仅当月/日均合法时才剥离，
# 保住「50-80元」这类两位数价格区间（50 不是合法月份）。
_SHORT_DATE_PATTERN = re.compile(r"(?<![\d.])(\d{1,2})\s*[./-]\s*(\d{1,2})(?![\d.])")

# 张数片段（抢12张 / 两张）。必须在短日期剥离之后执行：
# 否则「12-15 张杰」会先被吃掉「15 张」，令短日期「12-15」残缺，
# 残数 12 会被宽松回退层误判为票价。
_QUANTITY_FRAGMENT_PATTERN = re.compile(r"[0-9零一二两三四五六七八九十]+\s*张")


def _replace_short_date_if_valid(match: "re.Match[str]") -> str:
    month = int(match.group(1))
    day = int(match.group(2))
    if 1 <= month <= 12 and 1 <= day <= 31:
        return " "
    return match.group(0)


def _strip_schedule_fragments(text: str) -> str:
    """剥离日期/时间/张数片段，避免票价解析误食其中的数字（issue #45）。"""
    stripped = text or ""
    for pattern in _SCHEDULE_FRAGMENT_PATTERNS:
        stripped = pattern.sub(" ", stripped)
    stripped = _SHORT_DATE_PATTERN.sub(_replace_short_date_if_valid, stripped)
    return _QUANTITY_FRAGMENT_PATTERN.sub(" ", stripped)


# seat token 预剥离顺序：按长度降序，保证「看台区」先于「看台」被替换
_SEAT_TOKENS_BY_LENGTH = tuple(
    sorted(set(_SEAT_HINTS), key=lambda token: (-len(token), token))
)

# Tier1 上下文锚定：带「票价/价格/¥/元」等价格语境的数字优先命中
_PRICE_ANCHORED_PATTERNS = (
    re.compile(r"(?:票价|价格|价位|单价)\s*[为是:：]?\s*([1-9]\d{1,4})(?!\d)"),
    re.compile(r"[¥￥]\s*([1-9]\d{1,4})(?!\d)"),
    re.compile(r"([1-9]\d{1,4})(?!\d)\s*元"),
)

# Tier2 宽松回退：无价格语境的裸数字（兼容「内场280」「看台票 899」等输入）。
# (?<![A-Za-z\d])：排除「顽童mj116」这类字母数字混合 token 内的数字；
# (?!\d)：堵死贪婪回溯绕过——没有它「1380号」会回溯成「138」骗过后面的断言；
# (?!\s*[月号日张点时分秒年:：])：双保险，即使剥离有遗漏也排除紧跟
# 日期/时间/张数单位的数字。
_PRICE_LOOSE_PATTERN = re.compile(
    r"(?<![A-Za-z\d])([1-9]\d{1,4})(?!\d)(?!\s*[月号日张点时分秒年:：])"
)

# 可观测性事件：票价是在剥离日程片段后解析出来的。同时追加到 intent.notes
# （summary 模式「提示:」段会打印）与 diagnostics（不可执行路径打印），
# 便于真机排查。措辞刻意避开「日期」「票档」字样，不干扰缺失字段类提示的判定。
_SCHEDULE_STRIP_NOTE = (
    "price_parse.schedule_fragments_stripped："
    "已剥离场次/时间/张数片段后再解析票价，避免误匹配"
)


def _extract_numeric_price(prompt: str) -> Optional[int]:
    """从提示词中提取票价数字（issue #45 修复核心）。

    步骤：
    1. 剥离日期/时间/张数片段（``_strip_schedule_fragments``）；
    2. 把 seat token 替换为空格，使「VIP1680」的数字独立成词
       （否则 Tier2 的 lookbehind 会挡掉紧贴 seat 的价格）；
    3. 两层匹配：Tier1 上下文锚定优先，Tier2 宽松回退；层内取首个匹配，
       保持现行「多候选取第一个」语义。
    """
    stripped = _strip_schedule_fragments(prompt or "")
    for token in _SEAT_TOKENS_BY_LENGTH:
        stripped = stripped.replace(token, " ")
    for pattern in _PRICE_ANCHORED_PATTERNS:
        match = pattern.search(stripped)
        if match:
            return int(match.group(1))
    loose_match = _PRICE_LOOSE_PATTERN.search(stripped)
    if loose_match:
        return int(loose_match.group(1))
    return None


def _parse_price_hints(
    prompt: str,
) -> tuple[Optional[str], Optional[str], Optional[int]]:
    seat_hint = None
    for token in _SEAT_HINTS:
        if token in prompt:
            seat_hint = token
            break

    numeric_price = _extract_numeric_price(prompt)

    if seat_hint and numeric_price:
        return f"{seat_hint}{numeric_price}元", seat_hint, numeric_price
    if numeric_price:
        return f"{numeric_price}元", None, numeric_price
    if seat_hint:
        return seat_hint, seat_hint, None
    return None, None, None


def _parse_price_range(prompt: str) -> tuple[Optional[int], Optional[int]]:
    """从 ``500-800元`` / ``500到800`` 等区间表达中识别 (min, max)。

    先剥离日程片段：避免「12-15」这类短横线日期被误判为价格区间
    （issue #45 同族缺陷）。
    """
    stripped = _strip_schedule_fragments(prompt or "")
    match = re.search(
        r"([1-9]\d{1,4})\s*[-~到至]\s*([1-9]\d{1,4})\s*元?",
        stripped,
    )
    if not match:
        return None, None
    low = int(match.group(1))
    high = int(match.group(2))
    if low > high:
        low, high = high, low
    return low, high


def parse_prompt(prompt: str) -> "ParseResult":
    """解析自然语言提示词。

    返回 ``ParseResult``。为保持向后兼容，``ParseResult`` 通过 ``__getattr__``
    把字段访问转发到 ``intent``——历史上以 ``parse_prompt(...).attendee_names``
    形式读取的代码无需改动。``confidence`` 与 ``diagnostics`` 由解析阶段产出，
    用于 ``apply`` 模式下的「拒绝写残缺 config」判定。
    """
    if not isinstance(prompt, str) or len(prompt.strip()) == 0:
        raise ValueError("prompt 不能为空")

    normalized_prompt = _normalize_whitespace(prompt)
    parsed_date = _parse_date(normalized_prompt)
    parsed_city = _parse_city(normalized_prompt)
    attendee_names = _parse_attendee_names(normalized_prompt)
    parsed_quantity, quantity_explicit = _parse_quantity_with_explicit(
        normalized_prompt
    )
    if attendee_names and not quantity_explicit:
        parsed_quantity = len(attendee_names)
    price_hint, seat_hint, numeric_price = _parse_price_hints(normalized_prompt)
    price_min, price_max = _parse_price_range(normalized_prompt)
    if price_min is not None and price_max is not None:
        # 区间优先：避免「500-800元」被识别为「500元」单值 hint
        numeric_price = None
        price_hint = (
            f"{seat_hint}{price_min}-{price_max}元"
            if seat_hint
            else f"{price_min}-{price_max}元"
        )
    removable_tokens = []
    removable_tokens.extend(attendee_names)
    if parsed_city:
        removable_tokens.extend([parsed_city, f"{parsed_city}站"])
    if seat_hint:
        removable_tokens.append(seat_hint)
    if price_hint:
        removable_tokens.append(price_hint)
    if numeric_price is not None:
        removable_tokens.extend([str(numeric_price), f"{numeric_price}元"])

    artist, keyword, candidate_keywords = _parse_artist_and_keyword(
        normalized_prompt,
        removable_tokens=removable_tokens,
        city=parsed_city,
    )

    intent = PromptIntent(
        raw_prompt=normalized_prompt,
        quantity=parsed_quantity,
        quantity_explicit=quantity_explicit,
        attendee_names=attendee_names,
        date=parsed_date,
        city=parsed_city,
        artist=artist,
        search_keyword=keyword,
        candidate_keywords=candidate_keywords,
        price_hint=price_hint,
        seat_hint=seat_hint,
        numeric_price_hint=numeric_price,
        numeric_price_min=price_min,
        numeric_price_max=price_max,
    )

    if not intent.search_keyword:
        raise ValueError("无法从提示词中提取搜索关键词")

    if not intent.attendee_names:
        intent.notes.append("提示词中未识别到观演人姓名，自动写配置前需要补充")
    elif intent.quantity_explicit and len(intent.attendee_names) != intent.quantity:
        intent.notes.append(
            f"提示词中识别到 {len(intent.attendee_names)} 个观演人，但购票张数是 {intent.quantity}，"
            "建议改成一致后再执行 apply / probe"
        )

    if not intent.date:
        intent.notes.append("提示词中未识别到明确日期，后续需要基于查询结果确认场次")

    if not intent.price_hint:
        intent.notes.append("提示词中未识别到明确票档偏好，后续会使用查询结果确认票档")

    # issue #45 可观测性：票价/区间是在剥离日程片段后解析出来的时，
    # 同时写入 notes（summary「提示:」段可见）与 diagnostics（不可执行路径可见）
    price_recognized = numeric_price is not None or (
        price_min is not None and price_max is not None
    )
    schedule_fragments_stripped = (
        _strip_schedule_fragments(normalized_prompt) != normalized_prompt
    )
    if price_recognized and schedule_fragments_stripped:
        intent.notes.append(_SCHEDULE_STRIP_NOTE)

    diagnostics: list[str] = []
    confidence = 0.0
    if intent.search_keyword:
        confidence += 0.4
        diagnostics.append(f"识别搜索关键词：{intent.search_keyword}（+0.40）")
    if intent.attendee_names:
        confidence += 0.30
        diagnostics.append(
            f"识别 {len(intent.attendee_names)} 位观演人：{'、'.join(intent.attendee_names)}（+0.30）"
        )
    else:
        diagnostics.append("未识别到观演人，apply 模式必须补充姓名")
    if intent.date:
        confidence += 0.15
        diagnostics.append(f"识别目标日期：{intent.date}（+0.15）")
    else:
        diagnostics.append("未识别到具体日期，将依赖页面候选场次")
    if intent.numeric_price_hint or intent.price_hint:
        confidence += 0.10
        diagnostics.append(
            f"识别票档偏好：{intent.price_hint or intent.numeric_price_hint}（+0.10）"
        )
    else:
        diagnostics.append("未识别到票档偏好，将依赖页面默认推荐")
    if price_recognized and schedule_fragments_stripped:
        diagnostics.append(_SCHEDULE_STRIP_NOTE)
    if intent.city:
        confidence += 0.05
        diagnostics.append(f"识别目标城市：{intent.city}（+0.05）")

    return ParseResult(
        intent=intent,
        matched_item=None,
        matched_session=intent.date,
        matched_price=intent.price_hint,
        diagnostics=diagnostics,
        confidence=round(confidence, 3),
    )


def _extract_digits(text: str) -> Optional[int]:
    match = re.search(r"([1-9]\d{1,4})", text or "")
    if match:
        return int(match.group(1))
    return None


def is_price_option_available(option: dict) -> bool:
    tag = (option.get("tag") or "").strip()
    return tag not in _UNAVAILABLE_TAGS


def _score_numeric_match(target: int, option_digits: int) -> tuple[int, str]:
    """对单值数字 hint 进行宽容打分，返回 (分数, 说明)。"""
    if option_digits == target:
        return 100, f"{option_digits} 元 = {target} 元 精确命中（+100）"
    ratio = abs(option_digits - target) / target
    if ratio <= 0.10:
        # ±10%：精确点 80 分，到容忍边界 60 分（线性递减）
        tolerance_score = round(80 - (ratio / 0.10) * 20)
        return tolerance_score, (
            f"{option_digits} 元 ≈ {target} 元（差 {ratio * 100:.1f}%，±10% 容忍 +{tolerance_score}）"
        )
    return 0, f"{option_digits} 元 与 {target} 元 偏差 {ratio * 100:.1f}% 超出容忍区间"


def _score_range_match(low: int, high: int, option_digits: int) -> tuple[int, str]:
    """区间 hint 命中评分。命中区间 +50；其它情况 0。"""
    if low <= option_digits <= high:
        return 50, f"{option_digits} 元 落在区间 [{low}, {high}] 内（+50）"
    return 0, f"{option_digits} 元 不在区间 [{low}, {high}] 内"


def score_price_option(intent: PromptIntent, option: dict) -> int:
    """对单个 UI 票档进行打分。

    评分构成（与 ``ParseResult.diagnostics`` 同步）：
    - 文字 hint 包含：+120
    - seat_hint 包含：+80
    - 数字 hint 精确：+100；±10% 容忍：60-80 线性递减；超过：0
    - 区间 hint 命中：+50
    - 不可用 tag：-1000；常规可预约/预售：+10
    """
    text = option.get("text") or ""
    tag = option.get("tag") or ""
    normalized_text = normalize_text(text)
    score = 0

    if not is_price_option_available(option):
        score -= 1000

    if intent.price_hint and normalize_text(intent.price_hint) in normalized_text:
        score += 120

    if intent.seat_hint and normalize_text(intent.seat_hint) in normalized_text:
        score += 80

    digits = _extract_digits(text)
    if digits is not None:
        if (
            intent.numeric_price_min is not None
            and intent.numeric_price_max is not None
        ):
            range_score, _ = _score_range_match(
                intent.numeric_price_min, intent.numeric_price_max, digits
            )
            score += range_score
        elif intent.numeric_price_hint is not None:
            single_score, _ = _score_numeric_match(intent.numeric_price_hint, digits)
            score += single_score

    if tag in {"可预约", "预售", "可选"}:
        score += 10

    return score


_NEAREST_NEIGHBOR_MAX_RATIO = 0.50


def _nearest_numeric_option(target: int, options: Iterable[dict]) -> Optional[dict]:
    """返回价格数字与 ``target`` 距离最近且可用的票档。

    距离超过 ``±50%`` 不接受（避免 999 元 hint 错配到 380 元这类离谱情况）。
    """
    nearest = None
    nearest_distance = None
    for option in options:
        if not is_price_option_available(option):
            continue
        digits = _extract_digits(option.get("text") or "")
        if digits is None:
            continue
        distance = abs(digits - target)
        if nearest_distance is None or distance < nearest_distance:
            nearest = option
            nearest_distance = distance
    if nearest is None or nearest_distance is None:
        return None
    if nearest_distance / max(target, 1) > _NEAREST_NEIGHBOR_MAX_RATIO:
        return None
    return nearest


def choose_price_option(
    intent: PromptIntent, options: Iterable[dict]
) -> Optional[dict]:
    options_list = list(options)
    if not options_list:
        return None

    ranked = []
    for option in options_list:
        scored = dict(option)
        scored["score"] = score_price_option(intent, option)
        ranked.append(scored)

    ranked.sort(key=lambda item: item["score"], reverse=True)
    best = ranked[0]

    has_numeric = (
        intent.numeric_price_hint is not None or intent.numeric_price_max is not None
    )

    # 数字 hint：可以接受最近邻 fallback（30 分）
    if has_numeric and best["score"] < 60:
        target = intent.numeric_price_hint
        if target is None and intent.numeric_price_max is not None:
            mid = (intent.numeric_price_min + intent.numeric_price_max) // 2
            target = mid
        if target is not None:
            nearest = _nearest_numeric_option(target, options_list)
            if nearest is not None:
                fallback = dict(nearest)
                fallback["score"] = 30
                fallback["match_reason"] = "nearest_neighbor"
                return fallback
        return None

    # 仅文字 hint（如「内场」无具体价格）：保留严格阈值，避免错配看似无关的票档
    if intent.price_hint and not has_numeric and best["score"] < 100:
        return None

    if not intent.price_hint and not is_price_option_available(best):
        return None

    return best
