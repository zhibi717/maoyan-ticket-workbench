import base64
import copy
import datetime as dt
import io
import json
import threading
import time
from .paths import DATA
from .profiles import ProfileStore
from .simulation import Simulation, SCENARIOS


class Controller:
    def __init__(self):
        self.profiles = ProfileStore()
        self.selected_id = None
        self.target = None
        self.lock = threading.RLock()
        self.stopped = threading.Event()
        self.verified = threading.Event()
        self.thread = None
        self.clock = None
        self.last_seen = time.monotonic()
        self.active_process = None
        self.state = self.fresh_state()
        selection = DATA / 'selected-profile.txt'
        if selection.exists():
            candidate = selection.read_text(encoding='utf-8').strip()
            if self.profiles.get(candidate):
                self.selected_id = candidate

    def fresh_state(self):
        return dict(mode='simulation', scenario='captcha', busy=False, stage='ready',
                    status='准备就绪。选择场景，开始一次完整演练。', logs=[],
                    actions=0, submit_count=0, decision_ms=0, elapsed=0,
                    outcome='', preview='', milestones={}, revision=0)

    def snapshot(self):
        with self.lock:
            result = copy.deepcopy(self.state)
            if result['busy'] and self.clock is not None:
                result['elapsed'] = round(time.monotonic()-self.clock, 2)
            profile = self.profiles.get(self.selected_id) if self.selected_id else None
            result['profile'] = profile
            result['profiles'] = [dict(id=p['id'], title=p['title'], city=p['city'], platform=p['platform'],
                                       sale_time=p['sale_time']) for p in self.profiles.all()]
            if profile:
                sale = dt.datetime.fromisoformat(profile['sale_time'])
                remaining = (sale-dt.datetime.now(sale.tzinfo)).total_seconds()
                result['live_allowed'] = -1200 <= remaining <= 600
                result['live_reason'] = ('在这场演出的开售窗口内。' if result['live_allowed']
                                         else '这场演出尚未到开售前 10 分钟，或开售已超过 20 分钟。')
            else:
                result['live_allowed'] = False
                result['live_reason'] = '先保存并选择一场演出。'
            result['scenarios'] = SCENARIOS
            return result

    def save_profile(self, raw):
        with self.lock:
            if self.state['busy']:
                raise ValueError('运行中不能修改演出资料。')
            profile = self.profiles.save(raw)
            self.selected_id = profile['id']
            (DATA/'selected-profile.txt').write_text(self.selected_id, encoding='utf-8')
            self.state['status'] = f'已保存并选中：{profile["title"]}。'
            return profile

    def select_profile(self, identifier):
        with self.lock:
            if self.state['busy']:
                raise ValueError('运行中不能切换演出。')
            if not self.profiles.get(identifier):
                raise ValueError('找不到这场演出。')
            self.selected_id = identifier
            (DATA/'selected-profile.txt').write_text(identifier, encoding='utf-8')

    def selected_profile(self):
        profile = self.profiles.get(self.selected_id) if self.selected_id else None
        if not profile:
            raise ValueError('请先填写并保存目标演出。')
        return profile

    def open_autox(self):
        with self.lock:
            if self.state['busy']:
                raise ValueError('请先停止正在运行的任务。')
            profile = self.selected_profile()
            if profile['platform'] != 'maoyan':
                raise ValueError('AutoX 手机端目前只支持猫眼目标。')
            from .autox_phone import open_live_on_phone
            open_live_on_phone(profile)
            self.state['status'] = '已在手机打开 AutoX 脚本；点“运行脚本”并切回目标页。核对票档、1 张和指定观演人后，仅提交明确的订单按钮一次，尝试进入微信付款页后停止，不付款。'
            self.log('AutoX 脚本已打开；核对订单及观演人后提交一次，尝试到微信付款页后停止；确认购票页支持立即支付；收银台需确认微信方式，进入微信后停止。')

    def open_autox_selection_capture(self):
        with self.lock:
            if self.state['busy']:
                raise ValueError('请先停止正在运行的任务。')
            profile = self.selected_profile()
            if profile['platform'] != 'maoyan':
                raise ValueError('记录手机预选目前只支持猫眼。')
            from .autox_phone import open_selection_capture_on_phone
            open_selection_capture_on_phone(profile)
            self.state['status'] = '已打开记录预选文件；运行后 20 秒内切到猫眼预约选档页。仅记录明确选中的场次、票档和 1 张，不点击。'
            self.log('只读预选记录脚本已打开；改选后须重新记录。')

    def open_autox_rehearsal(self):
        with self.lock:
            if self.state['busy']:
                raise ValueError('请先停止正在运行的任务。')
            profile = self.selected_profile()
            if profile['platform'] != 'maoyan':
                raise ValueError('AutoX 选档演练目前只支持猫眼目标。')
            from .autox_phone import open_rehearsal_on_phone
            open_rehearsal_on_phone(profile)
            self.state['status'] = '手机已打开选档演练文件；运行后切回猫眼详情页。演练只选目标票档，不确认或提交订单。'
            self.log('AutoX 选档演练文件已打开；不会确认或提交订单。')

    def open_autox_congestion_probe(self):
        with self.lock:
            if self.state['busy']:
                raise ValueError('请先停止正在运行的任务。')
            profile = self.selected_profile()
            if profile['platform'] != 'maoyan':
                raise ValueError('拥堵只读检查目前只支持猫眼。')
            from .autox_phone import open_congestion_probe_on_phone
            open_congestion_probe_on_phone(profile)
            self.state['status'] = '已打开拥堵只读检查；运行后切回猫眼。仅检查弹窗和刷新按钮，不点击、不提交。'
            self.log('拥堵只读检查已打开；不刷新、不提交、不支付。')

    def emit(self, kind, value):
        with self.lock:
            self.state['revision'] += 1
            if kind == 'started_clock':
                self.clock = time.monotonic()
            elif kind == 'image':
                pic = value.copy()
                pic.thumbnail((360, 780))
                out = io.BytesIO()
                pic.save(out, format='JPEG', quality=80)
                self.state['preview'] = base64.b64encode(out.getvalue()).decode()
            elif kind == 'outcome':
                self.state['outcome'], self.state['status'] = value
            elif kind in ('stage', 'status', 'submit_count', 'decision_ms'):
                self.state[kind] = value
            elif kind == 'milestone':
                name, milliseconds = value
                if name in ('entry_visible', 'first_tap', 'next_page', 'order_visible',
                            'submit_tap', 'captcha_visible', 'payment_visible'):
                    self.state['milestones'][name] = max(0, int(milliseconds))
            elif kind == 'action':
                self.state['actions'] += 1
                self.log(value)
            elif kind == 'log':
                self.log(value)

    def log(self, message):
        self.state['logs'].append(dict(time=dt.datetime.now().strftime('%H:%M:%S'), text=message))
        self.state['logs'] = self.state['logs'][-120:]

    def start(self, mode='simulation', scenario='captcha', *, inspect=False):
        if mode not in ('simulation', 'probe', 'dryrun', 'live') or scenario not in SCENARIOS:
            raise ValueError('模式或场景无效')
        with self.lock:
            if self.state['busy']:
                raise ValueError('已有任务正在运行')
            profile = self.selected_profile()
            if mode == 'live' and profile['platform'] == 'maoyan':
                raise ValueError('猫眼旧 USB 下单入口已停用；请使用下方 AutoX 自动选档并进入订单页的入口。')
            if mode == 'live' and not self.snapshot()['live_allowed']:
                raise ValueError('目标不在开售窗口内，未启动真机下单。')
            if mode == 'live' and self.state.get('submit_count'):
                raise ValueError('已有提交记录，请先核对订单。')
            self.state = self.fresh_state()
            self.state.update(mode=mode, scenario=scenario, busy=True, stage='preflight')
            self.clock = None
            self.stopped.clear()
            self.verified.clear()
            self.thread = threading.Thread(target=self._run, args=(mode, scenario, profile), daemon=True)
            self.thread.start()

    def _run(self, mode, scenario, profile):
        try:
            if mode == 'simulation':
                from .simulation import GenericSimulation
                self.emit('log', '离线演练：使用当前演出资料的合成页面，不连接手机。')
                GenericSimulation(profile, scenario, self.stopped, self.verified, self.emit).run()
            else:
                if profile['platform'] == 'maoyan':
                    from .maoyan import run
                else:
                    from .hatickets import run
                run(self, profile, mode)
        except Exception as exc:
            self.emit('outcome', ('error', str(exc)))
            self.emit('log', f'任务停止：{exc}')
        finally:
            with self.lock:
                if self.stopped.is_set():
                    self.state['outcome'] = 'cancelled'
                    self.state['status'] = '已停止。若在真机提交后停止，请核实平台订单。'
                elif not self.state['outcome']:
                    self.state['outcome'] = 'unverified'
                if self.clock is not None:
                    self.state['elapsed'] = round(time.monotonic()-self.clock, 2)
                self.state['busy'] = False
                report = self.report()
                # No screenshots, names, phone numbers, identities or account data in report.
                temporary = DATA / 'last-run.tmp'
                temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                temporary.replace(DATA / 'last-run.json')

    def report(self):
        with self.lock:
            keys = ('mode', 'scenario', 'stage', 'outcome', 'actions', 'submit_count',
                    'elapsed', 'decision_ms', 'milestones')
            result = {key: self.state[key] for key in keys}
            profile = self.profiles.get(self.selected_id) if self.selected_id else None
            if self.state['mode'] == 'simulation':
                result['timing_note'] = '离线模拟耗时含演示等待，不代表真机购票速度。'
            elif profile and profile['platform'] == 'maoyan':
                result['timing_note'] = 'decision_ms 仅为开售后识别入口耗时，包含 USB 页面读取；不含后续选票、平台排队、验证码与付款。'
            else:
                result['timing_note'] = 'decision_ms 不包含完整购票、平台排队、验证码与付款耗时。'
            result['target'] = ({key: profile[key] for key in ('platform', 'title', 'event_time', 'price', 'city')}
                                if profile else {})
            return result

    def stop(self):
        self.stopped.set()
        process = self.active_process
        if process is not None and process.poll() is None:
            process.terminate()

    def verify(self):
        with self.lock:
            if not (self.state['mode'] == 'simulation' and self.state['busy'] and
                    self.state['stage'] == 'verification'):
                raise ValueError('当前不在模拟验证阶段；真机验证请在手机完成。')
            self.verified.set()

