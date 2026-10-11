"""Guangzhou Damai purchase: one verified order submission, manual payment."""
import ctypes
import datetime as dt
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from xml.etree import ElementTree

import numpy as np

from .rehearsal_runtime import (CHINA_TZ, ROOT, Phone, Reader, RunLock,
                               StaleFrameError, SubmissionJournal, same_content)
from .rehearsal_logic import BLOCKERS, analyze
from .selection_logic import is_sku_page, select_step
from .order_logic import decide_order


from ticketdesk.paths import DATA, TARGET_PATH
TARGET = json.loads(TARGET_PATH.read_text(encoding='utf-8'))
EVENT_URL = f"https://detail.damai.cn/item.htm?id={TARGET['item_id']}"
SALE = dt.datetime.fromisoformat(TARGET['sale_time']).astimezone(CHINA_TZ)
LOG = DATA / 'purchase-log.txt'


def can_run(now):
    """Avoid an unattended wait overnight or stale-sale execution."""
    remaining = (SALE - now).total_seconds()
    if remaining > 20 * 60:
        return False, '请在开售前20分钟内启动自动购票；现在可先点“检查目标页”。'
    if remaining < -10 * 60:
        return False, '公布的开售时间已过去10分钟以上；自动购票不再启动。'
    return True, ''


def title_anchor(image, rows):
    candidates = [box for text, box in rows if '庾澄庆' in text and box[1] < image.height * .5]
    if len(candidates) != 1:
        return None
    box = candidates[0]
    return box, np.asarray(image.crop(box)).astype(np.int16)


def anchor_matches(image, anchor):
    if anchor is None:
        return False
    box, pixels = anchor
    current = np.asarray(image.crop(box)).astype(np.int16)
    return current.shape == pixels.shape and float(np.mean(np.abs(current-pixels))) < 4


def entry_box_from_xml(xml, title_tokens, label_box):
    """Recheck the already-OCRed entry using a fresh Android UI tree.

    A missing or ambiguous node returns None so the original full-screen OCR
    check remains the fallback. The OCR label must sit inside the buy control.
    """
    try:
        visible = [node for node in ElementTree.fromstring(xml).iter('node')
                   if node.get('visible-to-user') == 'true']
        all_labels = '\n'.join(node.get('text', '') + node.get('content-desc', '')
                               for node in visible)
        if any(word in all_labels for word in BLOCKERS):
            return None
        nodes = [node for node in visible
                 if node.get('package') == 'cn.damai' and
                 node.get('visible-to-user') == 'true']
        labels = '\n'.join(node.get('text', '') + node.get('content-desc', '')
                           for node in nodes)
        if not all(token in labels for token in title_tokens):
            return None
        buttons = [node for node in nodes
                   if node.get('resource-id') == 'cn.damai:id/btn_buy_view' and
                   node.get('clickable') == 'true' and node.get('enabled') == 'true']
        if len(buttons) != 1:
            return None
        match = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]',
                             buttons[0].get('bounds', ''))
        if match is None:
            return None
        box = tuple(map(int, match.groups()))
        x1, y1, x2, y2 = box
        label_x = (label_box[0] + label_box[2]) / 2
        label_y = (label_box[1] + label_box[3]) / 2
        if not (x1 < x2 and y1 < y2 and
                x1 <= label_x <= x2 and y1 <= label_y <= y2):
            return None
        return box
    except (ElementTree.ParseError, ValueError, TypeError):
        return None


def prepared_phase(rows, image, target):
    """Keep a validated in-app page instead of reopening the deep link."""
    if is_sku_page(rows, image, target):
        return 'date'
    labels = '\n'.join(text.replace(' ', '') for text, _ in rows)
    if all(token in labels for token in target['title_tokens']) and any(
            text.replace(' ', '') in ('提交订单', '确认订单', '立即提交')
            and box[1] >= image.height*.72 for text, box in rows):
        return 'order'
    state = analyze(rows, image.size, target['title_tokens'])
    if state.kind in ('entry', 'wait'):
        return 'detail'
    return None


class PurchaseApp:
    def __init__(self, emit, stopped):
        self.emit = emit
        self.stopped = stopped
        self.reader = None

    def observe(self, phone, reader, anchor=None, order_baseline=None):
        started = time.monotonic()
        image = phone.screen()
        captured = time.monotonic()
        if order_baseline is not None and same_content(order_baseline, image):
            rows = reader.read_order_recheck(image)
            source = '订单局部复核'
        elif anchor_matches(image, anchor):
            rows = reader.read(image, bottom=.82)
            rows.append(('庾澄庆广州', (0, 0, 1, 1)))
            source = '底栏快读'
        else:
            rows = reader.read(image)
            source = '全屏识别'
        recognized = time.monotonic()
        self.emit('image', image)
        self.emit('log', f'{source}：截图 {captured-started:.2f}s，识别 {recognized-captured:.2f}s。')
        return image, rows, recognized-started

    def worker(self, execute):
        times = []
        try:
            with RunLock(DATA / 'purchase.lock'):
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
                phone = Phone()
                phone.connect()
                journal = SubmissionJournal(DATA / 'purchase-submission.json')
                if execute and journal.attempted():
                    raise RuntimeError('本场已有提交尝试记录；请先在大麦“我的订单”核实，程序不会重复提交。')
                self.emit('log', 'USB 已连接；付款必须在大麦 App 由你完成。')
                if self.reader is None:
                    self.reader = Reader()
                reader = self.reader
                fast_device = None
                try:
                    import uiautomator2 as u2
                    fast_device = u2.connect(phone.serial)
                    self.emit('log', '手机控件快读已就绪；识别不全时仍用原全屏复核。')
                except Exception:
                    self.emit('log', '手机控件快读不可用，使用原全屏复核。')
                phase = None
                if phone.foreground():
                    image, rows, _ = self.observe(phone, reader)
                    phase = prepared_phase(rows, image, TARGET)
                    if phase:
                        self.emit('log', f'保留已打开的目标页面（{phase}），不重新加载。')
                    else:
                        current = analyze(rows, image.size, TARGET['title_tokens'])
                        if current.kind == 'stop':
                            raise RuntimeError(current.message)
                if phase is None:
                    if not phone.foreground() and 'AlipayGphone' in phone.focused_window():
                        raise RuntimeError('手机正在支付页面；请先核实订单，程序不会重新打开演出。')
                    try:
                        phone.cmd('shell', 'am', 'start', '-a', 'android.intent.action.VIEW',
                                  '-d', EVENT_URL, '-p', 'cn.damai')
                        self.emit('log', '已要求大麦打开广州站详情页。')
                        self.stopped.wait(1.5)
                    except (RuntimeError, subprocess.TimeoutExpired):
                        self.emit('log', '自动打开失败；检查手机当前是否已在目标演出页。')
                    if self.stopped.is_set():
                        return
                    if not phone.foreground():
                        raise RuntimeError('大麦不在前台；请在手机打开目标演出详情页。')
                    image, rows, _ = self.observe(phone, reader)
                    phase = prepared_phase(rows, image, TARGET)
                    if phase is None:
                        state = analyze(rows, image.size, TARGET['title_tokens'])
                        raise RuntimeError(state.message)
                anchor = title_anchor(image, rows) if phase == 'detail' else None
                self.emit('status', f'目标页已核实（{phase}）；准备等待开售。' if execute
                          else f'目标页已核实（{phase}）；本次只检查，没有点击。')
                if not execute:
                    return
                while not self.stopped.is_set():
                    remaining = (SALE - dt.datetime.now(CHINA_TZ)).total_seconds()
                    if remaining <= 0:
                        break
                    self.emit('status', f'距广州站预售 {remaining:.0f} 秒；手机请保持解锁。')
                    self.stopped.wait(min(1, remaining))
                deadline = time.monotonic() + 90
                taps = 0
                transitional = 0
                verification_notified = False
                while not self.stopped.is_set() and time.monotonic() < deadline:
                    image, rows, elapsed = self.observe(phone, reader, anchor if phase == 'detail' else None)
                    times.append(elapsed)
                    self.emit('stage', phase)
                    if phase == 'detail':
                        state = analyze(rows, image.size, TARGET['title_tokens'])
                        self.emit('status', state.message)
                        if is_sku_page(rows, image, TARGET):
                            phase = 'date'
                            self.emit('log', '页面已进入目标选票页，开始核对场次、票档和数量。')
                            continue
                        if any(text.replace(' ', '') in ('提交订单', '确认订单', '立即提交')
                               for text, _ in rows):
                            phase = 'order'
                            self.emit('log', '页面已进入订单页，开始独立核对。')
                            continue
                        if state.kind in ('mismatch', 'stop'):
                            break
                        if state.kind == 'entry':
                            # The OCR label and a fresh UI tree must agree. If
                            # the app draws its CTA without an accessible node,
                            # retain the original full-screen OCR recheck.
                            checked_box = None
                            tap_image = image
                            if fast_device is not None:
                                try:
                                    checked_box = entry_box_from_xml(
                                        fast_device.dump_hierarchy(),
                                        TARGET['title_tokens'], state.box)
                                except Exception:
                                    pass
                            if checked_box is None:
                                image2, rows2, _ = self.observe(phone, reader)
                                checked = analyze(rows2, image2.size,
                                                  TARGET['title_tokens'])
                                if checked.kind != 'entry':
                                    if is_sku_page(rows2, image2, TARGET):
                                        phase = 'date'
                                        self.emit('log', '复核时已进入选票页，跳过入口点击。')
                                        continue
                                    if any(text.replace(' ', '') in ('提交订单', '确认订单', '立即提交')
                                           for text, _ in rows2):
                                        phase = 'order'
                                        self.emit('log', '复核时已进入订单页，跳过入口点击。')
                                        continue
                                    if checked.kind in ('mismatch', 'stop'):
                                        raise RuntimeError(checked.message)
                                    self.emit('log', '入口画面正在变化；未点击，继续观察。')
                                    continue
                                checked_box = checked.box
                                tap_image = image2
                            else:
                                self.emit('log', '入口已由独立手机控件树快速复核。')
                            if self.stopped.is_set():
                                break
                            try:
                                if not phone.tap(checked_box, self.stopped, tap_image):
                                    break
                            except StaleFrameError:
                                self.emit('log', '购票入口画面已变化，重新观察后再决定。')
                                continue
                            taps += 1
                            self.emit('action', '已执行一次已核对的页面点击。')
                            phase = 'date'
                            self.emit('log', '已点击购票入口，等待选票页面。')
                            self.stopped.wait(.35)
                            continue
                    elif phase in ('order', 'submitted'):
                        submitted = phase == 'submitted'
                        decision = decide_order(rows, image, TARGET, submitted=submitted)
                        self.emit('status', decision.message)
                        if decision.kind == 'done':
                            if submitted:
                                journal.complete()
                            self.emit('stage', 'payment')
                            self.emit('outcome', ('payment', decision.message))
                            break
                        if decision.kind == 'verify':
                            self.emit('stage', 'verification')
                            if not verification_notified:
                                self.emit('log', '大麦要求图片验证；请在手机上完成，程序不再点击。')
                                verification_notified = True
                                # After the one submitted order, observe the
                                # result while the user completes verification.
                                deadline = max(deadline, time.monotonic() + 120)
                            self.stopped.wait(.6)
                            continue
                        if decision.kind == 'stop':
                            break
                        if decision.kind == 'submit':
                            # A second screenshot must reproduce all facts and
                            # the exact fingerprint before the only submit tap.
                            image2, rows2, _ = self.observe(
                                phone, reader, order_baseline=image)
                            checked = decide_order(rows2, image2, TARGET)
                            if (checked.kind != 'submit' or
                                    checked.fingerprint != decision.fingerprint):
                                # A different layout or cropped OCR miss must
                                # not masquerade as a conflicting order.
                                rows2_full = reader.read(image2)
                                checked = decide_order(rows2_full, image2, TARGET)
                                self.emit('log', '订单局部复核不完整，已用同一画面整屏复核。')
                                if (checked.kind != 'submit' or
                                        checked.fingerprint != decision.fingerprint):
                                    raise RuntimeError('订单二次核对不一致，未提交。')
                            if self.stopped.is_set():
                                break
                            try:
                                phone.refresh_if_stale(image2)
                            except StaleFrameError:
                                self.emit('log', '订单页面在复核后变化，重新核对，不提交。')
                                continue
                            journal.claim(checked.fingerprint)
                            phase = 'submitted'
                            if not phone.tap(checked.box, self.stopped, image2):
                                raise RuntimeError('提交前任务已停止；提交记录已保留，请先核实订单。')
                            self.emit('submit_count', 1)
                            self.emit('action', '提交一次订单，等待官方结果。')
                            self.emit('log', '已发送一次提交订单指令；后续只查看结果，不再点击。')
                            self.emit('status', '已发送一次提交订单指令，等待大麦确认；付款由你完成。')
                            self.stopped.wait(.3)
                            continue
                    else:
                        # Reservation may preselect the SKU and skip the sheet.
                        # Treat a direct order page exactly like the normal
                        # order path; never press its button without all facts.
                        if any(text.replace(' ', '') in ('提交订单', '确认订单', '立即提交')
                               for text, _ in rows):
                            phase = 'order'
                            self.emit('log', '已到订单页，开始核对演出、场次、票档、张数和观演人。')
                            continue
                        step = select_step(rows, image, TARGET, phase)
                        # Advancing through already-selected date, tier and
                        # quantity needs no new screenshot: no UI action has
                        # changed the evidence in this frame.
                        for _ in range(4):
                            if step.kind != 'next':
                                break
                            phase = step.key
                            self.emit('log', f'选票阶段：{phase}。')
                            step = select_step(rows, image, TARGET, phase)
                        else:
                            raise RuntimeError('选票阶段异常循环，自动下单停止。')
                        self.emit('status', step.message)
                        if step.kind == 'stop' and phase == 'date' and transitional < 2:
                            # The first frames after entry can still be the detail page.
                            transitional += 1
                            self.stopped.wait(.25)
                            continue
                        if step.kind in ('stop', 'done'):
                            break
                        if step.kind == 'wait':
                            transitional += 1
                            if transitional > 3:
                                raise RuntimeError('选中状态未更新，自动下单停止；不重复点击。')
                            self.stopped.wait(.25)
                            continue
                        transitional = 0
                        if step.kind == 'tap':
                            if taps >= 4:
                                raise RuntimeError('达到点击上限，自动下单停止。')
                            # Recheck every selection against a new full screenshot.
                            image2, rows2, _ = self.observe(phone, reader)
                            checked = select_step(rows2, image2, TARGET, phase)
                            if checked.kind != 'tap' or checked.key != step.key:
                                raise RuntimeError('选票信息复核不一致，未继续点击。')
                            try:
                                if not phone.tap(checked.box, self.stopped, image2):
                                    break
                            except StaleFrameError:
                                self.emit('log', '选票页面已变化，重新观察后再决定。')
                                continue
                            taps += 1
                            self.emit('action', '已执行一次已核对的页面点击。')
                            self.emit('log', checked.message)
                            if checked.key == 'sku-confirm':
                                phase = 'order'
                                self.emit('status', '已确认选票，等待订单页并核对全部信息。')
                                self.stopped.wait(.4)
                                continue
                            phase = 'date_verify' if checked.key == 'date' else 'tier_verify'
                            self.stopped.wait(.25)
                            continue
                    self.stopped.wait(.1)
                else:
                    if not self.stopped.is_set():
                        self.emit('status', '90秒购票窗口结束；请在大麦核实页面与订单状态。')
        except Exception as exc:
            self.emit('outcome', ('error', str(exc)))
            self.emit('status', str(exc))
            self.emit('log', str(exc))
        finally:
            if times:
                self.emit('log', f'每轮观察＋识别：中位数 {np.median(times):.2f}s，P95 {np.percentile(times,95):.2f}s。')
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            self.emit('done', None)

