"""Conservative Maoyan Android UI adapter inspired by public AutoX/ADB projects.

This adapter reads the visible accessibility tree, uses one tap per verified
transition, and never solves a challenge, enters a payment credential, or
retries an uncertain submission.
"""
from __future__ import annotations

import datetime as dt
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .engine.rehearsal_runtime import RunLock
from .hatickets import device_serial
from .paths import DATA

PACKAGE = 'com.sankuai.movie'
ENTRY_TEXTS = ('立即预订', '立即预定', '立即购票', '立即抢购', '立即抢票', '特惠购票', '立即购买')
CHALLENGE_TEXTS = ('图片验证', '图形验证', '安全验证', '请完成验证', '滑块验证', '点击图片')
PAYMENT_TEXTS = ('支付方式', '微信支付', '支付宝支付', '确认付款', '付款方式', '立即支付', '去支付')
SUBMIT_TEXTS = ('提交订单', '立即提交')


def normal_date(value: str) -> str | None:
    for pattern in (r'\d{4}[-/.](\d{1,2})[-/.](\d{1,2})',
                    r'(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?',
                    r'(\d{1,2})[-/.](\d{1,2})'):
        match = re.search(pattern, value)
        if match:
            month, day = map(int, match.groups())
            if 1 <= month <= 12 and 1 <= day <= 31:
                return f'{month:02d}.{day:02d}'
    return None


@dataclass(frozen=True)
class Node:
    text: str
    bounds: tuple[int, int, int, int]
    tap_bounds: tuple[int, int, int, int]
    checked: bool = False
    selected: bool = False

    @property
    def center(self):
        x1, y1, x2, y2 = self.tap_bounds
        return (x1+x2)//2, (y1+y2)//2


def _bounds(value: str):
    numbers = [int(v) for v in re.findall(r'\d+', value)]
    if len(numbers) != 4 or numbers[0] >= numbers[2] or numbers[1] >= numbers[3]:
        return None
    return tuple(numbers)


class Screen:
    def __init__(self, xml: str):
        self.nodes: list[Node] = []
        root = ET.fromstring(xml)
        self.root = root
        self.parents = {child: parent for parent in root.iter() for child in parent}

        def collect(element, clickable_parent=None):
            own = _bounds(element.get('bounds', ''))
            clickable = own if element.get('clickable') == 'true' else clickable_parent
            label = ' '.join(filter(None, (element.get('text', ''), element.get('content-desc', '')))).strip()
            if label and own:
                self.nodes.append(Node(label, own, clickable or own,
                                       element.get('checked') == 'true',
                                       element.get('selected') == 'true'))
            for child in element:
                collect(child, clickable)

        collect(root)

    @property
    def text(self):
        return ' '.join(node.text for node in self.nodes)

    @property
    def signature(self):
        return tuple((n.text, n.bounds, n.checked, n.selected) for n in self.nodes)

    def has(self, *phrases):
        return any(phrase in self.text for phrase in phrases)

    def one(self, *phrases):
        matches = [n for n in self.nodes if any(phrase in n.text for phrase in phrases)]
        points = {n.center: n for n in matches}
        if len(points) != 1:
            raise RuntimeError(f'页面目标控件数量异常（{len(points)}）；已停止，避免误点。')
        return next(iter(points.values()))

    def one_exact(self, *phrases):
        matches = [n for n in self.nodes if n.text in phrases]
        points = {n.center: n for n in matches}
        if len(points) != 1:
            raise RuntimeError(f'页面目标控件数量异常（{len(points)}）；已停止，避免误点。')
        return next(iter(points.values()))

    def date_node(self, expected):
        matches = [n for n in self.nodes if len(n.text) <= 60 and normal_date(n.text) == expected]
        points = {n.center: n for n in matches}
        if len(points) != 1:
            raise RuntimeError('未唯一识别目标场次日期，已停止。')
        return next(iter(points.values()))

    def price_node(self, amount):
        matches = []
        for node in self.nodes:
            label = node.text.replace(',', '')
            numbers = re.findall(r'(?<!\d)\d{2,5}(?!\d)', label)
            if numbers == [str(amount)] and len(label) <= 35 and not any(
                    x in label for x in ('缺货', '售罄', '不可售', '登记')):
                matches.append(node)
        points = {n.center: n for n in matches}
        if len(points) != 1:
            raise RuntimeError('未唯一识别目标票档，已停止。')
        return next(iter(points.values()))

    def appointment_mark(self, *, date=None, amount=None):
        """Locate the unlabelled heart icon beside a reservation choice."""
        if (date is None) == (amount is None):
            raise ValueError('Specify date or amount.')
        matching = []
        for element in self.root.iter():
            label = element.get('text', '')
            if date is not None and normal_date(label) != date:
                continue
            if amount is not None and not re.fullmatch(rf'[¥￥]?\s*{amount}\s*元?', label):
                continue
            own = _bounds(element.get('bounds', ''))
            parent = self.parents.get(element)
            if not own or parent is None:
                continue
            for sibling in parent:
                bounds = _bounds(sibling.get('bounds', ''))
                if not bounds or sibling.get('text') or sibling.get('content-desc'):
                    continue
                if bounds[0] > own[2] and abs((bounds[1]+bounds[3])-(own[1]+own[3])) < 40:
                    matching.append(bounds)
        if len(set(matching)) != 1:
            raise RuntimeError('未能识别预约选择标记，请将目标场次和票档显示在屏幕上。')
        return matching[0]


def _red_pixels(image, bounds):
    count = 0
    crop = image.convert('RGB').crop(bounds)
    pixels = crop.load()
    for y in range(crop.height):
        for x in range(crop.width):
            r, g, b = pixels[x, y]
            if r > 160 and r > g * 1.5 and r > b * 1.4:
                count += 1
    return count


def verify_appointment(screen: Screen, image, profile):
    """On the observed Maoyan reservation page, filled red hearts mean selected."""
    date = normal_date(profile['date'])
    if date is None:
        raise RuntimeError('目标日期格式无法识别。')
    date_heart = screen.appointment_mark(date=date)
    price_heart = screen.appointment_mark(amount=profile['amount'])
    if _red_pixels(image, date_heart) < 100:
        raise RuntimeError('猫眼目标场次尚未预约；请先在手机选中。')
    if _red_pixels(image, price_heart) < 100:
        raise RuntimeError('猫眼目标票档尚未预约；请先在手机选中。')
    return True


def verify_detail(screen: Screen, profile):
    term = profile['keyword'].split()[0]
    if len(term) < 2 or term not in screen.text or profile['city'] not in screen.text:
        raise RuntimeError('猫眼当前页面未显示目标演出与城市；请打开目标详情页。')
    return True


def verify_order(screen: Screen, profile):
    """Fail closed if the order summary cannot prove date, price and attendee."""
    term = profile['keyword'].split()[0]
    if len(term) < 2 or term not in screen.text:
        raise RuntimeError('订单页未显示目标演出名称，已停止。')
    if normal_date(profile['date']) not in {normal_date(n.text) for n in screen.nodes}:
        raise RuntimeError('订单页未显示目标场次日期，已停止。')
    if not re.search(rf'(?<!\d){profile["amount"]}(?!\d)', screen.text):
        raise RuntimeError('订单页未显示目标票价，已停止。')
    if len(profile['users']) != 1 or profile['users'][0] not in screen.text:
        raise RuntimeError('订单页未确认唯一观演人，已停止。')
    if not re.search(r'(?<!\d)1\s*[张份位](?!\d)', screen.text):
        raise RuntimeError('订单页未确认 1 张，已停止。')
    return True


def _read(device, *, fast=False):
    return Screen(device.dump_hierarchy(compressed=fast))


def _tap(device, node):
    device.click(*node.center)


def _mark(emit, name, started):
    emit('milestone', (name, (time.monotonic()-started)*1000))


def _wait_for_change(device, previous_text, stopped, timeout=4):
    until = time.monotonic() + timeout
    while time.monotonic() < until and not stopped.is_set():
        screen = _read(device)
        if screen.signature != previous_text:
            return screen
        stopped.wait(.12)
    raise RuntimeError('点击后页面没有进入下一步，已停止。')


def _wait_for_entry(device, profile, stopped, emit):
    sale = dt.datetime.fromisoformat(profile['sale_time'])
    while not stopped.is_set():
        now = dt.datetime.now(sale.tzinfo)
        if now < sale:
            emit('stage', 'waiting')
            stopped.wait(min((sale-now).total_seconds(), .5))
            continue
        break
    started = time.monotonic()
    deadline = started + 15
    refreshed = 0
    next_refresh = started
    while time.monotonic() < deadline and not stopped.is_set():
        screen = _read(device, fast=True)
        if screen.has(*ENTRY_TEXTS):
            verify_detail(screen, profile)
            latency = int((time.monotonic()-started)*1000)
            emit('decision_ms', latency)
            emit('milestone', ('entry_visible', latency))
            return screen, started
        if screen.has(*CHALLENGE_TEXTS, *PAYMENT_TEXTS):
            raise RuntimeError('猫眼页面已进入验证或支付阶段，请在手机核对，程序不会误点。')
        # Some versions expose a refresh control while reservations turn into
        # sale inventory. Only tap a unique visible refresh control on the
        # verified event page, and limit retries rather than hammering it.
        if refreshed < 3 and time.monotonic() >= next_refresh and screen.has('刷新'):
            verify_detail(screen, profile)
            try:
                refresh = screen.one_exact('刷新', '点击刷新', '刷新页面')
            except RuntimeError:
                refresh = None
            if refresh is not None:
                _tap(device, refresh)
                refreshed += 1
                next_refresh = time.monotonic() + 1
                emit('action', '已点击猫眼页面刷新控件一次。')
                continue
    raise RuntimeError('开售后未读到猫眼购票入口；请核对页面或手动操作。')


def _tap_entry_bounded(device, entry, profile, stopped, emit, *, origin=None,
                       retry_delay=.65, timeout=6):
    """Retry the entry coordinate only while the same verified CTA remains visible."""
    origin = time.monotonic() if origin is None else origin
    original = entry.signature
    coordinate = entry.one(*ENTRY_TEXTS).center
    attempts = 0
    next_retry = time.monotonic()
    deadline = next_retry + timeout
    while time.monotonic() < deadline and not stopped.is_set():
        if time.monotonic() >= next_retry and attempts < 3:
            can_tap = True
            if attempts:
                # A changed page is never a reason to reuse the old coordinate.
                current = _read(device)
                if current.signature != original:
                    can_tap = False
                elif current.has(*ENTRY_TEXTS):
                    verify_detail(current, profile)
                    if current.one(*ENTRY_TEXTS).center != coordinate:
                        raise RuntimeError('购票按钮已移动，停止坐标重试。')
                else:
                    can_tap = False
            if can_tap:
                device.click(*coordinate)
                attempts += 1
                emit('action', f'已点击猫眼购票入口第 {attempts} 次。')
                if attempts == 1:
                    _mark(emit, 'first_tap', origin)
                next_retry = time.monotonic() + retry_delay
            else:
                next_retry = deadline
        screen = _read(device)
        if (screen.has(*CHALLENGE_TEXTS, *PAYMENT_TEXTS, *SUBMIT_TEXTS) or
                (screen.has('场次', '票档', '价格') and screen.has('确认', '数量', '张', '份'))):
            _mark(emit, 'next_page', origin)
            return screen
        if screen.signature != original:
            # A loading/transition page may still be in flight. Do not reuse
            # coordinates on it; continue observing for a recognized page.
            next_retry = deadline
        stopped.wait(.06)
    raise RuntimeError('购票入口点击后未出现可确认的下一页；请在手机核对。')



def run(controller, profile, mode):
    if mode not in ('probe', 'dryrun'):
        raise ValueError('猫眼 USB 桌面流程只做只读检查；自动选档请使用 AutoX。')
    import uiautomator2 as u2

    with RunLock(DATA / 'purchase.lock'):
        serial = device_serial(profile)
        controller.emit('started_clock', True)
        controller.emit('stage', 'preflight')
        controller.emit('status', '正在读取猫眼手机页面。')
        device = u2.connect(serial)
        app = device.app_current()
        if app.get('package') != PACKAGE:
            raise RuntimeError('手机当前未显示猫眼 App；请打开目标演出详情页。')
        screen = _read(device)
        verify_detail(screen, profile)
        reservation_editor = screen.has('更新抢票提醒')
        if reservation_editor:
            verify_appointment(screen, device.screenshot(format='pillow'), profile)
        controller.emit('outcome', ('inspected',
            ('猫眼预约设置页已识别，目标场次和票档均已选中；只读检查完成。'
             if reservation_editor else
             ('猫眼演出主详情页及已预约状态已识别；只读检查完成。'
              if screen.has('已 预 约', '已预约') else
              '猫眼目标详情页已识别；但未看到已预约状态，没有点击或下单。'))))
