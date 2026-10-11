"""Offline scenarios exercise the same SKU/order decision functions as USB.

Frames are synthetic observations, not OCR measurements or real inventory.
This module never imports Phone, connects to ADB, or writes a submission journal.
"""
import datetime as dt
import time
from PIL import Image, ImageDraw
from .engine.rehearsal_logic import analyze
from .engine.selection_logic import select_step
from .engine.order_logic import decide_order

SCENARIOS = {'normal': '顺畅流程', 'captcha': '出现图片验证',
             'sold_out': '最低档售罄', 'network': '提交后网络超时',
             'mismatch': '订单金额不符'}
SIZE = (1080, 2376)


def frame(target, page, *, selected_date=False, selected_tier=False, scenario='normal'):
    image = Image.new('RGB', SIZE, 'white')
    draw = ImageDraw.Draw(image)
    event = dt.datetime.fromisoformat(target['event_time'])
    title = (''.join(target['title_tokens']), (50, 100, 980, 170))
    date = (event.strftime('%Y-%m-%d %H:%M'), (80, 390, 790, 470))
    price = target['ticket_tier_cny']
    tier = (f'看台{price}元', (80, 790, 450, 870))
    if page == 'detail':
        rows = [title, ('立即预订', (240, 2170, 980, 2300))]
    elif page == 'sku':
        rows = [title, ('场次', (50, 260, 180, 330)), date,
                ('票档', (50, 620, 180, 690)), tier,
                ('数量', (50, 1900, 170, 1960)), ('1张', (800, 1900, 900, 1960)),
                ('确定', (660, 2170, 980, 2300))]
        if selected_date:
            draw.rectangle(date[1], fill='#ff6800')
        if selected_tier:
            draw.rectangle(tier[1], fill='#ff6800')
        if scenario == 'sold_out':
            rows.append(('售罄', (470, 790, 600, 850)))
    elif page == 'order':
        total = price + 200 if scenario == 'mismatch' else price
        rows = [title, date, (f'¥{price}.00票档×1张', (60, 750, 650, 810)),
                ('实名观演人', (56, 1086, 260, 1134)),
                ('演示观众', (50, 1238, 260, 1308)),
                ('身份证000********000', (56, 1310, 600, 1362)),
                ('配送方式', (54, 1464, 230, 1520)),
                (f'¥{total}.00明细', (56, 2212, 450, 2270)),
                ('立即提交', (680, 2206, 1000, 2270))]
        draw.line([(969, 1283), (979, 1292), (998, 1270)], fill='#f65b18', width=7)
    elif page == 'verification':
        rows = [('图片验证', (200, 600, 850, 720))]
    elif page == 'payment':
        rows = [('大麦', (460, 500, 620, 560)),
                (f'¥{price}.00', (350, 700, 800, 800)),
                ('付款', (240, 2110, 900, 2280))]
    else:
        rows = [('正在提交', (300, 900, 800, 1000))]
    return image, rows


class Simulation:
    def __init__(self, target, scenario, stopped, verified, emit, delay=.35, countdown=3):
        if scenario not in SCENARIOS:
            raise ValueError('未知模拟场景')
        self.target, self.scenario = target, scenario
        self.stopped, self.verified, self.emit = stopped, verified, emit
        self.delay, self.countdown = delay, countdown
        self.submissions = 0
        self.decision_ms = 0.0

    def decide(self, callback, *args, **kwargs):
        started = time.perf_counter()
        result = callback(*args, **kwargs)
        self.decision_ms += (time.perf_counter()-started)*1000
        self.emit('decision_ms', round(self.decision_ms, 2))
        return result

    def show(self, stage, message):
        self.emit('stage', stage)
        self.emit('status', message)
        self.emit('log', message)
        return not self.stopped.wait(self.delay)

    def run(self):
        self.emit('stage', 'waiting')
        for seconds in range(self.countdown, 0, -1):
            self.emit('status', f'模拟开售倒计时 {seconds} 秒')
            if self.stopped.wait(1):
                return
        self.emit('started_clock', True)
        if not self.show('detail', '模拟开售；核对目标演出与“立即预订”。'):
            return
        image, rows = frame(self.target, 'detail')
        decision = self.decide(analyze, rows, image.size, self.target['title_tokens'])
        if decision.kind != 'entry':
            raise RuntimeError(decision.message)
        self.emit('action', '模拟点击：立即预订')
        if not self.show('sku', '自动选择目标场次、最低档和 1 张。'):
            return
        selected_date = selected_tier = False
        phase = 'date'
        for _ in range(12):
            if self.stopped.is_set():
                return
            image, rows = frame(self.target, 'sku', selected_date=selected_date,
                                selected_tier=selected_tier, scenario=self.scenario)
            step = self.decide(select_step, rows, image, self.target, phase)
            if step.kind in ('stop', 'done'):
                self.emit('outcome', ('stopped', step.message))
                return
            if step.kind == 'next':
                phase = step.key
                continue
            if step.kind != 'tap':
                raise RuntimeError('模拟状态未按预期推进')
            self.emit('action', '模拟点击：'+step.message)
            if step.key == 'sku-confirm':
                break
            if step.key == 'date':
                selected_date, phase = True, 'date_verify'
            elif step.key == 'tier':
                selected_tier, phase = True, 'tier_verify'
            if self.stopped.wait(self.delay):
                return
        else:
            raise RuntimeError('模拟选票步骤超限')
        if not self.show('order', '核对订单：演出、场次、480 元、1 张、已选观演人。'):
            return
        image, rows = frame(self.target, 'order', scenario=self.scenario)
        first = self.decide(decide_order, rows, image, self.target)
        second = self.decide(decide_order, rows, image.copy(), self.target)
        if first.kind != 'submit' or second.fingerprint != first.fingerprint:
            self.emit('outcome', ('stopped', first.message))
            return
        if self.stopped.is_set():
            return
        self.submissions += 1
        self.emit('submit_count', self.submissions)
        self.emit('action', '模拟提交一次；没有向大麦发送请求。')
        if self.scenario == 'network':
            self.show('submitted', '模拟服务器无响应；不重复提交。')
            self.emit('outcome', ('unverified', '模拟提交结果未知，需要核实订单。'))
            return
        if self.scenario == 'captcha':
            image, rows = frame(self.target, 'verification')
            decision = self.decide(decide_order, rows, image, self.target, submitted=True)
            if decision.kind != 'verify':
                raise RuntimeError('验证分支识别失败')
            self.emit('stage', 'verification')
            self.emit('status', '模拟验证已出现；点“模拟验证完成”继续。')
            self.emit('log', '等待人工验证；此处是演练按钮，不是验证码破解。')
            while not self.verified.wait(.1):
                if self.stopped.is_set():
                    return
        if self.stopped.is_set():
            return
        if not self.show('payment', '模拟验证通过，进入演示支付页。'):
            return
        image, rows = frame(self.target, 'payment')
        result = self.decide(decide_order, rows, image, self.target, submitted=True)
        if result.kind != 'done':
            raise RuntimeError('模拟付款页识别失败')
        self.emit('outcome', ('payment', '演练完成：到达模拟支付页，未购买、未扣款。'))


class GenericSimulation:
    """UI rehearsal for any saved profile; never pretends to query real stock."""
    def __init__(self, profile, scenario, stopped, verified, emit, delay=.28, countdown=2):
        if scenario not in SCENARIOS:
            raise ValueError('未知模拟场景。')
        self.profile, self.scenario = profile, scenario
        self.stopped, self.verified, self.emit = stopped, verified, emit
        self.delay, self.countdown = delay, countdown
        self.submissions = 0

    def step(self, stage, message):
        self.emit('stage', stage)
        self.emit('status', message)
        self.emit('log', message)
        return not self.stopped.wait(self.delay)

    def run(self):
        p = self.profile
        self.emit('stage', 'waiting')
        for remaining in range(self.countdown, 0, -1):
            self.emit('status', f'模拟开售倒计时 {remaining} 秒')
            if self.stopped.wait(1):
                return
        self.emit('started_clock', True)
        if not self.step('detail', f'模拟识别：{p["title"]}；入口可用。'):
            return
        self.emit('action', '模拟点击购票入口。')
        if not self.step('sku', f'模拟选择：{p["date"]}、{p["price"]}、{len(p["users"])} 张。'):
            return
        if self.scenario == 'sold_out':
            self.emit('outcome', ('stopped', '模拟最低档售罄；未改选更贵票档。'))
            return
        self.emit('action', '模拟确认场次、票档和数量。')
        if not self.step('order', f'模拟订单核对：{p["title"]}、{p["price"]} × {len(p["users"])} 张。'):
            return
        if self.scenario == 'mismatch':
            self.emit('outcome', ('stopped', '模拟订单金额不符；未提交。'))
            return
        self.submissions = 1
        self.emit('submit_count', 1)
        self.emit('action', '模拟提交一次；未向平台发送请求。')
        if self.scenario == 'network':
            self.step('submitted', '模拟提交结果未知；不重复提交。')
            self.emit('outcome', ('unverified', '提交结果未知；请核对订单。'))
            return
        if self.scenario == 'captcha':
            self.emit('stage', 'verification')
            self.emit('status', '模拟图片验证；点“模拟验证完成”继续。')
            self.emit('log', '等待模拟人工验证。')
            while not self.verified.wait(.1):
                if self.stopped.is_set(): return
        if self.stopped.is_set() or not self.step('payment','已进入模拟支付页。'):
            return
        self.emit('outcome', ('payment','演练完成：到达模拟支付页，没有购买或扣款。'))
