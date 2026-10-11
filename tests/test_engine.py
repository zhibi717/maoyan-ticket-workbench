import datetime as dt
import queue
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont


from ticketdesk.engine.purchase_app import SALE, can_run, entry_box_from_xml, prepared_phase
from ticketdesk.engine.purchase_app import PurchaseApp
from ticketdesk.engine.rehearsal_logic import analyze
from ticketdesk.engine.rehearsal_runtime import (Phone, Reader, RunLock, StaleFrameError,
                               SubmissionJournal, same_content)
from ticketdesk.engine.selection_logic import is_sku_page, quantity_one, select_step
from ticketdesk.engine.order_logic import decide_order, order_evidence
import json


SIZE = (1080, 2376)
TITLE = ('庾澄庆广州站', (30, 100, 700, 170))
ENTRY = ('立即购买', (200, 2190, 700, 2270))
TOKENS = ('庾澄庆', '广州')
TARGET = json.loads((Path(__file__).resolve().parents[1] / 'config/target.json').read_text(encoding='utf-8'))


def order():
    rows = [('庾澄庆广州站', (50, 100, 800, 170)),
            ('2026-11-14周六19:00', (50, 400, 800, 470)),
            ('看台480元', (50, 800, 500, 870)),
            ('数量:1张', (50, 1200, 500, 1270)),
            ('观演人:测试用户', (50, 1400, 650, 1470)),
            ('身份证:440********123', (50, 1550, 800, 1620)),
            ('合计:¥480.00', (50, 1800, 600, 1870)),
            ('提交订单', (200, 2170, 900, 2300))]
    image = Image.new('RGB', SIZE, 'white')
    ImageDraw.Draw(image).line([(969, 1443), (979, 1452), (998, 1430)],
                               fill='#f65b18', width=7)
    return image, rows


def actual_order_layout(selected=True):
    image = Image.new('RGB', SIZE, 'white')
    if selected:
        draw = ImageDraw.Draw(image)
        draw.line([(969, 1283), (979, 1292), (998, 1270)],
                  fill='#f65b18', width=7)
    rows = [('确认购买', (410, 146, 668, 222)),
            ('【广州】庾澄庆2026巡回演唱会', (82, 438, 1002, 504)),
            ('广州站', (56, 500, 470, 570)),
            ('2026.11.1419:00', (56, 644, 456, 696)),
            ('¥480.00票档×1张', (62, 750, 430, 802)),
            ('实名观演人', (56, 1086, 246, 1134)),
            ('仅需选择1位;入场需携带对应证件', (62, 1148, 628, 1188)),
            ('测试用户', (50, 1238, 214, 1308)),
            ('身份证440********123', (56, 1310, 516, 1362)),
            ('配送方式', (54, 1464, 216, 1520)),
            ('¥480.00明细', (56, 2212, 416, 2266)),
            ('立即提交', (718, 2206, 928, 2268))]
    return image, rows


def sku(selected_date=False, selected_tier=False, quantity='数量:1张', button='确定', sold_out=False):
    image = Image.new('RGB', SIZE, 'white')
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 48)
    date = ('11月14日周六19:00', (80, 390, 570, 460))
    tier = ('看台480元', (80, 790, 450, 860))
    draw.text((80, 390), date[0], font=font, fill='#ff6800' if selected_date else '#222222')
    draw.text((80, 790), tier[0], font=font, fill='#ff6800' if selected_tier else '#222222')
    rows = [date, tier, (quantity, (80, 1500, 450, 1560)), (button, (250, 2170, 700, 2270))]
    if sold_out:
        rows.append(('售罄', (460, 790, 600, 860)))
    return image, rows


class PurchaseTests(unittest.TestCase):
    def test_fast_entry_recheck_requires_target_and_matching_control(self):
        header = ('<node package="cn.damai" visible-to-user="true" '
                  'text="庾澄庆广州站"/>')
        button = ('<node package="cn.damai" visible-to-user="true" '
                  'resource-id="cn.damai:id/btn_buy_view" clickable="true" '
                  'enabled="true" bounds="[243,2172][1017,2304]"/>')
        xml = '<hierarchy>' + header + button + '</hierarchy>'
        label = (450, 2200, 750, 2260)
        expected = (243, 2172, 1017, 2304)
        self.assertEqual(entry_box_from_xml(xml, TOKENS, label), expected)
        self.assertIsNone(entry_box_from_xml(xml.replace('广州', '深圳'), TOKENS, label))
        self.assertIsNone(entry_box_from_xml(xml.replace('</hierarchy>', button + '</hierarchy>'), TOKENS, label))
        self.assertIsNone(entry_box_from_xml(xml, TOKENS, (0, 2200, 200, 2260)))
        verification = ('<node package="cn.damai" visible-to-user="true" '
                        'text="图片验证"/>')
        self.assertIsNone(entry_box_from_xml(
            xml.replace('</hierarchy>', verification + '</hierarchy>'), TOKENS, label))

    def test_fast_entry_avoids_second_full_ocr_when_ui_tree_agrees(self):
        import uiautomator2

        detail = (Image.new('RGB', SIZE, 'white'), [TITLE, ENTRY])
        xml = ('<hierarchy><node package="cn.damai" visible-to-user="true" '
               'text="庾澄庆广州站"/><node package="cn.damai" '
               'visible-to-user="true" resource-id="cn.damai:id/btn_buy_view" '
               'clickable="true" enabled="true" bounds="[150,2150][900,2300]"/>'
               '</hierarchy>')
        calls, taps = [], []

        class FakePhone:
            serial = 'test-device'
            def connect(self): pass
            def cmd(self, *args, **kwargs): return b''
            def foreground(self): return True
            def tap(self, box, stopped, expected_image=None):
                taps.append(box)
                stopped.set()
                return True

        class FakeJournal:
            def __init__(self, path): pass
            def attempted(self): return False

        class FakeDevice:
            def dump_hierarchy(self): return xml

        app = PurchaseApp.__new__(PurchaseApp)
        app.events = queue.Queue()
        app.stopped = threading.Event()
        app.reader = object()
        app.emit = lambda kind, value: app.events.put((kind, value))
        def observe(phone, reader, anchor=None):
            calls.append(anchor)
            return (*detail, .01)
        app.observe = observe
        with patch('ticketdesk.engine.purchase_app.Phone', FakePhone), patch('ticketdesk.engine.purchase_app.SubmissionJournal', FakeJournal), \
             patch('ticketdesk.engine.purchase_app.SALE', dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))), \
             patch.object(uiautomator2, 'connect', return_value=FakeDevice()):
            app.worker(True)
        self.assertEqual(len(calls), 2)
        self.assertEqual(taps, [(150, 2150, 900, 2300)])

    def test_only_detail_entry_is_clickable(self):
        self.assertEqual(analyze([TITLE, ENTRY], SIZE, TOKENS).kind, 'entry')
        self.assertEqual(analyze([TITLE, ENTRY], SIZE, TOKENS, entered=True).kind, 'done')

    def test_order_sku_and_verification_never_click(self):
        for extra in ('提交订单', '确认订单', '待付款', '支付方式',
                      '选择票档', '选择场次', '购买数量', '验证码'):
            with self.subTest(extra=extra):
                rows = [TITLE, (extra, (50, 800, 600, 900)), ENTRY]
                self.assertNotEqual(analyze(rows, SIZE, TOKENS).kind, 'entry')

    def test_wrong_event_and_ambiguous_button_stop(self):
        self.assertEqual(analyze([('其他演出', TITLE[1]), ENTRY], SIZE, TOKENS).kind,
                         'mismatch')
        rows = [TITLE, ENTRY, ('立即抢票', (200, 2200, 700, 2290))]
        self.assertEqual(analyze(rows, SIZE, TOKENS).kind, 'stop')

    def test_appointment_waits(self):
        self.assertEqual(analyze([TITLE, ('已预约', ENTRY[1])], SIZE, TOKENS).kind,
                         'wait')

    def test_start_window(self):
        self.assertFalse(can_run(SALE-dt.timedelta(hours=1))[0])
        self.assertTrue(can_run(SALE-dt.timedelta(minutes=10))[0])
        self.assertFalse(can_run(SALE+dt.timedelta(minutes=11))[0])

    def test_single_instance_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            lock = Path(temp) / 'run.lock'
            with RunLock(lock):
                with self.assertRaises(RuntimeError):
                    with RunLock(lock):
                        pass

    def test_submission_journal_prevents_a_second_attempt(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = SubmissionJournal(Path(temp) / 'submission.json')
            self.assertFalse(journal.attempted())
            journal.claim('synthetic-fingerprint')
            self.assertTrue(journal.attempted())
            with self.assertRaises(FileExistsError):
                journal.claim('another-fingerprint')
            journal.complete()
            saved = json.loads(journal.path.read_text(encoding='utf-8'))
            self.assertEqual(saved['state'], 'payment_page_observed')
            self.assertEqual(saved['fingerprint'], 'synthetic-fingerprint')

    def test_phone_focus_rejects_notification_overlay(self):
        phone = Phone()
        phone.cmd = lambda *args: b'mCurrentFocus=Window{123 u0 NotificationShade}\n'
        self.assertFalse(phone.foreground())
        phone.cmd = lambda *args: b'mCurrentFocus=Window{123 u0 cn.damai/.DetailActivity}\n'
        self.assertTrue(phone.foreground())

    def test_ocr_recognizes_purchase_entry(self):
        image = Image.new('RGB', SIZE, 'white')
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 48)
        draw.text((60, 200), '庾澄庆广州站', font=font, fill='#202020')
        draw.rectangle((120, 2170, 950, 2300), fill='#ff6800')
        draw.text((400, 2200), '立即购买', font=font, fill='white')
        rows = Reader().read(image)
        self.assertEqual(analyze(rows, image.size, TOKENS).kind, 'entry')

    def test_selection_date_tier_and_quantity(self):
        image, rows = sku()
        self.assertEqual(select_step(rows, image, TARGET, 'date').key, 'date')
        self.assertEqual(select_step(rows, image, TARGET, 'date_verify').kind, 'wait')
        image, rows = sku(selected_date=True)
        self.assertEqual(select_step(rows, image, TARGET, 'date_verify').key, 'tier')
        self.assertEqual(select_step(rows, image, TARGET, 'tier').key, 'tier')
        image, rows = sku(selected_date=True, selected_tier=True)
        self.assertEqual(select_step(rows, image, TARGET, 'tier_verify').key, 'quantity')
        self.assertEqual(select_step(rows, image, TARGET, 'quantity').key, 'confirm')
        self.assertEqual(select_step(rows, image, TARGET, 'confirm').key, 'sku-confirm')

    def test_actual_sku_layout_has_far_right_single_ticket(self):
        image, rows = sku(selected_date=True, selected_tier=True)
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 48)
        draw.text((90, 710), '2026-11-14周六19:00', font=font, fill='#ff6800')
        rows = [TITLE, ('场次', (50, 570, 160, 620)),
                ('票档', (50, 890, 160, 950)),
                ('2026-11-14周六19:00', (90, 710, 540, 760)),
                rows[1], ('数量', (56, 1968, 140, 2020)),
                ('1张', (786, 2006, 898, 2066)),
                ('确定', (780, 2198, 902, 2272))]
        self.assertTrue(quantity_one(rows))
        self.assertTrue(is_sku_page(rows, image, TARGET))
        self.assertEqual(select_step(rows, image, TARGET, 'quantity').key, 'confirm')
        self.assertEqual(select_step(rows, image, TARGET, 'confirm').key, 'sku-confirm')
        wrong = [('2张' if text == '1张' else text, box) for text, box in rows]
        self.assertFalse(quantity_one(wrong))

    def test_entry_recheck_that_has_advanced_to_sku_does_not_tap_entry(self):
        detail = (Image.new('RGB', SIZE, 'white'), [TITLE, ENTRY])
        image, base = sku(selected_date=True, selected_tier=True)
        sheet = (image, [TITLE, ('场次', (50, 300, 150, 360)),
                         ('票档', (50, 700, 150, 760))] + base)
        frames = iter([detail, detail, sheet, sheet, sheet])
        taps = []

        class FakePhone:
            def connect(self): pass
            def cmd(self, *args, **kwargs): return b''
            def foreground(self): return True
            def tap(self, box, stopped, expected_image=None):
                taps.append(box)
                stopped.set()
                return True

        class FakeJournal:
            def __init__(self, path): pass
            def attempted(self): return False

        app = PurchaseApp.__new__(PurchaseApp)
        app.events = queue.Queue()
        app.stopped = threading.Event()
        app.reader = object()
        app.emit = lambda kind, value: app.events.put((kind, value))
        app.observe = lambda phone, reader, anchor=None, **kwargs: (*next(frames), .01)
        with patch('ticketdesk.engine.purchase_app.Phone', FakePhone), \
             patch('ticketdesk.engine.purchase_app.SubmissionJournal', FakeJournal), \
             patch('ticketdesk.engine.purchase_app.SALE', dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))):
            app.worker(True)
        self.assertEqual(taps, [base[-1][1]])

    def test_selection_never_upgrades_or_submits(self):
        image, rows = sku()
        rows.append(('售罄', (580, 390, 700, 460)))
        self.assertEqual(select_step(rows, image, TARGET, 'date').kind, 'stop')
        image, rows = sku(selected_date=True, sold_out=True)
        self.assertEqual(select_step(rows, image, TARGET, 'tier').kind, 'stop')
        image, rows = sku(selected_date=True, selected_tier=True, quantity='数量:2张')
        self.assertEqual(select_step(rows, image, TARGET, 'quantity').kind, 'stop')
        image, rows = sku(selected_date=True, selected_tier=True, button='提交订单')
        self.assertEqual(select_step(rows, image, TARGET, 'confirm').kind, 'done')
        image, rows = sku(selected_date=True, selected_tier=True, button='立即购买')
        self.assertEqual(select_step(rows, image, TARGET, 'confirm').kind, 'done')

    def test_selection_must_find_exact_tier_and_date(self):
        image, rows = sku(selected_date=True)
        rows[1] = ('看台580元', rows[1][1])
        self.assertEqual(select_step(rows, image, TARGET, 'tier').kind, 'stop')
        rows[0] = ('11月15日19:00', rows[0][1])
        self.assertEqual(select_step(rows, image, TARGET, 'date').kind, 'stop')
        rows[0] = ('2027年11月14日19:00', rows[0][1])
        self.assertEqual(select_step(rows, image, TARGET, 'date').kind, 'stop')
        image, rows = sku(selected_date=True)
        rows.append(('看台380元', (520, 900, 850, 970)))
        self.assertEqual(select_step(rows, image, TARGET, 'tier').kind, 'stop')

    def test_ocr_can_verify_selected_sku(self):
        image = Image.new('RGB', SIZE, 'white')
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 48)
        draw.text((80, 390), '11月14日周六19:00', font=font, fill='#ff6800')
        draw.text((80, 790), '看台480元', font=font, fill='#ff6800')
        draw.text((80, 1500), '数量:1张', font=font, fill='#222222')
        draw.rectangle((160, 2150, 900, 2290), fill='#ff6800')
        draw.text((450, 2185), '确定', font=font, fill='white')
        rows = Reader().read(image)
        self.assertEqual(select_step(rows, image, TARGET, 'confirm').key, 'sku-confirm')

    def test_order_rejects_wrong_facts(self):
        image, rows = order()
        self.assertEqual(decide_order(rows, image, TARGET).kind, 'submit')
        self.assertEqual(decide_order(rows, Image.new('RGB', SIZE, 'white'), TARGET).kind,
                         'stop')
        for original, replacement in (('2026-11-14周六19:00', '2026-11-15周日19:00'),
                                      ('看台480元', '看台580元'),
                                      ('数量:1张', '数量:2张'),
                                      ('合计:¥480.00', '合计:¥481.00'),
                                      ('观演人:测试用户', '联系人:测试用户')):
            with self.subTest(original=original):
                altered = [(replacement if text == original else text, box) for text, box in rows]
                self.assertEqual(decide_order(altered, image, TARGET).kind, 'stop')
        self.assertEqual(decide_order([('待付款', (0, 0, 300, 80))], image, TARGET).kind, 'stop')
        self.assertEqual(decide_order([('待付款', (0, 0, 300, 80))], image, TARGET, submitted=True).kind, 'done')
        self.assertEqual(decide_order([('图片验证码', (0, 0, 300, 80))], image,
                                      TARGET, submitted=True).kind, 'verify')
        confirm_rows = [('确认订单' if text == '提交订单' else text, box)
                        for text, box in rows]
        self.assertEqual(decide_order(confirm_rows, image, TARGET).kind, 'submit')

    def test_actual_order_layout_requires_selected_viewer_and_exact_facts(self):
        image, rows = actual_order_layout()
        original = decide_order(rows, image, TARGET)
        self.assertEqual(original.kind, 'submit')
        more_mask_marks = [
            (text.replace('********', '**********'), box) for text, box in rows]
        self.assertEqual(decide_order(more_mask_marks, image, TARGET).fingerprint,
                         original.fingerprint)
        unselected, _ = actual_order_layout(selected=False)
        self.assertEqual(decide_order(rows, unselected, TARGET).kind, 'stop')
        for original, replacement in (('¥480.00票档×1张', '¥480.00票档×2张'),
                                      ('¥480.00明细', '¥580.00明细'),
                                      ('2026.11.1419:00', '2026.11.1519:00')):
            with self.subTest(original=original):
                altered = [(replacement if text == original else text, box)
                           for text, box in rows]
                self.assertEqual(decide_order(altered, image, TARGET).kind, 'stop')

    def test_alipay_page_is_observed_without_payment_action(self):
        image = Image.new('RGB', SIZE, 'white')
        rows = [('大麦', (480, 470, 600, 550)),
                ('¥480.00', (320, 550, 760, 670)),
                ('付款', (470, 2030, 610, 2130))]
        self.assertEqual(decide_order(rows, image, TARGET, submitted=True).kind, 'done')
        self.assertEqual(decide_order(rows, image, TARGET).kind, 'stop')

    def test_order_crop_requires_unchanged_full_page(self):
        original = Image.new('RGB', SIZE, 'white')
        unchanged = original.copy()
        self.assertTrue(same_content(original, unchanged))
        changed = original.copy()
        ImageDraw.Draw(changed).rectangle((300, 1700, 800, 1950), fill='black')
        self.assertFalse(same_content(original, changed))

    def test_stale_frame_refreshes_only_when_body_is_unchanged(self):
        original = Image.new('RGB', SIZE, 'white')
        phone = Phone()
        phone.observed_at = time.monotonic() - 5
        def unchanged_screen():
            phone.observed_at = time.monotonic()
            return original.copy()
        phone.screen = unchanged_screen
        phone.refresh_if_stale(original)
        self.assertLess(time.monotonic() - phone.observed_at, 1)
        altered = original.copy()
        ImageDraw.Draw(altered).rectangle((300, 800, 800, 1200), fill='black')
        phone.observed_at = time.monotonic() - 5
        phone.screen = lambda: altered
        with self.assertRaises(StaleFrameError):
            phone.refresh_if_stale(original)

    def test_prepositioned_sku_and_order_are_not_reopened(self):
        sku_image, sku_rows = sku(selected_date=True, selected_tier=True)
        sku_rows += [TITLE, ('场次', (50, 280, 150, 340)),
                     ('票档', (50, 690, 150, 750))]
        for expected, frame in (('date', (sku_image, sku_rows)),
                                ('order', order())):
            with self.subTest(expected=expected):
                commands = []
                class FakePhone:
                    def connect(self): pass
                    def foreground(self): return True
                    def cmd(self, *args, **kwargs): commands.append(args)
                app = PurchaseApp.__new__(PurchaseApp)
                app.events = queue.Queue()
                app.stopped = threading.Event()
                app.reader = object()
                app.emit = lambda kind, value: app.events.put((kind, value))
                app.observe = lambda phone, reader: (*frame, .01)
                with patch('ticketdesk.engine.purchase_app.Phone', FakePhone):
                    app.worker(False)
                self.assertEqual(prepared_phase(frame[1], frame[0], TARGET), expected)
                self.assertFalse(commands)
                statuses = [value for kind, value in list(app.events.queue)
                            if kind == 'status']
                self.assertIn(expected, statuses[-1])

    def test_complete_purchase_submits_once(self):
        detail = (Image.new('RGB', SIZE, 'white'), [TITLE, ENTRY])
        date_unselected = sku()
        date_selected = sku(selected_date=True)
        tier_selected = sku(selected_date=True, selected_tier=True)
        review = order()
        payment = (Image.new('RGB', SIZE, 'white'), [('待付款', (50, 150, 500, 220))])
        frames = iter([detail, detail, detail, date_unselected, date_unselected,
                       date_selected, date_selected, tier_selected, tier_selected,
                       review, review, payment])
        taps = []
        claims = []
        completed = []

        class FakePhone:
            def connect(self): pass
            def cmd(self, *args, **kwargs): return b''
            def foreground(self): return True
            def tap(self, box, stopped, expected_image=None):
                taps.append(box)
                return True
            def screen(self): return Image.new('RGB', SIZE, 'white')
            def refresh_if_stale(self, expected_image): pass

        class FakeJournal:
            def __init__(self, path): pass
            def attempted(self): return False
            def claim(self, fingerprint): claims.append(fingerprint)
            def complete(self): completed.append(True)

        app = PurchaseApp.__new__(PurchaseApp)
        app.events = queue.Queue()
        app.stopped = threading.Event()
        app.reader = object()
        app.emit = lambda kind, value: app.events.put((kind, value))
        app.observe = lambda phone, reader, anchor=None, **kwargs: (*next(frames), .01)
        with patch('ticketdesk.engine.purchase_app.Phone', FakePhone), patch('ticketdesk.engine.purchase_app.SubmissionJournal', FakeJournal), patch('ticketdesk.engine.purchase_app.SALE', dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))):
            app.worker(True)
        messages = [value for kind, value in list(app.events.queue) if kind == 'status']
        self.assertEqual(len(taps), 5)
        self.assertEqual(len(claims), 1)
        self.assertEqual(len(completed), 1)
        self.assertIn('待付款', messages[-1])

    def test_reservation_can_advance_directly_to_verified_order(self):
        detail = (Image.new('RGB', SIZE, 'white'), [TITLE, ENTRY])
        review = order()
        payment = (Image.new('RGB', SIZE, 'white'), [('待付款', (50, 150, 500, 220))])
        frames = iter([detail, detail, detail, review, review, review, payment])
        taps = []
        claims = []

        class FakePhone:
            def connect(self): pass
            def cmd(self, *args, **kwargs): return b''
            def foreground(self): return True
            def tap(self, box, stopped, expected_image=None):
                taps.append(box)
                return True
            def refresh_if_stale(self, expected_image): pass

        class FakeJournal:
            def __init__(self, path): pass
            def attempted(self): return False
            def claim(self, fingerprint): claims.append(fingerprint)
            def complete(self): pass

        app = PurchaseApp.__new__(PurchaseApp)
        app.events = queue.Queue()
        app.stopped = threading.Event()
        app.reader = object()
        app.emit = lambda kind, value: app.events.put((kind, value))
        app.observe = lambda phone, reader, anchor=None, **kwargs: (*next(frames), .01)
        with patch('ticketdesk.engine.purchase_app.Phone', FakePhone), patch('ticketdesk.engine.purchase_app.SubmissionJournal', FakeJournal), patch('ticketdesk.engine.purchase_app.SALE', dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))):
            app.worker(True)
        self.assertEqual(len(taps), 2)
        self.assertEqual(len(claims), 1)
        messages = [value for kind, value in list(app.events.queue) if kind == 'status']
        self.assertIn('待付款', messages[-1])

    def test_incomplete_order_crop_falls_back_to_full_ocr(self):
        image, rows = order()
        payment = (Image.new('RGB', SIZE, 'white'), [('待付款', (50, 150, 500, 220))])
        frames = iter([(image, rows), (image, rows),
                       (image, [('立即提交', (200, 2170, 900, 2300))]), payment])
        taps, claims = [], []

        class FakePhone:
            def connect(self): pass
            def foreground(self): return True
            def tap(self, box, stopped, expected_image=None):
                taps.append(box)
                return True
            def refresh_if_stale(self, expected_image): pass

        class FakeReader:
            def read(self, image): return rows

        class FakeJournal:
            def __init__(self, path): pass
            def attempted(self): return False
            def claim(self, fingerprint): claims.append(fingerprint)
            def complete(self): pass

        app = PurchaseApp.__new__(PurchaseApp)
        app.events = queue.Queue()
        app.stopped = threading.Event()
        app.reader = FakeReader()
        app.emit = lambda kind, value: app.events.put((kind, value))
        app.observe = lambda phone, reader, anchor=None, **kwargs: (*next(frames), .01)
        with patch('ticketdesk.engine.purchase_app.Phone', FakePhone), \
             patch('ticketdesk.engine.purchase_app.SubmissionJournal', FakeJournal), \
             patch('ticketdesk.engine.purchase_app.SALE', dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))):
            app.worker(True)
        self.assertEqual(len(taps), 1)
        self.assertEqual(len(claims), 1)
        logs = [value for kind, value in list(app.events.queue) if kind == 'log']
        self.assertTrue(any('整屏复核' in value for value in logs))


if __name__ == '__main__':
    unittest.main()

