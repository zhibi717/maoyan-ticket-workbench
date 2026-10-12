import json
import datetime as dt
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch
from ticketdesk.simulation import Simulation
from ticketdesk.controller import Controller
from ticketdesk.server import AppServer
from ticketdesk.paths import TARGET_PATH
from ticketdesk.profiles import ProfileStore, to_hatickets_config
from ticketdesk.hatickets import interpret_result
from ticketdesk.maoyan import Screen, verify_appointment, verify_order, _wait_for_entry, _tap_entry_bounded
from PIL import Image, ImageDraw

TARGET = json.loads(TARGET_PATH.read_text(encoding='utf-8'))
PROFILE = dict(title='测试演唱会·北京站', keyword='测试演唱会 北京', city='北京',
               date='2026-11-14 周六 19:00', event_time='2026-11-14T19:00:00+08:00',
               sale_time='2026-10-20T15:00:00+08:00', price='看台 480 元',
               price_index=0, users=['测试甲', '测试乙'], serial='',
               target_title='', target_venue='')


class SimulationTests(unittest.TestCase):
    def run_scenario(self, scenario, verify=False, stop_stage=None):
        events, stopped, verified = [], threading.Event(), threading.Event()
        def emit(kind, value):
            events.append((kind, value))
            if kind == 'stage' and value == stop_stage:
                stopped.set()
            if kind == 'stage' and value == 'verification' and verify:
                verified.set()
        sim = Simulation(TARGET, scenario, stopped, verified, emit, delay=0, countdown=0)
        with patch('ticketdesk.engine.rehearsal_runtime.Phone.connect', side_effect=AssertionError('simulation touched USB')):
            sim.run()
        return sim, events

    def test_full_flow_uses_shared_decisions_and_single_submit(self):
        sim, events = self.run_scenario('normal')
        self.assertEqual(sim.submissions, 1)
        self.assertIn(('stage', 'order'), events)
        self.assertIn(('stage', 'payment'), events)
        self.assertEqual([v[0] for k,v in events if k == 'outcome'], ['payment'])

    def test_captcha_then_payment(self):
        sim, events = self.run_scenario('captcha', verify=True)
        stages = [v for k,v in events if k == 'stage']
        self.assertLess(stages.index('order'), stages.index('verification'))
        self.assertLess(stages.index('verification'), stages.index('payment'))
        self.assertEqual(sim.submissions, 1)

    def test_sold_out_and_wrong_price_do_not_submit(self):
        for scenario in ('sold_out','mismatch'):
            with self.subTest(scenario=scenario):
                sim, events = self.run_scenario(scenario)
                self.assertEqual(sim.submissions, 0)
                self.assertEqual([v[0] for k,v in events if k == 'outcome'], ['stopped'])

    def test_network_unknown_never_retries(self):
        sim, events = self.run_scenario('network')
        self.assertEqual(sim.submissions, 1)
        self.assertEqual([v[0] for k,v in events if k == 'outcome'], ['unverified'])

    def test_cancel_order_before_submit(self):
        sim, events = self.run_scenario('normal', stop_stage='order')
        self.assertEqual(sim.submissions, 0)
        self.assertNotIn(('stage','payment'), events)

    def test_cancel_captcha_prevents_payment(self):
        sim, events = self.run_scenario('captcha', stop_stage='verification')
        self.assertEqual(sim.submissions, 1)
        self.assertNotIn(('stage','payment'), events)


class ControllerTests(unittest.TestCase):
    def test_live_window_is_ten_minutes_before_and_twenty_after(self):
        with tempfile.TemporaryDirectory() as tmp, patch('ticketdesk.controller.DATA', Path(tmp)), \
             patch('ticketdesk.controller.ProfileStore',
                   lambda: ProfileStore(Path(tmp)/'profiles.json')):
            c = Controller()
            now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
            for offset_minutes, allowed in ((11, False), (9, True),
                                             (-19, True), (-21, False)):
                with self.subTest(offset_minutes=offset_minutes):
                    sale = (now + dt.timedelta(minutes=offset_minutes)).isoformat()
                    c.save_profile({**PROFILE, 'sale_time': sale})
                    self.assertEqual(c.snapshot()['live_allowed'], allowed)

    def test_expired_live_sale_rejected_before_usb_import(self):
        with tempfile.TemporaryDirectory() as tmp, patch('ticketdesk.controller.DATA', Path(tmp)), \
             patch('ticketdesk.controller.ProfileStore',
                   lambda: ProfileStore(Path(tmp)/'profiles.json')):
            c = Controller()
            c.save_profile({**PROFILE, 'sale_time':'2020-01-01T15:00:00+08:00'})
            with self.assertRaises(ValueError): c.start('live')
            self.assertIsNone(c.thread)

    def test_reject_duplicate_running_task_and_invalid_verify(self):
        with tempfile.TemporaryDirectory() as tmp, patch('ticketdesk.controller.DATA', Path(tmp)), \
             patch('ticketdesk.controller.ProfileStore', lambda: ProfileStore(Path(tmp)/'profiles.json')):
            c = Controller()
            c.save_profile(PROFILE)
            c.start('simulation')
            with self.assertRaises(ValueError): c.start('simulation')
            with self.assertRaises(ValueError): c.verify()
            c.stop()
            c.thread.join(3)
            self.assertFalse(c.state['busy'])
            self.assertEqual(c.state['outcome'], 'cancelled')
            report=json.loads((Path(tmp)/'last-run.json').read_text(encoding='utf-8'))
            self.assertNotIn('preview', report)
            self.assertNotIn('logs', report)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.profile_patch = patch('ticketdesk.controller.ProfileStore',
                lambda: ProfileStore(Path(self.temp.name)/'profiles.json'))
        self.data_patch = patch('ticketdesk.controller.DATA', Path(self.temp.name))
        self.profile_patch.start(); self.data_patch.start()
        self.server = AppServer(('127.0.0.1',0))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.data_patch.stop(); self.profile_patch.stop(); self.temp.cleanup()

    def request(self, path, data=None, token=True, origin=None):
        headers = {'Content-Type':'application/json'}
        if token: headers['X-App-Token']=self.server.token
        if origin: headers['Origin']=origin
        return urllib.request.urlopen(urllib.request.Request(self.server.origin+path,
            data=None if data is None else json.dumps(data).encode(), headers=headers), timeout=3)

    def test_api_requires_token_and_rejects_foreign_origin(self):
        for kwargs in ({'token':False}, {'origin':'https://example.com'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(urllib.error.HTTPError) as exc:
                self.request('/api/state', **kwargs)
            self.assertEqual(exc.exception.code,403)

    def test_state_and_default_mode(self):
        with self.request('/api/state') as response:
            data=json.load(response)
        self.assertEqual(data['mode'],'simulation')
        self.assertFalse(data['busy'])
        self.assertIsNone(data['profile'])
        self.assertFalse(data['live_allowed'])

    def test_invalid_mode_and_real_verification_rejected(self):
        for path,data in (('/api/start',{'mode':'invalid'}),('/api/verify',{})):
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as exc:
                self.request(path,data)
            self.assertEqual(exc.exception.code,400)

    def test_autox_can_transfer_before_sale_window(self):
        profile = {**PROFILE, 'platform': 'maoyan', 'users': ['测试甲'],
                   'sale_time': '2026-10-20T15:00:00+08:00'}
        with self.request('/api/profile/save', profile):
            pass
        with patch('ticketdesk.autox_phone.open_live_on_phone') as open_phone:
            with self.request('/api/autox/open', {}) as response:
                self.assertTrue(json.load(response)['ok'])
        open_phone.assert_called_once()

    def test_autox_selection_rehearsal_can_open_before_sale_without_live_attempt(self):
        profile = {**PROFILE, 'platform': 'maoyan', 'users': ['测试甲'],
                   'sale_time': '2026-10-20T15:00:00+08:00'}
        with self.request('/api/profile/save', profile):
            pass
        with patch('ticketdesk.autox_phone.open_rehearsal_on_phone') as open_phone:
            with self.request('/api/autox/rehearsal/open', {}) as response:
                self.assertTrue(json.load(response)['ok'])
        open_phone.assert_called_once()

    def test_autox_congestion_probe_can_open_before_sale_without_live_attempt(self):
        profile = {**PROFILE, 'platform': 'maoyan', 'users': ['测试甲'],
                   'sale_time': '2026-10-20T15:00:00+08:00'}
        with self.request('/api/profile/save', profile):
            pass
        with patch('ticketdesk.autox_phone.open_congestion_probe_on_phone') as open_phone:
            with self.request('/api/autox/congestion/probe', {}) as response:
                self.assertTrue(json.load(response)['ok'])
        open_phone.assert_called_once()

    def test_capture_route_is_authenticated_and_preserves_selected_source(self):
        profile = {**PROFILE, 'platform': 'maoyan', 'users': ['测试甲'], 'selection_source': 'phone'}
        with self.request('/api/profile/save', profile):
            pass
        with patch('ticketdesk.autox_phone.open_selection_capture_on_phone') as open_phone:
            with self.assertRaises(urllib.error.HTTPError) as exc:
                self.request('/api/autox/selection/capture', {}, token=False)
            self.assertEqual(exc.exception.code, 403)
            open_phone.assert_not_called()
            with self.request('/api/autox/selection/capture', {}) as response:
                self.assertTrue(json.load(response)['ok'])
            self.assertEqual(open_phone.call_args.args[0]['selection_source'], 'phone')

    def test_benchmark_route_opens_only_explicitly_and_requires_authentication(self):
        profile={**PROFILE,'platform':'maoyan','users':['测试甲']}
        with self.request('/api/profile/save',profile):
            pass
        with patch('ticketdesk.autox_phone.open_first_click_benchmark_on_phone') as open_phone:
            with self.request('/api/state') as response:
                self.assertFalse(json.load(response)['busy'])
            open_phone.assert_not_called()
            for kwargs in ({'token':False},{'origin':'https://example.com'}):
                with self.assertRaises(urllib.error.HTTPError) as exc:
                    self.request('/api/autox/benchmark/open',{},**kwargs)
                self.assertEqual(exc.exception.code,403)
            open_phone.assert_not_called()
            with self.request('/api/autox/benchmark/open',{}) as response:
                self.assertTrue(json.load(response)['ok'])
            open_phone.assert_called_once()

    def test_benchmark_rejects_wrong_platform_and_running_task(self):
        with patch('ticketdesk.autox_phone.open_first_click_benchmark_on_phone') as open_phone:
            with self.request('/api/profile/save',PROFILE):
                pass
            with self.assertRaises(urllib.error.HTTPError) as exc:
                self.request('/api/autox/benchmark/open',{})
            self.assertEqual(exc.exception.code,400)
            with self.request('/api/profile/save',{**PROFILE,'platform':'maoyan'}):
                pass
            self.server.controller.state['busy']=True
            try:
                with self.assertRaises(urllib.error.HTTPError) as exc:
                    self.request('/api/autox/benchmark/open',{})
                self.assertEqual(exc.exception.code,400)
            finally:
                self.server.controller.state['busy']=False
            open_phone.assert_not_called()

    def test_maoyan_usb_order_route_is_disabled(self):
        profile = {**PROFILE, 'platform': 'maoyan', 'users': ['测试甲'],
                   'sale_time': '2026-10-20T15:00:00+08:00'}
        with self.request('/api/profile/save', profile):
            pass
        with self.assertRaises(urllib.error.HTTPError) as exc:
            self.request('/api/start', {'mode': 'live'})
        self.assertEqual(exc.exception.code, 400)
        with self.request('/api/state') as response:
            self.assertFalse(json.load(response)['busy'])

    def test_save_profile_and_select(self):
        with self.request('/api/profile/save', PROFILE) as response:
            profile=json.load(response)['profile']
        self.assertEqual(profile['users'], ['测试甲','测试乙'])
        with self.request('/api/state') as response:
            state=json.load(response)
        self.assertEqual(state['profile']['id'], profile['id'])
        self.assertEqual(len(state['profiles']), 1)


class ProfileTests(unittest.TestCase):
    def test_selection_source_is_saved_and_legacy_profiles_keep_workbench_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp)/'profiles.json')
            old = store.save(PROFILE)
            self.assertEqual(old['selection_source'], 'workbench')
            new = store.save({**old, 'selection_source': 'phone'})
            self.assertEqual(store.get(new['id'])['selection_source'], 'phone')
            with self.assertRaises(ValueError):
                store.save({**old, 'selection_source': 'unknown'})

    def test_maps_to_upstream_modes_and_multiple_users(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=ProfileStore(Path(tmp)/'profiles.json').save(PROFILE)
        probe=to_hatickets_config(p,'USB123',mode='probe')
        dry=to_hatickets_config(p,'USB123',mode='dryrun')
        live=to_hatickets_config(p,'USB123',mode='live')
        self.assertEqual(len(live['users']),2)
        self.assertEqual((probe['probe_only'],probe['if_commit_order']),(True,False))
        self.assertEqual((dry['probe_only'],dry['if_commit_order']),(False,False))
        self.assertEqual((live['probe_only'],live['if_commit_order']),(False,True))
        self.assertFalse(live['rush_skip_session'])

    def test_invalid_date_and_duplicate_viewer_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=ProfileStore(Path(tmp)/'profiles.json')
            for bad in ({**PROFILE,'sale_time':'tomorrow'},
                        {**PROFILE,'users':['甲','甲']},
                        {**PROFILE,'price':'任意票档'}):
                with self.assertRaises(ValueError): store.save(bad)


class UpstreamAdapterTests(unittest.TestCase):
    def test_only_confirmed_order_outcomes_count_as_submitted(self):
        self.assertEqual(interpret_result('live', {'outcome':'order_pending_payment'}, 0)[0], 'submitted')
        self.assertEqual(interpret_result('live', {'outcome':'validation_ready'}, 0)[0], 'error')
        self.assertEqual(interpret_result('live', {'outcome':'terminal_failure',
            'terminal_reason':'submit_unverified'}, 11)[0], 'unverified')
        self.assertEqual(interpret_result('probe', {'outcome':'preexisting_pending_order'}, 0)[0], 'error')

    def test_multi_session_never_falls_back_to_another_date(self):
        import sys
        from ticketdesk.hatickets import UPSTREAM
        sys.path.insert(0, str(UPSTREAM))
        from mobile.damai_app.orchestrator import DamaiBot
        from mobile.event_navigator import SessionNotFoundError
        from types import SimpleNamespace
        bot = object.__new__(DamaiBot)
        bot.config = SimpleNamespace(date='2026-12-05', city='广州', rush_skip_session=False)
        bot.d = object()
        bot._terminal_failure_reason = None
        with patch('mobile.damai_app.orchestrator.select_session',
                   side_effect=SessionNotFoundError('not found')) as select:
            self.assertFalse(bot._handle_session_picker())
        self.assertIsNone(select.call_args.kwargs['fallback_index'])
        self.assertEqual(bot._terminal_failure_reason, 'session_not_found')


class MaoyanAdapterTests(unittest.TestCase):
    def profile(self):
        return {**PROFILE, 'platform':'maoyan', 'title':'周杰伦广州站',
                'keyword':'周杰伦 广州', 'city':'广州', 'date':'2026-12-05',
                'price':'1380元', 'amount':1380,
                'users':['测试人']}

    def test_maoyan_desktop_adapter_rejects_ordering(self):
        from ticketdesk.maoyan import run
        with self.assertRaisesRegex(ValueError, 'AutoX'):
            run(None, self.profile(), 'live')

    def test_selected_reservation_requires_both_red_marks(self):
        xml = '''<hierarchy>
          <node bounds="[0,0][90,30]">
            <node text="2026-12-05 周六 19:00" bounds="[1,1][45,25]"/>
            <node text="" bounds="[60,1][80,25]"/>
          </node>
          <node bounds="[0,40][90,70]">
            <node text="￥1380" bounds="[1,41][45,65]"/>
            <node text="" bounds="[60,41][80,65]"/>
          </node>
        </hierarchy>'''
        image = Image.new('RGB', (100, 80), 'white')
        paint = ImageDraw.Draw(image)
        paint.rectangle((60, 1, 79, 24), fill='#f43b45')
        paint.rectangle((60, 41, 79, 64), fill='#f43b45')
        screen = Screen(xml)
        self.assertTrue(verify_appointment(screen, image, self.profile()))
        paint.rectangle((60, 41, 79, 64), fill='white')
        with self.assertRaisesRegex(RuntimeError, '票档尚未预约'):
            verify_appointment(screen, image, self.profile())

    def test_order_facts_checked_before_submit(self):
        xml = '''<hierarchy><node bounds="[0,0][100,100]">
          <node text="周杰伦广州站" bounds="[0,0][90,12]"/>
          <node text="2026-12-05 周六 19:00" bounds="[0,13][90,25]"/>
          <node text="￥1380" bounds="[0,26][90,38]"/>
          <node text="测试人" bounds="[0,39][90,51]"/>
          <node text="1张" bounds="[0,52][90,64]"/>
          <node text="立即提交" bounds="[0,65][90,77]"/>
        </node></hierarchy>'''
        self.assertTrue(verify_order(Screen(xml), self.profile()))
        with self.assertRaisesRegex(RuntimeError, '场次日期'):
            verify_order(Screen(xml), {**self.profile(), 'date':'2026-12-04'})

    def test_profile_platform_separates_execution_engines(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile=ProfileStore(Path(tmp)/'profiles.json').save(self.profile())
        self.assertEqual(profile['platform'], 'maoyan')
        with self.assertRaisesRegex(ValueError, '大麦'):
            to_hatickets_config(profile, 'USB123', mode='live')

    def test_sale_entry_refreshes_verified_event_once(self):
        initial = '''<hierarchy><node bounds="[0,0][100,100]">
          <node text="周杰伦 广州" bounds="[0,0][90,20]"/>
          <node text="刷新" bounds="[10,40][60,70]"/>
        </node></hierarchy>'''
        ready = initial.replace('刷新', '立即预订')
        class Device:
            def __init__(self): self.reads=0; self.clicks=[]
            def dump_hierarchy(self, compressed=False):
                self.reads += 1
                return initial if self.reads == 1 else ready
            def click(self, x, y): self.clicks.append((x,y))
        device = Device()
        events = []
        profile = {**self.profile(), 'sale_time':'2020-01-01T13:00:00+08:00'}
        screen, _ = _wait_for_entry(device, profile, threading.Event(),
                                 lambda kind,value: events.append((kind,value)))
        self.assertTrue(screen.has('立即预订'))
        self.assertEqual(device.clicks, [(35,55)])
        self.assertTrue(any(kind == 'decision_ms' for kind,_ in events))

    def test_entry_coordinate_retry_stops_when_next_page_appears(self):
        entry_xml = '''<hierarchy><node bounds="[0,0][100,100]">
          <node text="周杰伦 广州" bounds="[0,0][90,20]"/>
          <node text="立即预订" bounds="[10,40][60,70]"/>
        </node></hierarchy>'''
        sku_xml = '''<hierarchy><node bounds="[0,0][100,100]">
          <node text="场次" bounds="[0,0][50,20]"/>
          <node text="票档" bounds="[0,21][50,40]"/>
          <node text="确认" bounds="[0,41][50,60]"/>
        </node></hierarchy>'''
        class Device:
            def __init__(self): self.clicks=[]
            def dump_hierarchy(self, compressed=False):
                return sku_xml if len(self.clicks) >= 2 else entry_xml
            def click(self, x, y): self.clicks.append((x,y))
        device = Device()
        screen = _tap_entry_bounded(device, Screen(entry_xml), self.profile(),
                                    threading.Event(), lambda *_: None,
                                    retry_delay=0, timeout=1)
        self.assertTrue(screen.has('票档'))
        self.assertEqual(device.clicks, [(35,55), (35,55)])


if __name__ == '__main__':
    unittest.main()

