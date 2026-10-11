"""The phone-side flow stays locked, private, and parseable until field validation."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from ticketdesk.autox_live_export import (build_flow_probe_script, build_live_script, build_locked_prototype,
                                          build_selection_rehearsal_script)
from ticketdesk.autox_phone import open_live_on_phone, open_rehearsal_on_phone
from ticketdesk.profiles import validate_profile


RAW_PROFILE = {
    'id': 'offline-demo',
    'platform': 'maoyan',
    'title': '测试演唱会',
    'keyword': '测试演唱会 广州',
    'city': '广州',
    'date': '2026-12-05 周六 19:00',
    'event_time': '2026-12-05T19:00:00+08:00',
    'sale_time': '2026-10-08T13:00:00+08:00',
    'price': '看台 1380 元',
    'users': ['测试观演人'],
    'serial': 'private-device-serial',
}


class AutoXLiveExportTests(unittest.TestCase):
    def simulate(self, scenario, *, rehearsal=False, probe=False):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js is needed for offline AutoX mock')
        builder = build_selection_rehearsal_script if rehearsal else build_live_script
        if probe:
            builder = build_flow_probe_script
        script = builder(validate_profile(RAW_PROFILE))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'synthetic-only.js'
            path.write_text(script, encoding='utf-8')
            harness = Path(__file__).with_name('autox_live_mock.js')
            run = subprocess.run([node, str(harness), str(path), scenario],
                                 capture_output=True, text=True,
                                 encoding='utf-8', errors='replace')
            self.assertEqual(run.returncode, 0, run.stderr)
            return json.loads(run.stdout)

    def test_live_selectors_can_probe_all_flow_pages_without_any_action_or_lock(self):
        for scenario in ('probe_detail', 'probe_sku', 'probe_order'):
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario, probe=True)
                self.assertEqual(outcome['clicks'], [])
                self.assertEqual(outcome['stored'], {})
                self.assertEqual(outcome['lockFiles'], {})
                self.assertTrue(any('只读检查' in log and '未点击' in log for log in outcome['logs']))

    def test_readonly_decision_uses_one_fresh_tree_and_no_repeated_selectors(self):
        outcome = self.simulate('probe_detail', probe=True)
        self.assertEqual(outcome['treeReads'], 1)
        self.assertEqual(outcome['selectorReads'], 0)
        self.assertEqual(outcome['clicks'], [])
        self.assertEqual(len(outcome['diagnosticWrites']), 1)
        record = outcome['diagnosticWrites'][0]
        self.assertTrue(record['data']['readOnly'])
        self.assertEqual(record['data']['screenReads'], 1)
        self.assertNotIn(RAW_PROFILE['users'][0], json.dumps(record, ensure_ascii=False))

    def test_native_tree_failure_emits_no_raw_exception_and_never_clicks(self):
        outcome = self.simulate('broken_native_tree', probe=True)
        self.assertEqual(outcome['clicks'], [])
        self.assertNotIn('Private screen text', str(outcome['logs']))
        self.assertTrue(any('权限异常' in log for log in outcome['logs']))

    def test_window_change_or_challenge_after_entry_prevents_further_clicks(self):
        for scenario in ('package_changed_after_entry', 'challenge_after_entry'):
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario)
                self.assertEqual(outcome['clicks'], ['立即购票'])
                self.assertTrue(any('已不在前台' in log or '出现验证' in log for log in outcome['logs']))

    def test_generated_prototype_stays_locked_and_minimal(self):
        script = build_locked_prototype(validate_profile(RAW_PROFILE))
        self.assertIn('var ENABLE_EXPERIMENTAL_LIVE = false;', script)
        self.assertIn('var TARGET = {', script)
        self.assertNotIn('private-device-serial', script)
        self.assertNotIn('users', script)
        self.assertNotIn('serial', script)
        self.assertNotIn(json.dumps('测试观演人', ensure_ascii=True)[1:-1], script)
        self.assertNotIn('var TARGET = null;', script)

    def test_live_export_has_one_enabled_attempt_and_no_device_serial(self):
        script = build_live_script(validate_profile(RAW_PROFILE))
        self.assertIn('var ENABLE_EXPERIMENTAL_LIVE = true;', script)
        self.assertIn('var PRECHECK_ONLY = false;', script)
        self.assertNotIn('private-device-serial', script)

    def test_rehearsal_export_is_separate_and_enabled(self):
        script = build_selection_rehearsal_script(validate_profile(RAW_PROFILE))
        self.assertIn('var SELECTION_REHEARSAL = true;', script)
        self.assertIn('var ENABLE_EXPERIMENTAL_LIVE = true;', script)
        self.assertNotIn('private-device-serial', script)

    def test_phone_launcher_pushes_selected_script_and_opens_autox(self):
        profile = validate_profile({**RAW_PROFILE, 'serial': ''})
        calls = []
        def fake_run(args):
            calls.append(args)
            return 'List of devices attached\nphone-1\tdevice\n' if args[1:] == ['devices'] else ''
        with tempfile.TemporaryDirectory() as tmp:
            adb = Path(tmp) / 'adb.exe'
            adb.touch()
            output = Path(tmp) / 'live.js'
            open_live_on_phone(profile, adb=adb, output=output, run=fake_run)
            self.assertTrue(output.exists())
            self.assertIn('var ENABLE_EXPERIMENTAL_LIVE = true;',
                          output.read_text(encoding='utf-8'))
        self.assertEqual(calls[1][1:4], ['-s', 'phone-1', 'push'])
        self.assertEqual(calls[2][1:6], ['-s', 'phone-1', 'shell', 'am', 'start'])

    def test_phone_launcher_rejects_multiple_devices_before_push(self):
        profile = validate_profile({**RAW_PROFILE, 'serial': ''})
        calls = []
        def fake_run(args):
            calls.append(args)
            return 'List of devices attached\na\tdevice\nb\tdevice\n'
        with tempfile.TemporaryDirectory() as tmp:
            adb = Path(tmp) / 'adb.exe'
            adb.touch()
            with self.assertRaises(ValueError):
                open_live_on_phone(profile, adb=adb, output=Path(tmp) / 'live.js',
                                   run=fake_run)
        self.assertEqual(len(calls), 1)

    def test_phone_rehearsal_uses_separate_file(self):
        profile = validate_profile({**RAW_PROFILE, 'serial': ''})
        calls = []
        def fake_run(args):
            calls.append(args)
            return 'List of devices attached\nphone-1\tdevice\n' if args[1:] == ['devices'] else ''
        with tempfile.TemporaryDirectory() as tmp:
            adb = Path(tmp) / 'adb.exe'
            adb.touch()
            output = Path(tmp) / 'rehearsal.js'
            open_rehearsal_on_phone(profile, adb=adb, output=output, run=fake_run)
            self.assertIn('var SELECTION_REHEARSAL = true;', output.read_text(encoding='utf-8'))
        self.assertEqual(calls[1][-1], '/sdcard/Download/autox-maoyan-rehearsal.js')

    def test_invalid_profile_cannot_build(self):
        profile = validate_profile(RAW_PROFILE)
        with self.assertRaises(ValueError):
            build_locked_prototype({**profile, 'platform': 'damai'})
        with self.assertRaises(ValueError):
            build_locked_prototype({**profile, 'users': ['甲', '乙']})
        with self.assertRaises(ValueError):
            build_locked_prototype({**profile, 'id': ''})
        with self.assertRaises(RuntimeError):
            build_locked_prototype(profile, template='var TARGET = null; var ENABLE_EXPERIMENTAL_LIVE = true;')

    @unittest.skipUnless(shutil.which('node'), 'Node.js is needed for syntax check')
    def test_rhino_compatible_javascript_parses_and_exits_locked(self):
        script = build_locked_prototype(validate_profile(RAW_PROFILE))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'locked-maoyan.js'
            path.write_text(script, encoding='utf-8')
            node = shutil.which('node')
            syntax = subprocess.run([node, '--check', str(path)], capture_output=True,
                                    text=True, encoding='utf-8', errors='replace')
            self.assertEqual(syntax.returncode, 0, syntax.stderr)
            run = subprocess.run([node, str(path)], capture_output=True,
                                 text=True, encoding='utf-8', errors='replace')
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('已锁定', run.stdout)

    def test_direct_order_stops_for_manual_review_without_submit(self):
        outcome = self.simulate('direct_order')
        self.assertEqual(outcome['diagnosticWrites'], [])
        self.assertEqual(outcome['clicks'], ['立即购票'])
        self.assertEqual(outcome['state'], 'order')
        self.assertEqual(len(outcome['stored']), 1)
        self.assertEqual(len(outcome['lockFiles']), 1)
        self.assertNotIn('submit_clicked', str(outcome['stored']))

    def test_order_with_payment_method_label_still_stops(self):
        outcome = self.simulate('decimal_price_payment_label')
        self.assertEqual(outcome['clicks'], ['立即购票'])
        self.assertEqual(outcome['state'], 'order')

    def test_real_order_with_immediate_payment_is_recognized_without_paying(self):
        for scenario in ('instant_pay_order', 'combined_order_summary'):
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario)
                self.assertEqual(outcome['clicks'], ['立即购票'])
                self.assertEqual(outcome['state'], 'order')
                self.assertTrue(any('唯一观演人已勾选' in log for log in outcome['logs']))

    def test_unique_unchecked_attendee_is_selected_once_without_paying(self):
        outcome = self.simulate('attendee_unchecked')
        self.assertEqual(outcome['clicks'], ['立即购票', '[观演人勾选框]'])
        self.assertEqual(outcome['state'], 'order')
        self.assertNotIn('立即支付', outcome['clicks'])

    def test_attendee_selection_waits_for_delayed_checked_state_without_reclick(self):
        outcome = self.simulate('attendee_delayed')
        self.assertEqual(outcome['clicks'], ['立即购票', '[观演人勾选框]'])
        self.assertTrue(any('已自动勾选' in log for log in outcome['logs']))

    def test_failed_checkbox_selection_is_not_retried_or_paid(self):
        outcome = self.simulate('attendee_no_change')
        self.assertEqual(outcome['clicks'], ['立即购票', '[观演人勾选框]'])
        self.assertTrue(any('未确认观演人已勾选' in log for log in outcome['logs']))

    def test_custom_accessible_checkable_control_is_recognized(self):
        outcome = self.simulate('custom_checkable')
        self.assertEqual(outcome['clicks'], ['立即购票', '[观演人勾选框]'])
        self.assertTrue(any('已自动勾选' in log for log in outcome['logs']))

    def test_wrong_person_or_disabled_checkbox_never_clicked(self):
        for scenario in ('wrong_attendee', 'attendee_disabled'):
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario)
                self.assertEqual(outcome['clicks'], ['立即购票'])
                self.assertEqual(outcome['state'], 'order')

    def test_ambiguous_or_missing_attendee_never_selects_or_pays(self):
        for scenario in ('attendee_ambiguous', 'attendee_missing'):
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario)
                self.assertEqual(outcome['clicks'], ['立即购票'])
                self.assertEqual(outcome['state'], 'order')
                self.assertTrue(any('未唯一识别' in log for log in outcome['logs']))

    def test_wrong_order_date_or_price_never_selects_attendee(self):
        for scenario in ('wrong_order_date', 'wrong_order_price',
                         'substring_order_price', 'eleven_ticket_order'):
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario)
                self.assertEqual(outcome['clicks'], ['立即购票'])
                self.assertEqual(outcome['state'], 'order')

    def test_stale_current_package_does_not_hide_actual_maoyan_window(self):
        outcome = self.simulate('stale_current_package')
        self.assertEqual(outcome['clicks'], ['立即购票'])
        self.assertEqual(outcome['state'], 'order')

    def test_reservation_label_accepts_variable_spaces_and_line_breaks(self):
        outcome = self.simulate('spaced_reservation')
        self.assertEqual(outcome['clicks'], ['立即购票'])
        self.assertEqual(outcome['state'], 'order')

    def test_live_selection_confirms_once_then_stops_on_order_review(self):
        outcome = self.simulate('sku')
        self.assertEqual(outcome['clicks'],
                         ['立即购票', '2026-12-05 周六 19:00', '看台 ¥1380', '确认'])
        self.assertEqual(outcome['state'], 'order')
        self.assertNotIn('提交订单', outcome['clicks'])

    def test_preselected_target_date_goes_straight_to_price(self):
        outcome = self.simulate('sku_preselected')
        self.assertEqual(outcome['clicks'],
                         ['立即购票', '看台 ¥1380', '确认'])
        self.assertEqual(outcome['state'], 'order')

    def test_rehearsal_taps_price_but_never_confirms_or_claims_order(self):
        outcome = self.simulate('sku_preselected', rehearsal=True)
        self.assertEqual(outcome['clicks'], ['立即购票', '看台 ¥1380'])
        self.assertEqual(outcome['state'], 'sku_selected')
        self.assertEqual(outcome['stored'], {})
        self.assertEqual(outcome['lockFiles'], {})
        self.assertTrue(any('选档演练已点中目标票档' in log for log in outcome['logs']))

    def test_rehearsal_direct_order_never_submits(self):
        outcome = self.simulate('direct_order', rehearsal=True)
        self.assertEqual(outcome['clicks'], ['立即购票'])
        self.assertEqual(outcome['state'], 'order')
        self.assertEqual(outcome['stored'], {})
        self.assertEqual(outcome['lockFiles'], {})

    def test_realistic_selection_page_stops_on_sold_out_target(self):
        outcome = self.simulate('sold_out_sku')
        self.assertEqual(outcome['clicks'], ['立即购票'])
        self.assertEqual(outcome['state'], 'sku')
        self.assertNotIn('submit_clicked', str(outcome['stored']))

    def test_separate_stock_badge_or_disabled_price_never_clicked(self):
        for scenario in ('sold_out_sibling', 'disabled_price'):
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario)
                self.assertEqual(outcome['clicks'], ['立即购票'])
                self.assertEqual(outcome['state'], 'sku')

    def test_explicitly_selected_target_tier_continues_without_toggling(self):
        outcome = self.simulate('sku_already_target')
        self.assertEqual(outcome['clicks'], ['立即购票', '确认'])
        self.assertEqual(outcome['state'], 'order')

    def test_price_tap_does_not_continue_while_choice_button_is_grey(self):
        outcome = self.simulate('price_click_no_change')
        self.assertEqual(outcome['clicks'], ['立即购票', '看台 ¥1380'])
        self.assertEqual(outcome['state'], 'sku')
        self.assertNotIn('submit_clicked', str(outcome['stored']))

    def test_confirm_tap_is_not_repeated_when_page_does_not_change(self):
        outcome = self.simulate('confirm_click_no_change')
        self.assertEqual(outcome['clicks'], ['立即购票', '看台 ¥1380', '确认'])
        self.assertEqual(outcome['state'], 'sku_selected')
        self.assertTrue(any('未确认进入订单页' in log for log in outcome['logs']))

    def test_wrong_quantity_after_price_never_confirms(self):
        outcome = self.simulate('wrong_quantity_after_price')
        self.assertEqual(outcome['clicks'], ['立即购票', '看台 ¥1380'])
        self.assertEqual(outcome['state'], 'sku_selected')

    def test_ambiguous_confirmation_never_confirms(self):
        outcome = self.simulate('ambiguous_confirm')
        self.assertEqual(outcome['clicks'], ['立即购票', '看台 ¥1380'])
        self.assertEqual(outcome['state'], 'sku_selected')

    def test_disabled_confirmation_never_confirms(self):
        outcome = self.simulate('disabled_confirm')
        self.assertEqual(outcome['clicks'], ['立即购票', '看台 ¥1380'])
        self.assertEqual(outcome['state'], 'sku_selected')

    def test_price_that_opens_order_directly_does_not_click_again(self):
        outcome = self.simulate('price_auto_order')
        self.assertEqual(outcome['clicks'], ['立即购票', '看台 ¥1380'])
        self.assertEqual(outcome['state'], 'order')

    def test_already_selected_price_is_not_toggled(self):
        outcome = self.simulate('sku_already_chosen')
        self.assertEqual(outcome['clicks'], ['立即购票'])
        self.assertEqual(outcome['state'], 'sku_selected')

    def test_duplicate_price_candidates_never_click_either(self):
        outcome = self.simulate('duplicate_price_sku')
        self.assertEqual(outcome['clicks'], ['立即购票'])
        self.assertEqual(outcome['state'], 'sku')

    def test_mock_invalid_page_ambiguous_entry_and_prior_claim_never_click(self):
        for scenario in ('wrong_package', 'ambiguous_entry', 'already_claimed'):
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario)
                self.assertEqual(outcome['clicks'], [])
                self.assertEqual(outcome['stored'], {})

    def test_mock_unknown_transition_never_submits(self):
        outcome = self.simulate('unknown_after_entry')
        self.assertEqual(outcome['clicks'], ['立即购票'])
        self.assertEqual(len(outcome['stored']), 1)

    def test_two_ticket_order_never_submits(self):
        outcome = self.simulate('two_ticket_order')
        self.assertEqual(outcome['clicks'], ['立即购票'])
        self.assertNotEqual(outcome['state'], 'payment')


if __name__ == '__main__':
    unittest.main()
