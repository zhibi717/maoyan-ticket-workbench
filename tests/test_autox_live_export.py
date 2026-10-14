"""The phone-side flow stays locked, private, and parseable until field validation."""
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from ticketdesk.autox_live_export import (build_flow_probe_script, build_live_script, build_locked_prototype,
                                          build_selection_rehearsal_script, build_selection_confirmation_rehearsal_script, build_payment_script, build_congestion_probe_script, build_wechat_script,
                                          build_selection_capture_script, build_fixed_coordinate_payment_script, build_merchant_payment_script)
from ticketdesk.autox_phone import (open_live_on_phone, open_rehearsal_on_phone,
                                    open_first_click_benchmark_on_phone)
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
    def test_fixed_coordinate_build_is_separate_and_guards_final_order(self):
        profile = validate_profile(RAW_PROFILE)
        points = {'width': 1080, 'height': 2376, 'gapMs': 5,
                  'entry': [600, 2240], 'tier': [290, 1380], 'confirm': [860, 2240]}
        script = build_fixed_coordinate_payment_script(profile, points)
        self.assertIn('var FAST_FIXED_PATH = true;', script)
        self.assertIn('var OPEN_WECHAT_PAYMENT = true;', script)
        self.assertIn('var FAST_COORDS = {"width":1080,"height":2376,"gapMs":5', script)
        self.assertIn('if (pageKind() === "order")', script)
        self.assertIn('finishAtOrder();', script)
        self.assertIn('orderIdentityMatches()', script)
        self.assertIn('stop("固定点击后未确认进入目标订单页', script)
        with self.assertRaises(ValueError):
            build_fixed_coordinate_payment_script(profile, {**points, 'tier': [1080, 1380]})
        with self.assertRaises(ValueError):
            build_fixed_coordinate_payment_script(profile, {**points, 'width': 720})
        node = shutil.which('node')
        if node:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'fixed.js'
                path.write_text(script, encoding='utf-8')
                checked = subprocess.run([node, '--check', str(path)], capture_output=True, text=True)
                self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_fixed_entry_still_touches_when_reservation_changes_at_sale(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js is needed for offline AutoX mock')
        profile = validate_profile(RAW_PROFILE)
        script = build_fixed_coordinate_payment_script(profile, {
            'width': 1080, 'height': 2376, 'gapMs': 5,
            'entry': [600, 2150], 'tier': [300, 560], 'confirm': [860, 2150],
        }).replace('var USER_PRESELECTED = false;', 'var USER_PRESELECTED = true;', 1)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'fixed-sale-transition.js'
            path.write_text(script, encoding='utf-8')
            harness = Path(__file__).with_name('autox_live_mock.js')
            for scenario in ('fixed_reservation_switches', 'fixed_reservation_already_switched'):
                with self.subTest(scenario=scenario):
                    run = subprocess.run([node, str(harness), str(path), scenario],
                                         capture_output=True, text=True, encoding='utf-8', errors='replace')
                    self.assertEqual(run.returncode, 0, run.stderr)
                    outcome = json.loads(run.stdout)
                    self.assertGreaterEqual(len(outcome['shortPresses']), 1)
                    self.assertEqual(outcome['clicks'][0], '立即购票')
                    self.assertGreaterEqual(outcome['clickTimes'][0], 0)
                    self.assertGreaterEqual(outcome['diagnosticWrites'][-1]['data']['entryTouchCount'], 1)

    def simulate(self, scenario, *, rehearsal=False, confirm_rehearsal=False, merchant_payment=False, probe=False, payment=False, congestion_probe=False, wechat=False,
                 capture=False, amount=1380, source='workbench', initial_storage=None, template=None):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js is needed for offline AutoX mock')
        builder = build_selection_rehearsal_script if rehearsal else build_live_script
        if confirm_rehearsal:
            builder = build_selection_confirmation_rehearsal_script
        if merchant_payment:
            builder = build_merchant_payment_script
        if probe:
            builder = build_flow_probe_script
        if payment:
            builder = build_payment_script
        if congestion_probe:
            builder = build_congestion_probe_script
        if wechat:
            builder = build_wechat_script
        if capture:
            builder = build_selection_capture_script
        raw = {**RAW_PROFILE, 'price': f'看台 {amount} 元', 'selection_source': source}
        script = builder(validate_profile(raw), template=template)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'synthetic-only.js'
            path.write_text(script, encoding='utf-8')
            harness = Path(__file__).with_name('autox_live_mock.js')
            args = [node, str(harness), str(path), scenario]
            if initial_storage is not None:
                storage = Path(tmp) / 'private-capture.json'
                storage.write_text(json.dumps(initial_storage), encoding='utf-8')
                args.append(str(storage))
            run = subprocess.run(args,
                                 capture_output=True, text=True,
                                 encoding='utf-8', errors='replace')
            self.assertEqual(run.returncode, 0, run.stderr)
            return json.loads(run.stdout)

    def test_entry_short_press_stops_before_wechat_and_never_retries_failure(self):
        outcome = self.simulate('wechat_direct', wechat=True)
        self.assertEqual(outcome['shortPresses'][0], {'duration': 1, 'state': 'detail'})
        self.assertTrue(all(item['duration'] in (1, 20) and item['state'] != 'wechat'
                            for item in outcome['shortPresses']))
        self.assertEqual(outcome['state'], 'wechat')
        failed = self.simulate('entry_press_failed', wechat=True)
        self.assertEqual(len(failed['shortPresses']), 1)
        self.assertEqual(failed['clicks'], [])
        self.assertEqual(failed['state'], 'detail')

    def test_entry_retries_only_when_same_detail_remains(self):
        ignored = self.simulate('entry_first_touch_ignored')
        self.assertEqual(ignored['clicks'], ['立即购票', '立即购票'])
        self.assertEqual(len(ignored['shortPresses']), 2)
        advanced = self.simulate('sku_already_target')
        self.assertEqual(advanced['clicks'].count('立即购票'), 1)
        self.assertEqual(sum(touch['state'] == 'detail' for touch in advanced['shortPresses']), 1)

    def test_guarded_preflight_accepts_presale_entry_but_never_touches_early(self):
        switched = self.simulate('presale_reservation_switched', payment=True)
        self.assertEqual(switched['clicks'], ['立即购票', '提交订单'])
        self.assertGreaterEqual(switched['clickTimes'][0], 0)
        missing = self.simulate('presale_reservation_missing_no_entry', payment=True)
        self.assertEqual(missing['clicks'], [])
        self.assertEqual(missing['lockFiles'], {})

    def test_short_touches_continue_all_four_merchant_pages_without_touching_wechat(self):
        outcome = self.simulate('wechat_payment_sheet_full_flow', wechat=True, amount=480)
        self.assertEqual(outcome['clicks'], ['立即购票', '看台 ¥480', '确认', '立即支付', '确认支付'])
        self.assertEqual(outcome['state'], 'wechat')
        self.assertEqual(len(outcome['shortPresses']), len(outcome['clicks']))
        self.assertTrue(all(item['duration'] in (1, 20) and item['state'] != 'wechat'
                            for item in outcome['shortPresses']))
        record = outcome['diagnosticWrites'][-1]['data']
        self.assertEqual(record['state'], 'wechat_payment_page')
        self.assertEqual(len(record['touchTimings']), len(outcome['clicks']))
        self.assertTrue(all(item['pressDurationMs'] == (1 if item['stage'] == '购票入口' else 20)
                            for item in record['touchTimings']))
        self.assertFalse(record['automaticPayment'])
        unselected_date = self.simulate('sku', payment=True, amount=480)
        self.assertEqual(unselected_date['clicks'], ['立即购票', '2026-12-05 周六 19:00',
                                                   '看台 ¥480', '确认', '提交订单'])
        self.assertTrue(all(item['duration'] in (1, 20) for item in unselected_date['shortPresses']))

    def test_failed_short_confirmation_does_not_fall_back_or_submit(self):
        outcome = self.simulate('wechat_payment_sheet_full_flow_failed_confirm', wechat=True)
        self.assertEqual(outcome['state'], 'sku_selected')
        self.assertNotIn('确认', outcome['clicks'])
        self.assertNotIn('立即支付', outcome['clicks'])
        self.assertEqual(sum(item['state'] == 'sku_selected' for item in outcome['shortPresses']), 1)

    def test_failed_short_submission_or_gateway_never_retries(self):
        for scenario, state, attempted, forbidden in [
                ('wechat_payment_sheet_full_flow_failed_submit', 'order', 'submission_attempted', '立即支付'),
                ('wechat_payment_sheet_full_flow_failed_gateway', 'payment_sheet', 'wechat_launch_attempted', '确认支付')]:
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario, wechat=True)
                self.assertEqual(outcome['state'], state)
                self.assertNotIn(forbidden, outcome['clicks'])
                self.assertEqual(sum(item['state'] == state for item in outcome['shortPresses']), 1)
                self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], attempted)
                self.assertFalse(outcome['diagnosticWrites'][-1]['data']['automaticPayment'])

    def test_click_is_used_only_when_short_touch_api_is_unavailable(self):
        outcome = self.simulate('wechat_payment_sheet_full_flow_no_press', wechat=True)
        self.assertEqual(outcome['state'], 'wechat')
        self.assertEqual(outcome['shortPresses'], [])
        record = outcome['diagnosticWrites'][-1]['data']
        self.assertEqual(len(record['touchTimings']), len(outcome['clicks']))
        self.assertTrue(all(item['pressDurationMs'] is None for item in record['touchTimings']))

    def test_unknown_actual_order_price_is_not_adopted_as_user_choice(self):
        outcome = self.simulate('wechat_preselected_price', wechat=True)
        self.assertEqual(outcome['clicks'], ['立即购票'])
        self.assertNotIn('提交订单', outcome['clicks'])

    def test_cashier_must_match_actual_submitted_price(self):
        outcome = self.simulate('wechat_preselected_price_changed', wechat=True)
        self.assertEqual(outcome['clicks'], ['立即购票'])

    def test_wechat_direct_and_cashier_routes_stop_without_final_payment(self):
        for scenario, clicks in [
                ('wechat_direct', ['立即购票', '提交订单']),
                ('wechat_immediate_review', ['立即购票', '立即支付']),
                ('wechat_cashier_selected', ['立即购票', '提交订单', '立即支付']),
                ('wechat_cashier_explicit', ['立即购票', '提交订单', '前往微信支付'])]:
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario, wechat=True)
                self.assertEqual(outcome['clicks'], clicks)
                self.assertEqual(outcome['state'], 'wechat')
                self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], 'wechat_payment_page')
                self.assertFalse(outcome['diagnosticWrites'][-1]['data']['automaticPayment'])
                self.assertNotIn('确认支付', outcome['clicks'])
                self.assertNotIn('确认付款', outcome['clicks'])

    def test_wechat_cashier_guards_and_uncertain_launch_never_retry_or_pay(self):
        for scenario in ['wechat_cashier_disabled', 'wechat_cashier_wrong_amount', 'wechat_cashier_unselected',
                         'wechat_cashier_method_changed', 'wechat_cashier_autopay', 'wechat_cashier_final_only']:
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario, wechat=True)
                self.assertEqual(outcome['clicks'], ['立即购票', '提交订单'])
                self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], 'cashier_manual_required')
        outcome = self.simulate('wechat_cashier_timeout', wechat=True)
        self.assertEqual(outcome['clicks'], ['立即购票', '提交订单', '立即支付'])
        self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], 'wechat_launch_unconfirmed')

    def test_export_includes_only_digests_of_supported_partial_name_masks(self):
        script = build_wechat_script(validate_profile(RAW_PROFILE))
        target = json.loads(next(line for line in script.splitlines() if line.startswith('var TARGET = '))[13:-1])
        masks = target['attendeeMaskedNameSha256']
        for value in ('测***人', '测*人', '测＊＊＊人', '*试观演人', '测试观演*'):
            with self.subTest(value=value):
                self.assertIn(hashlib.sha256(value.encode('utf-8')).hexdigest(), masks)
        for value in ('测试观演人', '*****', '*', '＊＊＊'):
            with self.subTest(value=value):
                self.assertNotIn(hashlib.sha256(value.encode('utf-8')).hexdigest(), masks)
        self.assertEqual(len(masks), len(set(masks)))
        self.assertTrue(all(len(value) == 64 and set(value) <= set('0123456789abcdef') for value in masks))
        self.assertNotIn(json.dumps('测***人', ensure_ascii=True)[1:-1], script)
        self.assertNotIn('测试观演人', str(self.simulate('wechat_masked_selected_sheet', wechat=True, amount=480)['diagnosticWrites']))

    def test_checked_masked_attendee_is_preserved_and_submitted_once(self):
        for scenario, clicks in [('wechat_masked_selected_sheet', ['立即购票', '立即支付', '确认支付']),
                                 ('wechat_masked_fullwidth', ['立即购票', '立即支付'])]:
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario, wechat=True, amount=480)
                self.assertEqual(outcome['clicks'], clicks)
                self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], 'wechat_payment_page')
                self.assertFalse(outcome['diagnosticWrites'][-1]['data']['automaticPayment'])
                self.assertNotIn('[观演人勾选框]', outcome['clicks'])
                self.assertLessEqual(outcome['clicks'].count('确认支付'), 1)

    def test_masked_attendee_requires_user_preselected_mode_and_checked_unique_identity(self):
        for scenario in ['wechat_masked_unchecked', 'wechat_masked_wrong', 'wechat_masked_ambiguous',
                         'wechat_masked_ambiguous_checkbox', 'wechat_masked_all_stars',
                         'wechat_masked_changed_before_submit', 'wechat_masked_check_changed_before_submit']:
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario, wechat=True, amount=480)
                self.assertEqual(outcome['clicks'], ['立即购票'])
                self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], 'not_submitted')
                self.assertNotIn('[观演人勾选框]', outcome['clicks'])
        outcome = self.simulate('wechat_masked_selected_sheet', payment=True, amount=480)
        self.assertEqual(outcome['clicks'], ['立即购票'])
        self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], 'not_submitted')

    def test_real_payment_order_sheet_uses_its_amount_and_launches_wechat_once(self):
        for scenario in ['wechat_payment_sheet', 'wechat_payment_sheet_right_amount_background_wrong',
                         'wechat_payment_sheet_split_amount', 'wechat_payment_sheet_sibling_selected']:
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario, wechat=True, amount=480)
                self.assertEqual(outcome['clicks'], ['立即购票', '立即支付', '确认支付'])
                self.assertEqual(outcome['state'], 'wechat')
                self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], 'wechat_payment_page')
                self.assertFalse(outcome['diagnosticWrites'][-1]['data']['automaticPayment'])
                self.assertEqual(next(iter(outcome['stored'].values()))['state'], 'wechat_launch_attempted')
                self.assertEqual(outcome['clicks'].count('确认支付'), 1)
                self.assertNotIn('微信支付', outcome['clicks'])

    def test_payment_only_flow_stops_at_payment_order_sheet_without_launching_wechat(self):
        outcome = self.simulate('payment_order_sheet', payment=True, amount=480)
        self.assertEqual(outcome['clicks'], ['立即购票', '提交订单'])
        self.assertEqual(outcome['state'], 'payment_sheet')
        self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], 'pending_payment_sheet')
        self.assertEqual(next(iter(outcome['stored'].values()))['state'], 'submission_attempted')
        self.assertNotIn('确认支付', outcome['clicks'])

    def test_payment_order_sheet_does_not_accept_background_amount_or_unknown_container(self):
        for scenario in ['wechat_payment_sheet_wrong_amount_background_right', 'wechat_payment_sheet_unbounded',
                         'wechat_payment_sheet_bad_button_bounds', 'wechat_payment_sheet_disabled_button',
                         'wechat_payment_sheet_missing_method', 'wechat_payment_sheet_ambiguous_amount',
                         'wechat_payment_sheet_ambiguous_button', 'wechat_payment_sheet_amount_changed_before_review',
                         'wechat_payment_sheet_wechat_unselected', 'wechat_payment_sheet_multiple_selected',
                         'wechat_payment_sheet_method_changed', 'wechat_payment_sheet_ambiguous_method',
                         'wechat_payment_sheet_autopay']:
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario, wechat=True, amount=480)
                self.assertEqual(outcome['clicks'], ['立即购票', '立即支付'])
                self.assertEqual(outcome['state'], 'payment_sheet')
                self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], 'payment_sheet_manual_required')
                self.assertFalse(outcome['diagnosticWrites'][-1]['data']['automaticPayment'])
                self.assertNotIn('确认支付', outcome['clicks'])

    def test_payment_order_sheet_blocks_underlying_submit_and_further_congestion_refresh(self):
        before_submit = self.simulate('wechat_payment_sheet_before_submit', wechat=True, amount=480)
        self.assertEqual(before_submit['clicks'], ['立即购票'])
        self.assertEqual(before_submit['diagnosticWrites'][-1]['data']['state'], 'not_submitted')
        after_refresh = self.simulate('wechat_payment_sheet_during_congestion', wechat=True, amount=480)
        self.assertEqual(after_refresh['clicks'], ['立即购票', '立即支付', '刷新', '确认支付'])
        self.assertEqual(after_refresh['diagnosticWrites'][-1]['data']['state'], 'wechat_payment_page')
        self.assertEqual(after_refresh['clicks'].count('确认支付'), 1)

    def test_payment_order_sheet_uncertain_gateway_launch_is_never_retried(self):
        for scenario, terminal in [('wechat_payment_sheet_launch_failed', 'wechat_launch_attempted'),
                                   ('wechat_payment_sheet_launch_timeout', 'wechat_launch_unconfirmed')]:
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario, wechat=True, amount=480)
                self.assertEqual(outcome['clicks'], ['立即购票', '立即支付', '确认支付'])
                self.assertEqual(outcome['state'], 'payment_sheet')
                self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], terminal)
                self.assertEqual(next(iter(outcome['stored'].values()))['state'], 'wechat_launch_attempted')
                self.assertFalse(outcome['diagnosticWrites'][-1]['data']['automaticPayment'])

    def test_attendee_checkbox_update_does_not_mark_wechat_method_selected(self):
        outcome = self.simulate('wechat_cashier_unselected_after_attendee', wechat=True)
        self.assertEqual(outcome['clicks'], ['立即购票', '[观演人勾选框]', '提交订单'])
        self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], 'cashier_manual_required')

    def test_wechat_foreground_is_not_mistaken_for_verified_payment(self):
        for scenario in ['wechat_chat', 'wechat_wrong_amount']:
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario, wechat=True)
                self.assertEqual(outcome['clicks'], ['立即购票', '提交订单'])
                self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], 'wechat_foreground_unconfirmed')

    def test_submitted_congestion_can_refresh_to_wechat_without_another_submit(self):
        outcome = self.simulate('wechat_congestion_after_submit', wechat=True)
        self.assertEqual(outcome['clicks'], ['立即购票', '提交订单', '刷新', '刷新', '刷新'])
        self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], 'wechat_payment_page')

    def test_congestion_probe_inspects_real_fields_without_any_action_or_lock(self):
        for scenario, present, candidates, enabled in [
                ('probe_detail', False, 0, False), ('probe_congestion', True, 1, True),
                ('probe_congestion_disabled', True, 1, False), ('probe_congestion_ambiguous', True, 2, False)]:
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario, congestion_probe=True)
                self.assertEqual(outcome['clicks'], [])
                self.assertEqual(outcome['stored'], {})
                self.assertEqual(outcome['lockFiles'], {})
                record = outcome['diagnosticWrites'][-1]['data']
                self.assertTrue(record['readOnly'])
                self.assertEqual(record['congestionProbe']['present'], present)
                self.assertEqual(record['congestionProbe']['refreshCandidates'], candidates)
                self.assertEqual(record['congestionProbe']['refreshEnabled'], enabled)
                self.assertEqual(outcome['treeReads'], 1)
                self.assertNotIn(RAW_PROFILE['users'][0], str(record))

    def test_payment_flow_submits_once_then_stops_without_payment_clicks(self):
        for scenario, state in [('direct_order', 'payment_page'), ('pending_after_submit', 'pending_payment'),
                                ('attendee_unchecked', 'payment_page'), ('sku_already_target', 'payment_page')]:
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario, payment=True)
                self.assertEqual(outcome['clicks'].count('提交订单'), 1)
                self.assertEqual(outcome['state'], 'payment')
                self.assertFalse(set(['微信支付', '确认付款', '去支付', '立即支付']) & set(outcome['clicks']))
                record = outcome['diagnosticWrites'][-1]['data']
                self.assertEqual(record['state'], state)
                self.assertFalse(record['automaticPayment'])
                self.assertNotIn(RAW_PROFILE['users'][0], str(record))

    def test_payment_flow_never_retries_uncertain_submission_or_verification(self):
        for scenario, state in [('submit_no_change', 'submission_unconfirmed'),
                                ('challenge_after_submit', 'verification_required'),
                                ('external_after_submit', 'submission_attempted')]:
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario, payment=True)
                self.assertEqual(outcome['clicks'], ['立即购票', '提交订单'])
                self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], state)
                self.assertEqual(next(iter(outcome['stored'].values()))['state'], 'submission_attempted')

    def test_payment_flow_revalidates_facts_and_never_guesses_submit_or_pay(self):
        for scenario in ['challenge_before_submit', 'wrong_before_submit', 'attendee_changed_before_submit',
                         'disabled_submit', 'ambiguous_submit', 'autopay_order', 'instant_pay_order',
                         'wrong_attendee', 'wrong_order_price', 'two_ticket_order']:
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario, payment=True)
                self.assertEqual(outcome['clicks'], ['立即购票'])
                self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], 'not_submitted')

    def test_delayed_page_updates_reach_cashier_without_repeated_actions(self):
        for scenario in ['tier_long_delay', 'quantity_long_delay', 'confirm_long_delay', 'order_long_delay']:
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario, payment=True)
                self.assertEqual(outcome['clicks'], ['立即购票', '看台 ¥1380', '确认', '提交订单'])
                self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], 'payment_page')

    def test_order_loading_polls_skip_geometry_without_skipping_submit_checks(self):
        outcome = self.simulate('order_long_delay', payment=True)
        self.assertGreater(outcome['stateReads']['order_loading'], 10)
        self.assertEqual(outcome['geometryReads'].get('order_loading', 0), 0)
        self.assertEqual(outcome['clicks'], ['立即购票', '看台 ¥1380', '确认', '提交订单'])
        self.assertGreater(outcome['propertyReads']['checkable'], 0)
        reads = outcome['diagnosticWrites'][-1]['data']['readTimings']
        self.assertTrue(any(read['mode'] is True for read in reads))
        self.assertLessEqual(len(reads), 120)
        self.assertTrue(all(set(read) == {'startedAfterOriginMs', 'elapsedMs', 'mode', 'nodes', 'phase',
                                         'rootReadMs', 'nativeProbeMs', 'treeCopyMs'} for read in reads))
        self.assertTrue(all(read['rootReadMs'] + read['nativeProbeMs'] + read['treeCopyMs'] ==
                            read['elapsedMs'] for read in reads))

    def test_native_order_reader_reuses_first_complete_order_but_keeps_fresh_submit_read(self):
        baseline = (Path(__file__).resolve().parents[1] / 'autox/versions' /
                    'maoyan_live_before_order_compact_20261014.js').read_text(encoding='utf-8')
        old = self.simulate('native_order_regular', merchant_payment=True, template=baseline)
        new = self.simulate('native_order_regular', merchant_payment=True)
        self.assertEqual(new['clicks'], ['立即购票', '看台 ¥1380', '确认', '立即支付'])
        self.assertEqual(new['clicks'], old['clicks'])
        self.assertEqual(new['stateReads']['order'], old['stateReads']['order'] - 1)
        self.assertEqual(new['stateReads']['order'], 2)
        self.assertEqual(new['diagnosticWrites'][-1]['data']['state'], 'pending_payment_sheet')
        self.assertEqual(new['unrecycledNativeNodes'], 0)
        self.assertEqual(new['doubleNativeRecycles'], 0)
        order_reads = [r for r in new['diagnosticWrites'][-1]['data']['readTimings'] if r['mode'] == 'order']
        self.assertEqual(len(order_reads), 2)

    def test_native_order_reader_revalidates_changes_before_submission(self):
        for change in ['challenge', 'autopay', 'wrong_price', 'attendee_changed', 'disabled_submit']:
            with self.subTest(change=change):
                outcome = self.simulate('native_order_' + change, merchant_payment=True)
                self.assertEqual(outcome['clicks'], ['立即购票', '看台 ¥1380', '确认'])
                self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], 'not_submitted')
                self.assertEqual(outcome['unrecycledNativeNodes'], 0)
                self.assertEqual(outcome['doubleNativeRecycles'], 0)

    def test_native_order_marker_failure_falls_back_without_losing_order_fields(self):
        for case in ['marker_throw', 'marker_missing']:
            with self.subTest(case=case):
                outcome = self.simulate('native_order_' + case, merchant_payment=True)
                self.assertEqual(outcome['clicks'], ['立即购票', '看台 ¥1380', '确认', '立即支付'])
                self.assertEqual(outcome['stateReads']['order'], 3)
                self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], 'pending_payment_sheet')
                self.assertEqual(outcome['unrecycledNativeNodes'], 0)
                self.assertEqual(outcome['doubleNativeRecycles'], 0)

    def test_entry_loading_uses_light_reads_then_fresh_full_selection(self):
        outcome = self.simulate('transition_entry_regular', confirm_rehearsal=True)
        self.assertEqual(outcome['clicks'], ['立即购票', '看台 ¥1380', '确认'])
        self.assertGreaterEqual(outcome['stateReads']['entry_loading'], 3)
        self.assertEqual(outcome['geometryReads'].get('entry_loading', 0), 0)
        reads = outcome['diagnosticWrites'][-1]['data']['readTimings']
        self.assertTrue(any(read['mode'] == 'entry' for read in reads))
        self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], 'order_review_reached')

    def test_entry_sampling_is_twenty_ms_and_completes_sku_without_second_root(self):
        previous = (Path(__file__).resolve().parents[1] / 'autox' / 'versions' /
                    'maoyan_live_before_entry_poll_20261014.js').read_text(encoding='utf-8')
        old = self.simulate('transition_entry_timed', confirm_rehearsal=True, template=previous)
        new = self.simulate('transition_entry_timed', confirm_rehearsal=True)
        self.assertEqual(new['clicks'], old['clicks'])
        old_record = old['diagnosticWrites'][-1]['data']
        new_record = new['diagnosticWrites'][-1]['data']
        # Same fake page becomes ready at +96 ms; each loading read costs 5 ms.
        # This proves scheduling and root count, not a real-phone speed claim.
        self.assertEqual(old_record['entryClickToNextPageObservedMs'], 165)
        self.assertEqual(new_record['entryClickToNextPageObservedMs'], 105)
        self.assertEqual(new['stateReads']['sku'], old['stateReads']['sku'] - 1)
        self.assertEqual(new['geometryReads'].get('entry_loading', 0), 0)
        self.assertTrue(all(ms == 15 for ms in new['sleeps'][1:]))

    def test_entry_slow_reads_yield_without_adding_full_polling_interval(self):
        outcome = self.simulate('transition_entry_slow', confirm_rehearsal=True)
        self.assertEqual(outcome['clicks'], ['立即购票', '看台 ¥1380', '确认'])
        self.assertEqual(outcome['sleeps'], [20000, 2, 2])
        self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], 'order_review_reached')

    def test_ready_entry_sku_does_not_gain_a_new_tree_read(self):
        previous = (Path(__file__).resolve().parents[1] / 'autox' / 'versions' /
                    'maoyan_live_before_entry_poll_20261014.js').read_text(encoding='utf-8')
        old = self.simulate('sku_preselected', confirm_rehearsal=True, template=previous)
        new = self.simulate('sku_preselected', confirm_rehearsal=True)
        self.assertEqual(new['clicks'], old['clicks'])
        self.assertEqual(new['treeReads'], old['treeReads'])

    def test_entry_deferred_nodes_release_on_success_and_copy_failure(self):
        for scenario in ('transition_entry_regular', 'transition_entry_label_error',
                         'transition_entry_geometry_error'):
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario, confirm_rehearsal=True)
                self.assertEqual(outcome['unrecycledNativeNodes'], 0)
                self.assertEqual(outcome['doubleNativeRecycles'], 0)
                if scenario != 'transition_entry_regular':
                    self.assertEqual(outcome['clicks'], ['立即购票'])
                    self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], 'not_submitted')
                    self.assertNotIn('Private', str(outcome['logs']))

    def test_selection_readiness_samples_twenty_ms_but_does_not_confirm_early(self):
        previous = (Path(__file__).resolve().parents[1] / 'autox' / 'versions' /
                    'maoyan_live_before_readiness_timing_20261014.js').read_text(encoding='utf-8')
        old = self.simulate('quantity_short_delay', merchant_payment=True, template=previous)
        new = self.simulate('quantity_short_delay', merchant_payment=True)
        self.assertEqual(new['clicks'], old['clicks'])
        price_index = new['clicks'].index('看台 ¥1380')
        confirm_index = new['clicks'].index('确认')
        new_delay = new['clickTimes'][confirm_index] - new['clickTimes'][price_index]
        old_delay = old['clickTimes'][confirm_index] - old['clickTimes'][price_index]
        self.assertEqual(new_delay, 100)
        self.assertEqual(old_delay, 160)
        self.assertGreaterEqual(new_delay, 96)
        self.assertEqual(new['clicks'].count('确认'), 1)
        timings = new['diagnosticWrites'][-1]['data']['transitionTimings']
        selected = next(row for row in timings if row['stage'] == '票档控件就绪')
        self.assertEqual(selected['firstReadyObservedAfterOriginMs'] - selected['actionStartedAfterOriginMs'], 100)
        self.assertEqual(selected['readyToNextActionMs'], 0)
        self.assertEqual(selected['lastUnavailableObservedAfterOriginMs'] - selected['actionStartedAfterOriginMs'], 80)

    def test_transition_timings_are_observations_without_extra_queries_or_private_text(self):
        previous = (Path(__file__).resolve().parents[1] / 'autox' / 'versions' /
                    'maoyan_live_before_readiness_timing_20261014.js').read_text(encoding='utf-8')
        old = self.simulate('wechat_payment_sheet_full_flow', merchant_payment=True, template=previous)
        new = self.simulate('wechat_payment_sheet_full_flow', merchant_payment=True)
        self.assertEqual(new['clicks'], old['clicks'])
        self.assertEqual(new['treeReads'], old['treeReads'])
        record = new['diagnosticWrites'][-1]['data']
        self.assertEqual(record['state'], 'pending_payment_sheet')
        timings = record['transitionTimings']
        self.assertEqual([row['stage'] for row in timings],
                         ['入口后页面', '票档控件就绪', '确认后订单页面', '提交后支付页面'])
        for row in timings:
            self.assertTrue(row['completed'])
            self.assertGreater(row['readCount'], 0)
            self.assertGreaterEqual(row['firstReadyReadStartedAfterOriginMs'], row['actionStartedAfterOriginMs'])
            self.assertGreaterEqual(row['firstReadyObservedAfterOriginMs'], row['firstReadyReadStartedAfterOriginMs'])
            self.assertGreaterEqual(row['finishedAfterOriginMs'], row['firstReadyObservedAfterOriginMs'])
            self.assertNotIn('readBaseCount', row)
        self.assertNotIn('测试观演人', str(timings))
        self.assertNotIn(RAW_PROFILE['title'], str(timings))
        self.assertNotIn('loadingMs', str(timings))
        self.assertNotIn('确认支付', new['clicks'])

    def test_order_wait_does_not_complete_geometry_of_the_old_sku(self):
        previous = (Path(__file__).resolve().parents[1] / 'autox' / 'versions' /
                    'maoyan_live_before_readiness_timing_20261014.js').read_text(encoding='utf-8')
        old = self.simulate('order_still_shows_sku', merchant_payment=True, template=previous)
        new = self.simulate('order_still_shows_sku', merchant_payment=True)
        self.assertEqual(new['clicks'], old['clicks'])
        self.assertEqual(new['clickTimes'], old['clickTimes'])
        self.assertGreater(old['geometryReads'].get('order_pending_sku', 0), 0)
        self.assertEqual(new['geometryReads'].get('order_pending_sku', 0), 0)
        self.assertGreater(new['propertyReads']['checked'], 0)
        self.assertEqual(new['diagnosticWrites'][-1]['data']['state'], 'payment_page')
        self.assertEqual(new['clicks'].count('提交订单'), 1)

    def test_light_match_cannot_act_when_fresh_selection_changes_or_gets_verification(self):
        for scenario in ('transition_entry_changed_date', 'transition_entry_challenge'):
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario, payment=True)
                self.assertEqual(outcome['clicks'], ['立即购票'])
                self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], 'not_submitted')

    def test_attendee_update_over_old_timeout_is_observed_without_reclick(self):
        outcome = self.simulate('attendee_long_delay', payment=True)
        self.assertEqual(outcome['clicks'], ['立即购票', '[观演人勾选框]', '提交订单'])
        self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], 'payment_page')

    def test_rect_reuse_preserves_click_coordinates_without_per_node_allocations(self):
        outcome = self.simulate('sku_already_target', payment=True)
        self.assertEqual(outcome['clicks'], ['立即购票', '看台 ¥1380', '确认', '提交订单'])
        self.assertLessEqual(outcome['rectAllocations'], outcome['treeReads'])
        self.assertGreater(sum(outcome['geometryReads'].values()), outcome['rectAllocations'] * 3)

    def test_changed_date_or_unproven_reselected_tier_prevents_order_creation(self):
        date = self.simulate('retained_date_changed', payment=True)
        self.assertEqual(date['clicks'], ['立即购票'])
        self.assertTrue(any('预选场次状态已变化' in log for log in date['logs']))
        tier = self.simulate('retained_tier_changed', payment=True)
        self.assertEqual(tier['clicks'], ['立即购票', '看台 ¥1380'])
        self.assertNotIn('确认', tier['clicks'])
        self.assertNotIn('提交订单', tier['clicks'])

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

    def test_challenge_at_fresh_price_or_confirm_read_prevents_that_click(self):
        for scenario, expected in (
                ('challenge_before_price', ['立即购票']),
                ('challenge_before_confirm', ['立即购票', '看台 ¥1380'])):
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario)
                self.assertEqual(outcome['clicks'], expected)
                self.assertTrue(any('出现验证' in log for log in outcome['logs']))

    def test_selection_rehearsal_does_not_read_order_only_properties(self):
        outcome = self.simulate('sku_preselected', rehearsal=True)
        self.assertEqual(outcome['propertyReads'],
                         {'className': 0, 'checkable': 0, 'checked': 0})
        self.assertEqual(outcome['clicks'], ['立即购票', '看台 ¥1380'])

    def test_immediate_preselected_flow_avoids_redundant_tree_reads(self):
        outcome = self.simulate('sku_preselected')
        self.assertLessEqual(outcome['treeReads'], 8)
        self.assertEqual(outcome['clicks'], ['立即购票', '看台 ¥1380', '确认'])
        self.assertTrue(any('页面读取=' in log for log in outcome['logs']))

    def test_delayed_entry_polling_skips_geometry_and_extra_sleep_on_slow_reads(self):
        outcome = self.simulate('slow_entry')
        self.assertEqual(outcome['clicks'], ['立即购票'])
        self.assertGreater(outcome['stateReads']['detail'], 2)
        # All decision reads skip geometry; only the fresh pre-click read
        # fetches the candidate button bounds.
        self.assertEqual(outcome['geometryReads']['detail'], 1)
        self.assertEqual(outcome['sleeps'], [20000])

    def test_hot_entry_discovery_and_enabled_check_share_one_fresh_decision_read(self):
        outcome = self.simulate('slow_entry', payment=True)
        record = outcome['diagnosticWrites'][-1]['data']
        self.assertEqual(outcome['clicks'], ['立即购票', '提交订单'])
        # The fifth slow read reveals the enabled control; a sixth scan would
        # add 120 ms between discovery and the touch.
        self.assertEqual(outcome['stateReads']['detail'], 5)
        self.assertEqual(outcome['geometryReads']['detail'], 1)
        self.assertEqual(record['entryDecisionReadMs'], 120)
        self.assertEqual(record['entryDiscoveryToClickMs'], 0)
        self.assertGreaterEqual(outcome['detailReadStartedAfterSaleMs'][-1], 0)
        self.assertEqual(outcome['sleeps'], [20000])

    def test_preselected_date_is_retained_but_target_tier_is_selected_once(self):
        outcome = self.simulate('sku_already_target')
        self.assertEqual(outcome['clicks'], ['立即购票', '看台 ¥1380', '确认'])
        self.assertLessEqual(outcome['treeReads'], 8)
        self.assertEqual(outcome['geometryReads']['detail'], 1)
        self.assertTrue(any('沿用已选日期' in log and '点击目标票档' in log
                            for log in outcome['logs']))

    def test_fast_entry_appearing_between_polls_is_seen_within_twenty_ms(self):
        outcome = self.simulate('fast_entry', payment=True)
        record = outcome['diagnosticWrites'][-1]['data']
        # Synthetic clock: entry appears 30ms after monitoring begins.
        # The former 80ms cadence discovered it at +80ms; now it is +40ms.
        self.assertEqual(record['entryFoundAfterSaleMs'], 21040)
        self.assertEqual(outcome['clicks'], ['立即购票', '提交订单'])
        self.assertEqual(outcome['geometryReads']['detail'], 1)
        self.assertEqual(outcome['sleeps'][:3], [20000, 20, 20])

    def test_prepared_start_skips_detail_and_selection_then_submits_once(self):
        for scenario, button in [('prepared_confirm', '确认'), ('prepared_entry', '立即购票'),
                                 ('prepared_countdown', '确认')]:
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario, payment=True)
                self.assertEqual(outcome['clicks'], [button, '提交订单'])
                self.assertNotIn('detail', outcome['stateReads'])
                self.assertGreaterEqual(outcome['clickTimes'][0], 0)
                self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], 'payment_page')

    def test_prepared_start_guards_identity_selection_quantity_and_action(self):
        for scenario in ['prepared_unselected', 'prepared_two_tickets', 'prepared_ambiguous',
                         'prepared_changed', 'prepared_challenge', 'prepared_retained_changed',
                         'prepared_action_changed', 'prepared_disabled']:
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario, payment=True)
                self.assertEqual(outcome['clicks'], [])
                self.assertEqual(outcome['lockFiles'], {})

    def test_prepared_start_uncertain_transition_does_not_repeat_touch(self):
        outcome = self.simulate('prepared_no_change', payment=True)
        self.assertEqual(outcome['clicks'], ['确认'])
        self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], 'not_submitted')

    def test_prepared_start_rehearsal_never_confirms_or_claims_attempt(self):
        outcome = self.simulate('prepared_confirm', rehearsal=True)
        self.assertEqual(outcome['clicks'], [])
        self.assertEqual(outcome['lockFiles'], {})

    def test_prepared_readonly_probe_checks_selected_flags_without_actions(self):
        for scenario, valid in [('prepared_confirm', True), ('prepared_unselected', False)]:
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario, probe=True)
                self.assertEqual(outcome['clicks'], [])
                self.assertEqual(outcome['lockFiles'], {})
                self.assertEqual(outcome['treeReads'], 1)
                self.assertEqual(any('选中状态已匹配' in log for log in outcome['logs']), valid)
                facts = outcome['diagnosticWrites'][-1]['data']['preparedProbe']
                self.assertTrue(facts['layout'])
                self.assertEqual(facts['dateCandidates'], 1)
                self.assertEqual(facts['tierCandidates'], 1)
                self.assertEqual(facts['tierSelected'], valid)

    def test_prepared_entry_sets_configured_tier_once_on_actual_sku(self):
        outcome = self.simulate('prepared_to_sku', payment=True)
        self.assertEqual(outcome['clicks'], ['立即购票', '看台 ¥1380', '确认', '提交订单'])
        self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], 'payment_page')
        self.assertNotIn('detail', outcome['stateReads'])

    def test_prepared_entry_sku_confirmation_does_not_repeat_on_uncertain_transition(self):
        outcome = self.simulate('prepared_to_sku_no_change', payment=True)
        self.assertEqual(outcome['clicks'], ['立即购票', '看台 ¥1380', '确认'])
        self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], 'not_submitted')

    def test_prepared_start_requires_selection_evidence(self):
        outcome = self.simulate('prepared_unselected', wechat=True)
        self.assertEqual(outcome['clicks'], [])
        self.assertFalse(outcome['diagnosticWrites'][-1]['data']['automaticPayment'])

    def test_user_preselected_sets_only_configured_tier_on_intermediate_sku(self):
        outcome = self.simulate('prepared_to_sku', wechat=True)
        self.assertEqual(outcome['clicks'], ['立即购票', '看台 ¥1380', '确认', '提交订单'])
        self.assertEqual(outcome['clicks'].count('看台 ¥1380'), 1)

    def test_any_configured_tier_is_reselected_when_reservation_is_lost(self):
        for amount in [480, 580, 780, 980, 1080, 1380, 2380]:
            with self.subTest(amount=amount):
                result = self.simulate('sku_preselected', wechat=True, amount=amount)
                self.assertEqual(result['clicks'], ['立即购票', f'看台 ¥{amount}', '确认', '提交订单'])
                self.assertEqual(result['state'], 'payment')

    def test_recorded_phone_tier_overrides_placeholder_and_reselects_after_entry(self):
        result = self.simulate('phone_valid', wechat=True, source='phone')
        self.assertEqual(result['clicks'], ['立即购票', '看台 ¥580', '确认', '提交订单'])
        self.assertNotIn('看台 ¥1380', result['clicks'])

    def test_phone_capture_must_be_fresh_and_for_this_session(self):
        for scenario in ['phone_missing', 'phone_stale', 'phone_wrong_date', 'phone_two_tickets']:
            with self.subTest(scenario=scenario):
                result = self.simulate(scenario, wechat=True, source='phone')
                self.assertEqual(result['clicks'], [])
                self.assertEqual(result['lockFiles'], {})

    def test_capture_is_readonly_and_does_not_claim_purchase_attempt(self):
        result = self.simulate('prepared_confirm', capture=True)
        self.assertEqual(result['clicks'], [])
        self.assertEqual(result['lockFiles'], {})
        records = list(result['stored'].values())
        self.assertEqual(len(records), 1)
        self.assertEqual((records[0]['amount'], records[0]['quantity']), (1380, 1))
        self.assertNotIn(RAW_PROFILE['users'][0], str(result['stored']))
        for scenario in ['prepared_unselected', 'prepared_two_tickets', 'prepared_duplicate_tier', 'probe_detail']:
            invalid = self.simulate(scenario, capture=True)
            self.assertEqual(invalid['clicks'], [])
            self.assertEqual(invalid['stored'], {})

    def test_readonly_capture_is_reusable_in_a_separate_formal_engine(self):
        captured = self.simulate('prepared_capture_other', capture=True, source='phone')
        result = self.simulate('phone_chain', wechat=True, source='phone',
                               initial_storage=captured['stored'])
        self.assertEqual(result['clicks'], ['立即购票', '看台 ¥580', '确认', '提交订单'])
        changed = self.simulate('phone_chain', wechat=True, source='phone', amount=780,
                                initial_storage=captured['stored'])
        self.assertEqual(changed['clicks'], [])

    def test_button_ready_is_insufficient_when_tier_did_not_become_selected(self):
        result = self.simulate('price_wrong_selection', wechat=True)
        self.assertNotIn('确认', result['clicks'])
        self.assertNotIn('提交订单', result['clicks'])

    def test_user_preselected_disabled_or_ambiguous_buttons_are_not_tapped(self):
        for scenario in ['prepared_disabled', 'prepared_ambiguous', 'prepared_two_tickets']:
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario, wechat=True)
                self.assertEqual(outcome['clicks'], [])
                self.assertEqual(outcome['lockFiles'], {})

    def test_long_notice_does_not_block_entry_matching_or_hide_verification(self):
        outcome = self.simulate('long_detail_notice', wechat=True)
        self.assertEqual(outcome['clicks'], ['立即购票', '提交订单'])
        blocked = self.simulate('long_detail_challenge', wechat=True)
        self.assertEqual(blocked['clicks'], [])
        self.assertEqual(blocked['lockFiles'], {})

    def test_slow_countdown_read_consumes_polling_interval(self):
        outcome = self.simulate('slow_countdown')
        self.assertEqual(outcome['clicks'], ['立即购票'])
        self.assertNotIn(200, outcome['sleeps'])
        self.assertNotIn(50, outcome['sleeps'])
        self.assertTrue(any(0 < delay <= 80 for delay in outcome['sleeps']))

    def test_presale_crossing_read_requires_fresh_entry_decision_with_separate_read_cost(self):
        outcome = self.simulate('slow_countdown', payment=True)
        record = outcome['diagnosticWrites'][-1]['data']
        self.assertEqual(record['entryClickAfterSaleMs'], outcome['clickTimes'][0])
        self.assertEqual(record['entryDiscoveryToClickMs'], 0)
        self.assertEqual(record['entryDecisionReadMs'], 120)
        self.assertEqual(record['entryClickToNextPageObservedMs'], 120)
        self.assertEqual(record['screenRootReadMs'] + record['screenTreeCopyMs'], record['screenReadMs'])
        self.assertGreaterEqual(record['entryClickAfterSaleMs'], 0)
        # The read that starts before sale is never used for the touch, even
        # though retrieving that root takes 120 ms and crosses the sale time.
        self.assertLess(outcome['detailReadStartedAfterSaleMs'][-2], 0)
        self.assertGreaterEqual(outcome['detailReadStartedAfterSaleMs'][-1], 0)
        self.assertEqual(outcome['onSaleDetailReads'], 2)

    def test_challenge_in_fresh_read_after_countdown_prevents_entry_and_lock(self):
        outcome = self.simulate('slow_countdown_challenge', payment=True)
        self.assertEqual(outcome['clicks'], [])
        self.assertEqual(outcome['lockFiles'], {})
        self.assertEqual(outcome['diagnosticWrites'][-1]['data']['entryClickAfterSaleMs'], None)

    def test_sale_reached_during_sleep_does_not_reuse_presale_snapshot(self):
        outcome = self.simulate('sale_during_sleep', payment=True)
        self.assertGreaterEqual(outcome['clickTimes'][0], 0)
        self.assertEqual(outcome['onSaleDetailReads'], 1)
        self.assertEqual(outcome['detailReadStartedAfterSaleMs'][-1], 0)

    def test_challenge_appearing_during_sale_wait_blocks_entry_from_old_startup_tree(self):
        outcome = self.simulate('sale_during_sleep_challenge', payment=True)
        self.assertEqual(outcome['clicks'], [])
        self.assertEqual(outcome['stored'], {})
        self.assertEqual(outcome['lockFiles'], {})
        self.assertEqual(outcome['onSaleDetailReads'], 1)
        self.assertLess(outcome['detailReadStartedAfterSaleMs'][0], 0)
        self.assertEqual(outcome['detailReadStartedAfterSaleMs'][-1], 0)
        self.assertEqual(outcome['diagnosticWrites'][-1]['data']['entryClickAfterSaleMs'], None)

    def test_congestion_refreshes_dialog_then_continues_without_repeated_submit(self):
        for scenario in ['congestion_before_order', 'congestion_after_submit']:
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario, payment=True)
                self.assertEqual(outcome['clicks'].count('刷新'), 3)
                self.assertEqual(outcome['clicks'].count('提交订单'), 1)
                self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], 'payment_page')
                self.assertEqual(outcome['diagnosticWrites'][-1]['data']['congestionRefreshes'], 3)
                refresh_times = [time for label, time in zip(outcome['clicks'], outcome['clickTimes']) if label == '刷新']
                self.assertTrue(all(b - a >= 400 for a, b in zip(refresh_times, refresh_times[1:])))

    def test_refresh_loop_only_reads_refresh_geometry_and_restores_order_fields(self):
        outcome = self.simulate('congestion_after_submit', payment=True)
        self.assertEqual(outcome['congestionUnderlyingGeometryReads'], 0)
        self.assertEqual(outcome['clicks'].count('刷新'), 3)
        self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], 'payment_page')
        outcome = self.simulate('congestion_before_order', payment=True)
        self.assertLessEqual(outcome['congestionUnderlyingGeometryReads'], 16)
        self.assertEqual(outcome['clicks'].count('提交订单'), 1)

    def test_computation_cache_reduces_hashes_but_checks_fresh_attendee_name(self):
        outcome = self.simulate('attendee_long_delay', payment=True)
        self.assertLessEqual(outcome['hashComputations'], 3)
        self.assertEqual(outcome['clicks'], ['立即购票', '[观演人勾选框]', '提交订单'])
        self.assertNotIn(RAW_PROFILE['users'][0], str(outcome['diagnosticWrites']))
        outcome = self.simulate('name_changed_before_submit', payment=True)
        self.assertEqual(outcome['clicks'], ['立即购票'])
        self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], 'not_submitted')

    def test_congestion_limits_never_click_underlying_submit_or_pay(self):
        for scenario, clicks in [('congestion_disabled', 0),
                                 ('congestion_ambiguous', 0), ('congestion_wrong_order', 0),
                                 ('congestion_challenge', 1)]:
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario, payment=True)
                self.assertEqual(outcome['clicks'].count('刷新'), clicks)
                self.assertNotIn('提交订单', outcome['clicks'])
                self.assertNotIn('立即支付', outcome['clicks'])

    def test_refresh_requires_the_button_and_hints_in_one_bounded_dialog(self):
        for scenario in ['congestion_background_refresh_only', 'congestion_unbounded_dialog']:
            with self.subTest(scenario=scenario):
                result = self.simulate(scenario, payment=True)
                self.assertNotIn('刷新', result['clicks'])
                self.assertNotIn('提交订单', result['clicks'])
                self.assertTrue(any('弹窗内刷新按钮' in log for log in result['logs']))

    def test_dialog_refresh_is_distinguished_from_the_same_label_on_background(self):
        result = self.simulate('congestion_dialog_with_background_refresh', payment=True)
        self.assertEqual(result['clicks'].count('刷新'), 3)
        self.assertEqual(result['clicks'].count('提交订单'), 1)

    def test_description_only_congestion_hints_still_belong_to_dialog(self):
        result = self.simulate('congestion_description_hints', payment=True)
        self.assertEqual(result['clicks'].count('刷新'), 3)
        self.assertEqual(result['clicks'].count('提交订单'), 1)

    def test_persistent_congestion_stops_at_sale_window_without_submitting(self):
        outcome = self.simulate('congestion_forever', payment=True)
        self.assertGreater(outcome['clicks'].count('刷新'), 60)
        self.assertLessEqual(outcome['clicks'].count('刷新'), 3000)
        self.assertLess(max(outcome['clickTimes']), 1200000)
        self.assertNotIn('提交订单', outcome['clicks'])
        reads = outcome['diagnosticWrites'][-1]['data']['readTimings']
        self.assertEqual(len(reads), 120)
        self.assertGreater(reads[0]['startedAfterOriginMs'], 1000000)
        self.assertEqual([read['startedAfterOriginMs'] for read in reads],
                         sorted(read['startedAfterOriginMs'] for read in reads))

    def test_refresh_rereads_when_dialog_disappears_or_context_changes(self):
        for scenario in ['congestion_disappears', 'congestion_changed_while_waiting', 'congestion_payment']:
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario, payment=True)
                self.assertEqual(outcome['clicks'].count('刷新'), 1)
                self.assertNotIn('确认付款', outcome['clicks'])
                if scenario != 'congestion_disappears':
                    self.assertNotIn('提交订单', outcome['clicks'])

    def test_congestion_in_readonly_and_rehearsal_never_refreshes(self):
        for mode in [{'probe': True}, {'rehearsal': True}]:
            with self.subTest(mode=mode):
                outcome = self.simulate('congestion_before_order', **mode)
                self.assertNotIn('刷新', outcome['clicks'])
                self.assertNotIn('提交订单', outcome['clicks'])

    def test_entry_is_revalidated_when_city_or_challenge_changes_before_click(self):
        for scenario in ('challenge_before_entry', 'wrong_detail_before_entry'):
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario)
                self.assertEqual(outcome['clicks'], [])
                self.assertEqual(outcome['stored'], {})
                self.assertEqual(outcome['lockFiles'], {})

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
            self.assertIn('var SUBMIT_TO_PAYMENT = true;', output.read_text(encoding='utf-8'))
            self.assertIn('var OPEN_WECHAT_PAYMENT = true;', output.read_text(encoding='utf-8'))
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

    def test_benchmark_transfer_is_separate_and_does_not_embed_private_profile(self):
        profile = validate_profile({**RAW_PROFILE, 'serial': ''})
        calls=[]
        def fake_run(args):
            calls.append(args)
            return 'List of devices attached\nphone-1\tdevice\n' if args[1:] == ['devices'] else ''
        with tempfile.TemporaryDirectory() as tmp:
            adb=Path(tmp)/'adb.exe'; adb.touch()
            live=Path(tmp)/'live.js'; live.write_text('unchanged purchase script',encoding='utf-8')
            output=Path(tmp)/'benchmark.js'
            open_first_click_benchmark_on_phone(profile,adb=adb,output=output,run=fake_run)
            body=output.read_text(encoding='utf-8')
            self.assertIn('org.autojs.autoxjs.v7',body)
            self.assertNotIn(RAW_PROFILE['users'][0],body)
            self.assertNotIn('attendeeNameSha256',body)
            self.assertNotIn('private-device-serial',body)
            self.assertNotIn('var TARGET =',body)
            self.assertEqual(live.read_text(encoding='utf-8'),'unchanged purchase script')
        self.assertEqual(calls[1][-1],'/sdcard/Download/autox-first-click-benchmark.js')
        self.assertEqual(calls[2][1:6],['-s','phone-1','shell','am','start'])
        self.assertEqual(len(calls),3)

    def test_missing_benchmark_fails_before_any_phone_command(self):
        calls=[]
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                open_first_click_benchmark_on_phone(validate_profile(RAW_PROFILE),
                    source=Path(tmp)/'missing.js',run=lambda args:calls.append(args))
        self.assertEqual(calls,[])

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

    def test_year_before_zhang_artist_name_is_not_a_ticket_quantity(self):
        outcome = self.simulate('combined_order_numeric_zhang_title', merchant_payment=True, amount=1080)
        self.assertEqual(outcome['clicks'], ['立即购票', '立即支付'])
        self.assertEqual(outcome['state'], 'payment')
        self.assertNotIn('确认支付', outcome['clicks'])

    def test_zhang_artist_name_does_not_hide_a_real_two_ticket_order(self):
        outcome = self.simulate('combined_order_numeric_zhang_title_two_tickets', merchant_payment=True, amount=1080)
        self.assertEqual(outcome['clicks'], ['立即购票'])
        self.assertEqual(outcome['state'], 'order')

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

    def test_slow_attendee_reads_do_not_add_polling_sleep_or_repeat_click(self):
        outcome = self.simulate('slow_attendee_no_change')
        self.assertEqual(outcome['clicks'], ['立即购票', '[观演人勾选框]'])
        self.assertNotIn(40, outcome['sleeps'])
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

    def test_date_transition_waits_for_sheet_without_repeating_date_click(self):
        outcome = self.simulate('date_transition')
        self.assertEqual(outcome['clicks'], ['立即购票', '12月5日 周六 19:00', '看台 ¥1380', '确认'])
        self.assertEqual(outcome['state'], 'order')
        self.assertEqual(outcome['stateReads']['date_loading'], 3)

    def test_disabled_entry_never_clicked_or_claimed(self):
        outcome = self.simulate('disabled_entry')
        self.assertEqual(outcome['clicks'], [])
        self.assertEqual(outcome['lockFiles'], {})
        self.assertEqual(outcome['stored'], {})

    def test_entry_label_before_enabled_waits_then_clicks_without_extra_final_read(self):
        outcome = self.simulate('entry_enabled_later', payment=True)
        self.assertEqual(outcome['clicks'], ['立即购票', '提交订单'])
        self.assertEqual(outcome['clickTimes'][0], 21040)
        # One startup label read and three fresh entry polls at +0/+20/+40 ms.
        # Disabled polls do not claim or click; the first enabled poll does both.
        self.assertEqual(outcome['stateReads']['detail'], 4)
        self.assertEqual(outcome['geometryReads']['detail'], 3)
        self.assertEqual(outcome['sleeps'], [20000, 20, 20])
        self.assertEqual(outcome['diagnosticWrites'][-1]['data']['entryDiscoveryToClickMs'], 0)
        self.assertEqual(len(outcome['lockFiles']), 1)

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

    def test_already_selected_target_is_clicked_once_without_preselection_branch(self):
        outcome = self.simulate('sku_already_target', confirm_rehearsal=True)
        self.assertEqual(outcome['clicks'], ['立即购票', '看台 ¥1380', '确认'])
        self.assertEqual(outcome['state'], 'order')
        self.assertEqual(outcome['clicks'].count('看台 ¥1380'), 1)
        self.assertFalse(outcome['diagnosticWrites'][-1]['data']['skuDecision']['preselectionChecked'])

    def test_reclick_that_clears_selection_cannot_confirm_or_submit(self):
        outcome = self.simulate('sku_preselected_toggle_off', payment=True)
        self.assertEqual(outcome['clicks'], ['立即购票', '看台 ¥1380'])
        self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], 'not_submitted')
        self.assertNotIn('确认', outcome['clicks'])
        self.assertNotIn('提交订单', outcome['clicks'])

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

    def test_unknown_selection_sets_target_once_but_never_confirms_without_proof(self):
        outcome = self.simulate('sku_already_chosen')
        # With neither a selected flag nor a total, choose the configured card
        # once. The unchanged mock still supplies no proof, so never confirm.
        self.assertEqual(outcome['clicks'], ['立即购票', '看台 ¥1380'])
        self.assertEqual(outcome['state'], 'sku_selected')

    def test_android_preselection_without_selected_flags_selects_then_checks_split_total(self):
        outcome = self.simulate('android_sku_preselected_split_total', amount=580)
        self.assertEqual(outcome['clicks'], ['立即购票', '¥580', '确认'])
        self.assertEqual(outcome['state'], 'order')

    def test_android_unselected_page_without_placeholder_selects_target(self):
        outcome = self.simulate('android_sku_unselected_no_prompt', amount=580)
        self.assertEqual(outcome['clicks'], ['立即购票', '¥580', '确认'])
        self.assertEqual(outcome['state'], 'order')

    def test_android_limit_six_is_not_the_selected_quantity(self):
        outcome = self.simulate('android_sku_preselected_split_total', amount=580, payment=True)
        self.assertEqual(outcome['clicks'][-2:], ['确认', '提交订单'])
        self.assertEqual(outcome['state'], 'payment')

    def test_confirmation_rehearsal_reaches_review_without_attendee_submit_or_lock(self):
        outcome = self.simulate('android_sku_unselected_no_prompt', amount=580, confirm_rehearsal=True)
        self.assertEqual(outcome['clicks'], ['立即购票', '¥580', '确认'])
        self.assertEqual(outcome['state'], 'order')
        self.assertEqual(outcome['stored'], {})
        self.assertEqual(outcome['lockFiles'], {})
        self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], 'order_review_reached')

    def test_native_sku_clicks_date_tier_and_confirmation_without_coordinate_retries(self):
        for scenario in ('native_sku_regular', 'native_sku_zero_width_order', 'native_sku_child_label'):
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario, confirm_rehearsal=True, amount=1580)
                self.assertEqual(outcome['nativeActions'], ['12月5日 周六 19:00', '看台 ¥1580', '确认'])
                self.assertEqual(len(outcome['shortPresses']), 1)
                self.assertEqual(outcome['state'], 'order')
                diagnostic = outcome['diagnosticWrites'][-1]['data']
                self.assertEqual(diagnostic['state'], 'order_review_reached')
                self.assertTrue(all(t.get('method') == 'accessibility_action' for t in diagnostic['touchTimings'][1:]))
                self.assertNotIn('提交订单', outcome['clicks'])

    def test_native_sku_rejection_or_duplicate_never_falls_back_to_touch(self):
        for scenario in ('native_sku_rejected_tier', 'native_sku_duplicate_lookup'):
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario, confirm_rehearsal=True, amount=1580)
                self.assertEqual(len(outcome['shortPresses']), 1)
                self.assertNotIn('看台 ¥1580', outcome['clicks'])
                self.assertNotIn('确认', outcome['clicks'])
                self.assertEqual(outcome['state'], 'sku')

    def test_two_price_labels_in_one_clickable_card_are_one_tier(self):
        outcome = self.simulate('native_sku_double_label_card', confirm_rehearsal=True, amount=1098)
        self.assertEqual(outcome['nativeActions'], ['12月5日 周六 19:00', '看台 ¥1098', '确认'])
        self.assertEqual(outcome['state'], 'order')
        self.assertEqual(outcome['clicks'].count('看台 ¥1098'), 1)

    def test_split_amount_cannot_select_a_different_tier_category(self):
        outcome = self.simulate('native_sku_double_wrong_kind', confirm_rehearsal=True, amount=1098)
        self.assertNotIn('¥1098', outcome['clicks'])
        self.assertNotIn('内场1098元', outcome['clicks'])
        self.assertNotIn('确认', outcome['clicks'])
        self.assertEqual(outcome['state'], 'sku')

    def test_merchant_payment_mode_stops_before_payment_confirmation(self):
        outcome = self.simulate('wechat_payment_sheet_full_flow', merchant_payment=True, amount=780)
        self.assertEqual(outcome['clicks'], ['立即购票', '看台 ¥780', '确认', '立即支付'])
        self.assertEqual(outcome['state'], 'payment_sheet')
        self.assertEqual(outcome['diagnosticWrites'][-1]['data']['state'], 'pending_payment_sheet')
        self.assertNotIn('确认支付', outcome['clicks'])
        cashier = self.simulate('wechat_cashier_selected', merchant_payment=True)
        self.assertNotIn('立即支付', cashier['clicks'])

    def test_android_wrong_total_or_two_tickets_never_confirms(self):
        for scenario in ('android_sku_wrong_total', 'android_sku_two_tickets',
                         'android_sku_total_changed_before_confirm', 'android_sku_substring_price'):
            with self.subTest(scenario=scenario):
                outcome = self.simulate(scenario, amount=580, payment=True)
                self.assertNotIn('确认', outcome['clicks'])
                self.assertNotIn('提交订单', outcome['clicks'])
                self.assertNotEqual(outcome['state'], 'payment')

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

