// Offline fake accessibility tree for the locked prototype's unit tests.
// This file is never deployed to a phone.
const fs = require('fs');
const vm = require('vm');
const crypto = require('crypto');

const script = fs.readFileSync(process.argv[2], 'utf8');
const inputScenario = process.argv[3];
const nativeOrderCase = inputScenario.startsWith('native_order_') ? inputScenario.slice(13) : null;
const scenario = nativeOrderCase ? 'wechat_payment_sheet_full_flow' : inputScenario;
const target = JSON.parse(script.match(/^var TARGET = (.+);$/m)[1]);
const androidSkuScenario = scenario.startsWith('android_sku_');
const nativeSkuScenario = scenario.startsWith('native_sku_');
const numericZhangTitleScenario = scenario.startsWith('combined_order_numeric_zhang');
const combinedOrderScenario = scenario === 'combined_order_summary' || numericZhangTitleScenario;
const nativeActions = [];
const androidSkuFixture = androidSkuScenario ? JSON.parse(fs.readFileSync(
    require('path').join(__dirname, 'fixtures', 'maoyan_sku_split_total.json'), 'utf8')) : null;
const maskedScenario = scenario.startsWith('wechat_masked_');
const paymentSheetScenario = scenario.startsWith('wechat_payment_sheet') ||
    scenario === 'wechat_masked_selected_sheet' || scenario === 'payment_order_sheet';
const fullFastFlowScenario = scenario.startsWith('wechat_payment_sheet_full_flow');
const actualAmount = scenario.startsWith('wechat_preselected_price') || scenario.startsWith('phone_') ? 580 : target.amount;
const chosenAmount = scenario.startsWith('phone_') ? 580 : target.amount;
let now = target.saleEpochMs + 1000;
if (['spaced_reservation', 'slow_countdown', 'slow_countdown_challenge'].includes(scenario)) now = target.saleEpochMs - 30000;
if (scenario === 'fixed_reservation_switches') now = target.saleEpochMs - 30000;
if (scenario === 'fixed_reservation_already_switched') now = target.saleEpochMs - 21000;
if (scenario.startsWith('presale_reservation_')) now = target.saleEpochMs - 21000;
if (scenario === 'sale_during_sleep' || scenario === 'sale_during_sleep_challenge') now = target.saleEpochMs - 20060;
let state = 'detail';
if (scenario.startsWith('prepared_')) state = 'prepared';
if (scenario === 'prepared_countdown') now = target.saleEpochMs - 20060;
let dateClicked = false;
if (scenario === 'probe_order') state = 'order';
if (scenario.startsWith('probe_congestion')) state = 'order';
if (scenario === 'probe_sku') state = 'sku';
let attendeeChecked = false;
let attendeeCheckedAt = Infinity;
let selectedSkuAt = Infinity;
let orderReadyAt = Infinity;
let entryReadyAt = Infinity;
let rectAllocations = 0;
let congestionVisible = scenario.startsWith('probe_congestion');
let refreshCount = 0;
let congestionNonRefreshGeometryReads = 0;
let congestionUnderlyingGeometryReads = 0;
let hashComputations = 0;
let regexCompilations = 0;
const clicks = [];
const clickTimes = [];
let onSaleDetailReads = 0;
const logs = [];
const diagnosticWrites = [];
const stored = {};
if (scenario.startsWith('phone_') && scenario !== 'phone_missing') {
    stored[target.selectionKey] = {version: 1, key: target.selectionKey,
        date: target.dateLabels[0], amount: chosenAmount, tierLabel: '看台 ¥' + chosenAmount,
        quantity: 1, at: now - (scenario === 'phone_stale' ? 86400001 : 1000)};
    if (scenario === 'phone_wrong_date') stored[target.selectionKey].date = '2026-12-06';
    if (scenario === 'phone_two_tickets') stored[target.selectionKey].quantity = 2;
}
if (scenario.startsWith('phone_')) target.amount = chosenAmount;
if (scenario === 'prepared_capture_other') target.amount = 580;
if (process.argv[4]) {
    for (const key of Object.keys(stored)) delete stored[key];
    Object.assign(stored, JSON.parse(fs.readFileSync(process.argv[4], 'utf8')));
}
const lockFiles = {};
let treeReads = 0;
let selectorReads = 0;
const stateReads = {};
const detailReadStartedAfterSaleMs = [];
const propertyReads = { className: 0, checkable: 0, checked: 0 };
const geometryReads = {};
const sleeps = [];
let unrecycledNativeNodes = 0;
let doubleNativeRecycles = 0;
if (scenario === 'already_claimed') {
    lockFiles['/private/qiangpiao-maoyan-' + target.attemptKey.replace(/:/g, '-') + '.lock'] = true;
}
const dateToken = ['decimal_price_payment_label', 'sku', 'sold_out_sku'].includes(scenario) ? target.dateLabels[0] :
    target.dateLabels.find(label => label.indexOf('月') >= 0);
const dateLabel = dateToken + ' 周六 19:00';

function androidSkuTree() {
    const selected = state === 'sku_selected';
    const total = scenario === 'android_sku_wrong_total' ? target.amount + 100 :
        scenario === 'android_sku_total_changed_before_confirm' && stateReads.sku_selected >= 2 ? target.amount + 100 :
        scenario === 'android_sku_two_tickets' ? target.amount * 2 : target.amount;
    function copy(raw) {
        let label = raw.text.replace('$TITLE', target.titleToken).replace('$CITY', target.city)
            .replace('$DATE', dateLabel).replace('$AMOUNT', String(target.amount)).replace('$TOTAL', String(total));
        if (!selected && (label === '数量' || /限购/.test(label) || /^\d+张$/.test(label) ||
                /^总计/.test(label) || label === String(total) || label === '明细' || label === '确认')) label = '';
        if (scenario === 'android_sku_two_tickets' && label === '1张') label = '2张';
        if (scenario === 'android_sku_substring_price' && raw.text === '¥$AMOUNT') label = '¥1580';
        const b = raw.bounds;
        const row = { text: label, desc: '', enabled: raw.enabled !== false, selected: false, checked: false,
            checkable: false, bounds: {left: b[0], top: b[1], right: b[2], bottom: b[3]} };
        if (raw.children) row.children = raw.children.map(copy);
        return row;
    }
    return copy(androidSkuFixture.root);
}

function androidSkuRows() {
    const output = [];
    function walk(row) { output.push(row); (row.children || []).forEach(walk); }
    walk(androidSkuTree());
    return output;
}

function item(label, index) {
    const confirmation = label === '确认';
    return { text: label, desc: '',
        className: label === '[观演人勾选框]' ?
            scenario === 'custom_checkable' ? 'android.view.View' : 'android.widget.CheckBox' : 'android.widget.TextView',
        checkable: label === '[观演人勾选框]' || state === 'cashier' && label === '微信支付',
        checked: !['attendee_unchecked', 'attendee_no_change', 'slow_attendee_no_change', 'attendee_delayed', 'attendee_long_delay', 'attendee_disabled', 'custom_checkable', 'wechat_masked_unchecked', 'wechat_cashier_unselected_after_attendee'].includes(scenario) && label === '[观演人勾选框]',
        enabled: !(scenario === 'disabled_confirm' && state === 'sku_selected' && confirmation) &&
            !(scenario === 'disabled_submit' && label === '提交订单') &&
            !(scenario === 'congestion_disabled' && label === '刷新') &&
            !(scenario === 'probe_congestion_disabled' && label === '刷新') &&
            !(scenario === 'wechat_cashier_disabled' && state === 'cashier' && label === '立即支付') &&
            !(scenario === 'confirm_long_delay' && confirmation && now < selectedSkuAt + 1200) &&
            !(scenario === 'disabled_entry' && label === '立即购票') &&
            !(scenario === 'entry_enabled_later' && label === '立即购票' && now < target.saleEpochMs + 21040) &&
            !(scenario === 'attendee_disabled' && label === '[观演人勾选框]') &&
            !(scenario === 'disabled_price' && label === '看台 ¥' + target.amount),
        selected: ((state === 'sku' || state === 'sku_selected') &&
            label === dateLabel && scenario !== 'sku' && !nativeSkuScenario &&
            (scenario !== 'date_transition' || dateClicked)) ||
            (state === 'sku_selected' && selectedSkuAt !== Infinity && scenario !== 'price_wrong_selection' &&
                label === '看台 ¥' + target.amount) ||
            (['sku_already_target', 'sku_preselected_toggle_off', 'retained_tier_changed', 'retained_date_changed',
              'prepared_to_sku', 'prepared_to_sku_no_change'].includes(scenario) &&
                label === '看台 ¥' + target.amount), bounds: {
        left: label === '刷新' && congestionVisible ? 500 : confirmation ? 640 + index * 10 : 20 + index * 2,
        top: label === '刷新' && congestionVisible ? 850 : confirmation ? 2070 + index * 5 :
            label.indexOf('立即') === 0 || label === '提交订单' ? 2100 : 400 + index * 70,
        right: label === '刷新' && congestionVisible ? 800 : confirmation ? 990 + index * 10 : 600 + index * 2,
        bottom: label === '刷新' && congestionVisible ? 920 : confirmation ? 2180 + index * 5 :
            label.indexOf('立即') === 0 || label === '提交订单' ? 2210 : 450 + index * 70
    }};
}
function rows() {
    if (androidSkuScenario && ['sku', 'sku_selected'].includes(state)) return androidSkuRows();
    if (state === 'prepared') {
        const labels = [target.titleToken, target.city, '场次', '票档', dateLabel,
            '看台 ¥' + target.amount, scenario === 'prepared_two_tickets' ? '2张' : '1张',
            now < target.saleEpochMs ? '已预约' :
                scenario === 'prepared_entry' || scenario.startsWith('prepared_to_sku') ? '立即购票' : '确认'];
        if (scenario === 'prepared_ambiguous') labels.push('立即购票');
        if (scenario === 'prepared_duplicate_tier') labels.push('看台 ¥' + target.amount);
        if (scenario === 'prepared_changed' && stateReads.prepared >= 3) labels[1] = '其他城市';
        if (scenario === 'prepared_action_changed' && stateReads.prepared >= 3) labels[7] = '立即支付';
        if (scenario === 'prepared_challenge' && stateReads.prepared >= 3) labels.push('人机验证');
        return labels.map(item).map(row => {
            row.selected = row.text === dateLabel || row.text === '看台 ¥' + target.amount;
            if (scenario === 'prepared_unselected' && row.text === '看台 ¥' + target.amount) row.selected = false;
            if (scenario === 'prepared_retained_changed' && stateReads.prepared >= 3 && row.text === dateLabel) row.selected = false;
            if (scenario === 'prepared_disabled' && row.text === '确认') row.enabled = false;
            return row;
        });
    }
    if (state === 'detail') {
        const labels = [target.titleToken, target.city,
            scenario.startsWith('fixed_reservation_') && now >= target.saleEpochMs - 1000 ? '开售' : '已预约',
            '立即购票'];
        if (scenario.startsWith('presale_reservation_')) labels[2] = '开售';
        if (scenario === 'presale_reservation_missing_no_entry') labels.pop();
        if (scenario === 'slow_entry' && stateReads.detail < 5) labels.pop();
        if (scenario === 'fast_entry' && now < target.saleEpochMs + 21030) labels.pop();
        if (state === 'detail' && ['challenge_before_entry', 'wrong_detail_before_entry'].includes(scenario) && stateReads.detail >= 2) {
            if (scenario === 'challenge_before_entry') labels.push('人机验证');
            else labels[1] = '其他城市';
        }
        if (scenario === 'spaced_reservation') labels[2] = '已   预\n约';
        if (scenario === 'slow_countdown_challenge' && onSaleDetailReads >= 2) labels.push('人机验证');
        if (scenario === 'sale_during_sleep_challenge' && now >= target.saleEpochMs) labels.push('人机验证');
        if (scenario === 'ambiguous_entry') labels.push('立即购票');
        if (scenario === 'long_detail_notice') labels.unshift('普通说明'.repeat(5000));
        if (scenario === 'long_detail_challenge') labels.unshift('普通说明'.repeat(5000) + '人机验证');
        return labels.map(item);
    }
    if (state === 'sku') {
        // Real SKU sheets omit the event title and city shown on the detail page.
        const labels = ['选择票档', dateLabel,
            scenario === 'sold_out_sku' ? '看台 ¥' + target.amount + ' 缺货登记' :
                '看台 ¥' + target.amount,
            '请选择票档'];
        if (scenario === 'duplicate_price_sku') labels.push('看台 ¥' + target.amount);
        if (['native_sku_double_label_card', 'native_sku_double_wrong_kind'].includes(scenario)) {
            if (scenario === 'native_sku_double_wrong_kind') labels[2] = '内场' + target.amount + '元';
            labels.push('¥' + target.amount);
        }
        if (scenario === 'sku' || nativeSkuScenario || scenario === 'date_transition' && !dateClicked) labels.push('2026-12-06 周日 19:00');
        if (scenario === 'sold_out_sibling') labels.push('缺货登记');
        return labels.map(item);
    }
    if (state === 'sku_selected' || state === 'order_pending_sku') {
        if (scenario === 'tier_long_delay' && now < selectedSkuAt + 1200) {
            return ['选择票档', dateLabel, '看台 ¥' + target.amount, '请选择票档'].map(item);
        }
        const labels = ['选择票档', dateLabel,
            '看台 ¥' + target.amount,
            scenario === 'wrong_quantity_after_price' ? '2张' : '1张', '确认'];
        if (scenario === 'quantity_long_delay' && now < selectedSkuAt + 1200) labels.splice(3, 1);
        if (scenario === 'quantity_short_delay' && now < selectedSkuAt + 96) labels.splice(3, 1);
        if (scenario === 'ambiguous_confirm') labels.push('确认');
        return labels.map(item);
    }
    if (state === 'order' || state === 'payment_sheet') {
        const price = combinedOrderScenario ? '看台 ￥' + target.amount +
            (scenario.endsWith('_two_tickets') ? ' ×2张' : ' ×1张') :
            scenario === 'decimal_price_payment_label' ?
            '¥' + target.amount + '.00' : '¥' +
            (scenario === 'wrong_order_price' ? target.amount + 100 :
                scenario === 'substring_order_price' ? 11380 :
                    state === 'payment_sheet' && scenario === 'wechat_payment_sheet_right_amount_background_wrong' ? actualAmount + 100 : actualAmount);
        const attendeeName = maskedScenario ?
            scenario === 'wechat_masked_wrong' || scenario === 'wechat_masked_changed_before_submit' && stateReads.order >= 3 ? '另***人' :
                scenario === 'wechat_masked_all_stars' ? '*****' :
                    scenario === 'wechat_masked_fullwidth' ? '测＊＊＊人' : '测***人' :
            scenario === 'wrong_attendee' ? '另一个人' : '测试观演人';
        const orderTitle = numericZhangTitleScenario ? '2\u200b0\u200b2\u200b6张智霖 “在” ' + target.titleToken :
            scenario === 'native_sku_zero_width_order' ? target.titleToken.split('').join('\u200b') : target.titleToken;
        const labels = [orderTitle, target.city,
            scenario === 'wrong_order_date' ? '2026-12-06 周日 19:00' : dateLabel,
            price, '实名观演人', attendeeName];
        const immediateReview = combinedOrderScenario || ['instant_pay_order', 'wechat_immediate_review'].includes(scenario) ||
            maskedScenario || paymentSheetScenario && scenario !== 'payment_order_sheet';
        if (immediateReview) labels.unshift('\u786e\u8ba4\u8d2d\u7968');
        if (scenario === 'wechat_masked_ambiguous') labels.push('测***人');
        if (scenario !== 'attendee_missing') labels.push('[观演人勾选框]');
        if (scenario === 'attendee_ambiguous' || scenario === 'wechat_masked_ambiguous_checkbox') labels.push('[观演人勾选框]');
        if (!combinedOrderScenario) labels.push(scenario === 'two_ticket_order' ? '2张' :
            scenario === 'eleven_ticket_order' ? '11张' : '1张');
        labels.push('取票方式',
            immediateReview ? '立即支付' : '提交订单');
        if (scenario === 'decimal_price_payment_label') labels.push('支付方式');
        if (scenario === 'ambiguous_submit') labels.push('提交订单');
        if (scenario === 'autopay_order') labels.push('免密支付');
        if (scenario === 'wrong_before_submit' && stateReads.order >= 3) labels[0] = '其他演出';
        if (scenario === 'name_changed_before_submit' && stateReads.order >= 3) labels[5] = '另一位观演人';
        if (congestionVisible) {
            labels.push('前方拥堵 请刷新重试', '刷新');
            if (['congestion_ambiguous', 'probe_congestion_ambiguous'].includes(scenario)) labels.push('刷新');
            if (scenario === 'congestion_wrong_order' ||
                    scenario === 'congestion_changed_while_waiting' && refreshCount >= 1 && now >= target.saleEpochMs + 21400) {
                labels[1] = '其他城市';
            }
        }
        const records = labels.map(item);
        if (scenario === 'wechat_masked_ambiguous') {
            // Both name labels belong to the same attendee region.
            const names = records.filter(row => row.text === '测***人');
            names[1].bounds = {...names[0].bounds, top: names[0].bounds.top + 5, bottom: names[0].bounds.bottom + 5};
        }
        if (scenario === 'wechat_masked_check_changed_before_submit' && stateReads.order >= 3) {
            records.forEach(row => { if (row.text === '[观演人勾选框]') row.checked = false; });
        }
        if (state === 'payment_sheet' || scenario === 'wechat_payment_sheet_before_submit' && stateReads.order >= 3) {
            const amount = scenario === 'wechat_payment_sheet_wrong_amount_background_right' ||
                scenario === 'wechat_payment_sheet_amount_changed_before_review' && stateReads.payment_sheet >= 2 ? 1580 : actualAmount;
            const sheetLabels = ['支付订单', '支付剩余时间 14:59', '¥' + amount + '.00', '支付方式', '微信支付', '云闪付', '确认支付'];
            if (scenario === 'wechat_payment_sheet_missing_method') sheetLabels.splice(3, 1);
            if (scenario === 'wechat_payment_sheet_ambiguous_amount') sheetLabels.splice(3, 0, '¥' + (amount + 100) + '.00');
            if (scenario === 'wechat_payment_sheet_ambiguous_button') sheetLabels.push('确认支付');
            if (scenario === 'wechat_payment_sheet_ambiguous_method') sheetLabels.push('微信支付');
            if (scenario === 'wechat_payment_sheet_autopay') sheetLabels.push('免密支付');
            if (scenario === 'wechat_payment_sheet_split_amount') sheetLabels.splice(2, 1, '¥', amount + '.00');
            const sheetRows = sheetLabels.map((label, index) => {
                const row = item(label, index);
                row.paymentSheet = true;
                row.bounds = {left: 80, top: 1090 + index * 120, right: 1000, bottom: 1160 + index * 120};
                if (label === '确认支付') row.bounds = {
                    left: 80, top: scenario === 'wechat_payment_sheet_bad_button_bounds' ? 1500 : 2190,
                    right: 1000, bottom: scenario === 'wechat_payment_sheet_bad_button_bounds' ? 1580 : 2310
                };
                if (label === '微信支付') {
                    row.checkable = true;
                    row.checked = scenario !== 'wechat_payment_sheet_wechat_unselected' &&
                        !(scenario === 'wechat_payment_sheet_method_changed' && stateReads.payment_sheet >= 2);
                    if (scenario === 'wechat_payment_sheet_sibling_selected') {
                        row.checkable = false;
                        row.checked = false;
                        row.wechatMethodRow = true;
                    }
                }
                if (label === '云闪付') { row.checkable = true; row.checked = scenario === 'wechat_payment_sheet_multiple_selected'; }
                if (scenario === 'wechat_payment_sheet_disabled_button' && label === '确认支付') row.enabled = false;
                return row;
            });
            if (scenario === 'wechat_payment_sheet_sibling_selected') {
                const method = sheetRows.find(row => row.text === '微信支付');
                sheetRows.push({text: '[微信选择框]', className: 'android.widget.RadioButton', enabled: true,
                    checkable: true, checked: true, paymentSheet: true, wechatMethodRow: true,
                    bounds: {left: 920, top: method.bounds.top + 10, right: 980, bottom: method.bounds.bottom - 10}});
            }
            records.push(...sheetRows);
        }
        return records;
    }
    if (state === 'payment') return (scenario === 'pending_after_submit' ?
        ['待付款', '去支付'] : ['收银台', '支付方式', '微信支付', '确认付款']).map(item);
    if (state === 'cashier') {
        const labels = ['收银台', '￥' + (scenario === 'wechat_cashier_wrong_amount' || scenario === 'wechat_preselected_price_changed' ? actualAmount + 10 : actualAmount), '微信支付',
            scenario === 'wechat_cashier_final_only' ? '确认付款' :
                scenario === 'wechat_cashier_explicit' ? '前往微信支付' : '立即支付'];
        if (scenario === 'wechat_cashier_autopay') labels.push('免密支付');
        return labels.map(item).map(row => {
            if (row.text === '微信支付') row.checked = !['wechat_cashier_unselected', 'wechat_cashier_unselected_after_attendee'].includes(scenario) &&
                !(scenario === 'wechat_cashier_method_changed' && stateReads.cashier >= 2);
            return row;
        });
    }
    if (state === 'wechat') return (scenario === 'wechat_chat' ? ['微信', '聊天'] :
        ['微信支付', '￥' + (scenario === 'wechat_wrong_amount' ? actualAmount + 1 : actualAmount), '确认支付', '支付方式']).map(item);
    if (state === 'challenge') return [item('人机验证', 0)];
    return [item('加载中', 0)];
}
function clickRow(row) {
    clicks.push(row.text);
    clickTimes.push(now - target.saleEpochMs);
    if (state === 'payment_sheet' && row.text === '确认支付') {
        if (scenario === 'wechat_payment_sheet_launch_failed') return false;
        if (scenario !== 'wechat_payment_sheet_launch_timeout') state = 'wechat';
        return true;
    }
    if (state === 'cashier' && ['立即支付', '前往微信支付'].includes(row.text)) {
        if (scenario !== 'wechat_cashier_timeout') state = 'wechat';
        return true;
    }
    if (row.text === '刷新' && congestionVisible) {
        refreshCount++;
        if (scenario === 'wechat_payment_sheet_during_congestion') { state = 'payment_sheet'; return true; }
        if (scenario === 'congestion_challenge') { state = 'challenge'; congestionVisible = false; }
        else if (scenario === 'congestion_payment') { state = 'payment'; congestionVisible = false; }
        else if (!['congestion_forever', 'congestion_disabled', 'congestion_ambiguous', 'congestion_wrong_order'].includes(scenario) && refreshCount >= 3) {
            congestionVisible = false;
            if (scenario === 'congestion_after_submit') state = 'payment';
            if (scenario === 'wechat_congestion_after_submit') state = 'wechat';
        }
        return true;
    }
    if (state === 'prepared' && ['立即购票', '确认'].includes(row.text)) {
        if (scenario.startsWith('prepared_to_sku')) state = 'sku_selected';
        else if (scenario !== 'prepared_no_change') state = 'order';
    } else if (state === 'detail' && row.text === '立即购票') {
        if (scenario === 'entry_first_touch_ignored' &&
                clicks.filter(label => label === '立即购票').length === 1) return true;
        state = ['sku_already_chosen', 'sku_already_target', 'sku_preselected_toggle_off', 'retained_tier_changed', 'retained_date_changed'].includes(scenario) ? 'sku_selected' :
            ['sku', 'sku_preselected', 'sold_out_sku', 'price_click_no_change',
             'duplicate_price_sku', 'wrong_quantity_after_price',
             'ambiguous_confirm', 'confirm_click_no_change',
             'disabled_confirm', 'price_auto_order', 'sold_out_sibling', 'disabled_price',
             'challenge_before_price', 'challenge_before_confirm', 'date_transition', 'tier_long_delay',
             'quantity_long_delay', 'quantity_short_delay', 'confirm_long_delay', 'order_long_delay',
             'order_still_shows_sku'].includes(scenario) ? 'sku' :
            scenario === 'unknown_after_entry' ? 'unknown' : 'order';
        if (scenario === 'challenge_after_entry') state = 'challenge';
        if (scenario.startsWith('phone_') || scenario === 'price_wrong_selection' || fullFastFlowScenario) state = 'sku';
        if (nativeSkuScenario) state = 'sku';
        if (scenario.startsWith('transition_entry_')) {
            state = 'entry_loading';
            if (scenario === 'transition_entry_timed') entryReadyAt = now + 96;
        }
        if (androidSkuScenario) state = ['android_sku_unselected_no_prompt', 'android_sku_substring_price'].includes(scenario) ? 'sku' : 'sku_selected';
        if (scenario.startsWith('congestion_') && scenario !== 'congestion_after_submit') congestionVisible = true;
    } else if (state === 'sku_selected' && scenario === 'sku_preselected_toggle_off' && row.text === '看台 ¥' + target.amount) {
        state = 'sku';
        selectedSkuAt = Infinity;
    } else if (state === 'sku' && row.text === dateLabel && scenario === 'date_transition') {
        dateClicked = true;
        state = 'date_loading';
    } else if (androidSkuScenario && ['sku', 'sku_selected'].includes(state) && row.text === '¥' + target.amount) {
        selectedSkuAt = now;
        state = 'sku_selected';
    } else if (state === 'sku' && row.text === '看台 ¥' + target.amount) {
        selectedSkuAt = now;
        if (scenario === 'price_auto_order') state = 'order';
        else if (scenario !== 'price_click_no_change') state = 'sku_selected';
    } else if (state === 'sku_selected' && row.text === '确认') {
        if (scenario === 'order_long_delay') { state = 'order_loading'; orderReadyAt = now + 5000; }
        else if (scenario === 'order_still_shows_sku') { state = 'order_pending_sku'; orderReadyAt = now + 96; }
        else if (!['confirm_click_no_change', 'prepared_to_sku_no_change'].includes(scenario)) state = 'order';
    } else if (state === 'order' && row.checkable) {
        if (['attendee_delayed', 'attendee_long_delay'].includes(scenario)) attendeeCheckedAt = now +
            (scenario === 'attendee_long_delay' ? 1200 : 120);
        else if (!['attendee_no_change', 'slow_attendee_no_change'].includes(scenario)) attendeeChecked = true;
    } else if (state === 'order' && (row.text === '提交订单' || row.text === '立即支付')) {
        if (scenario === 'wechat_congestion_after_submit' || scenario === 'wechat_payment_sheet_during_congestion') congestionVisible = true;
        else if (paymentSheetScenario) state = 'payment_sheet';
        else if (scenario.startsWith('wechat_cashier_') || scenario === 'wechat_preselected_price_changed') state = 'cashier';
        else if (scenario.startsWith('wechat_')) state = 'wechat';
        else if (scenario === 'congestion_after_submit') congestionVisible = true;
        else if (scenario === 'challenge_after_submit') state = 'challenge';
        else if (scenario !== 'submit_no_change') state = 'payment';
    }
    return true;
}
function node(row) {
    return {
        bounds: () => row.bounds,
        text: () => row.text,
        enabled: () => row.enabled,
        parent: () => scenario === 'sold_out_sibling' && row.text === '看台 ¥' + target.amount ? {
            enabled: () => true,
            bounds: () => ({ left: 10, top: 500, right: 700, bottom: 770 }),
            parent: () => null
        } : null,
        checked: () => row.text === '[观演人勾选框]' && (attendeeChecked || now >= attendeeCheckedAt) || row.checked,
        click: () => clickRow(row)
    };
}
function selector(kind, value) {
    return {
        packageName: function () { return this; },
        enabled: function (value) { this.requiredEnabled = value; return this; },
        matches: function () {
            selectorReads++;
            return rows().filter(row => {
                if (this.requiredEnabled !== undefined && row.enabled !== this.requiredEnabled) return false;
                const field = row[kind === 'checkable' ? 'checkable' : kind === 'className' ? 'className' :
                    kind.indexOf('text') === 0 ? 'text' : 'desc'];
                return kind.endsWith('Contains') ? field.indexOf(value) >= 0 :
                    kind.endsWith('Matches') ? value.test(field) : field === value;
            }).map(node);
        },
        exists: function () { return this.matches().length > 0; },
        find: function () {
            const results = this.matches();
            return { size: () => results.length, get: index => results[index] };
        }
    };
}
function nativeRow(row, children) {
    children = children || [];
    unrecycledNativeNodes++;
    let recycled = false;
    const result = {
        __children: children,
        __row: row,
        getPackageName: () => state === 'wechat' ? 'com.tencent.mm' : scenario === 'wrong_package' ||
            scenario === 'external_after_submit' && state === 'payment' ||
            scenario === 'package_changed_after_entry' && state !== 'detail' ? 'other.app' : target.package,
        getText: () => {
            if (scenario === 'transition_entry_label_error' && state === 'sku' && row.text === dateLabel) {
                throw new Error('Private label must not escape');
            }
            return row.text;
        },
        getContentDescription: () => row.desc || '',
        getClassName: () => { propertyReads.className++; return row.className || 'android.view.View'; },
        getBoundsInScreen: rect => {
            if (scenario === 'transition_entry_geometry_error' && state === 'sku' && row.text === dateLabel) {
                throw new Error('Private geometry must not escape');
            }
            if (congestionVisible && row.text !== '刷新') congestionNonRefreshGeometryReads++;
            if (congestionVisible && row.text && row.text !== '刷新' &&
                    !/前方拥堵|请刷新重试/.test(row.text)) congestionUnderlyingGeometryReads++;
            geometryReads[state] = (geometryReads[state] || 0) + 1;
            Object.assign(rect, row.bounds);
        },
        isEnabled: () => row.enabled !== false,
        isChecked: () => { propertyReads.checked++; return row.checkable &&
            (row.text === '[观演人勾选框]' && (attendeeChecked || now >= attendeeCheckedAt) || row.checked); },
        isCheckable: () => { propertyReads.checkable++; return !!row.checkable; },
        isSelected: () => !!row.selected,
        isClickable: () => row.clickable === true || row.clickable !== false && !!row.text,
        getParent: () => result.__parent || null,
        performAction: () => {
            const clickTarget = row.clickTarget || row;
            nativeActions.push(clickTarget.text);
            if (scenario === 'native_sku_rejected_tier' && clickTarget.text === '看台 ¥' + target.amount) return false;
            return clickRow(clickTarget);
        },
        getChildCount: () => children.length,
        getChild: index => children[index],
        recycle: () => {
            if (recycled) doubleNativeRecycles++;
            else { recycled = true; unrecycledNativeNodes--; }
        }
    };
    children.forEach(child => { child.__parent = result; });
    return result;
}
function nativeRoot() {
    treeReads++;
    if (state === 'entry_loading' && scenario === 'transition_entry_timed') now += 5;
    if (state === 'entry_loading' && scenario === 'transition_entry_slow') now += 75;
    if (state === 'detail') detailReadStartedAfterSaleMs.push(now - target.saleEpochMs);
    if (['slow_entry', 'slow_countdown', 'slow_countdown_challenge'].includes(scenario)) now += 120;
    if (state === 'detail' && now >= target.saleEpochMs) onSaleDetailReads++;
    if (scenario === 'slow_attendee_no_change' && state === 'order') now += 120;
    stateReads[state] = (stateReads[state] || 0) + 1;
    if (state === 'date_loading' && stateReads.date_loading >= 3) state = 'sku';
    if (state === 'entry_loading' && (scenario === 'transition_entry_timed' ?
            now >= entryReadyAt : stateReads.entry_loading >= 3)) state = 'sku';
    if (state === 'order_loading' && now >= orderReadyAt) state = 'order';
    if (state === 'order_pending_sku' && now >= orderReadyAt) state = 'order';
    if (scenario === 'transition_entry_challenge' && state === 'sku' && stateReads.sku >= 1) state = 'challenge';
    if (scenario === 'congestion_disappears' && refreshCount >= 1) congestionVisible = false;
    // Introduce a verification overlay specifically at the fresh pre-click read.
    if (scenario === 'challenge_before_price' && state === 'sku' && stateReads.sku === 2 ||
            scenario === 'challenge_before_confirm' && state === 'sku_selected' && stateReads.sku_selected === 2) {
        state = 'challenge';
    }
    if (scenario === 'broken_native_tree') throw new Error('Private screen text must never be printed');
    if (scenario === 'challenge_before_submit' && state === 'order' && stateReads.order >= 3) state = 'challenge';
    const records = rows();
    if (nativeOrderCase && state === 'order' && stateReads.order >= 2) {
        if (nativeOrderCase === 'challenge') records.push(item('人机验证', records.length));
        if (nativeOrderCase === 'autopay') records.push(item('免密支付', records.length));
        if (nativeOrderCase === 'wrong_price') records.forEach(row => {
            if (row.text === '¥' + target.amount) row.text = '¥' + (target.amount + 100);
        });
        if (nativeOrderCase === 'attendee_changed') records.forEach(row => {
            if (row.checkable) row.checked = false;
        });
        if (nativeOrderCase === 'disabled_submit') records.forEach(row => {
            if (row.text === '立即支付') row.enabled = false;
        });
    }
    if (scenario === 'transition_entry_changed_date' && state === 'sku' && stateReads.sku >= 1) {
        records.forEach(row => { if (row.text === dateLabel) row.text = '2026-12-06 周日 19:00'; });
    }
    if (androidSkuScenario && ['sku', 'sku_selected'].includes(state)) {
        function build(row) { return nativeRow(row, (row.children || []).map(build)); }
        return build(androidSkuTree());
    }
    if (['retained_tier_changed', 'retained_date_changed'].includes(scenario) &&
            state === 'sku_selected' && stateReads.sku_selected >= 2) {
        records.forEach(row => {
            if (row.text === (scenario === 'retained_tier_changed' ? '看台 ¥' + target.amount : dateLabel)) {
                row.selected = false;
            }
        });
    }
    if (scenario === 'attendee_changed_before_submit' && state === 'order' && stateReads.order >= 3) {
        records.forEach(row => { if (row.checkable) row.checked = false; });
    }
    const children = [];
    const dialogChildren = [];
    const paymentSheetChildren = [];
    const wechatMethodChildren = [];
    for (const row of records) {
        if (row.paymentSheet && scenario !== 'wechat_payment_sheet_unbounded') {
            if (row.wechatMethodRow) wechatMethodChildren.push(nativeRow(row));
            else paymentSheetChildren.push(nativeRow(row));
            continue;
        }
        if (congestionVisible && /前方拥堵|请刷新重试/.test(row.text)) {
            row.bounds = {left: 200, top: 720, right: 850, bottom: 790};
            if (scenario === 'congestion_description_hints') {
                row.desc = row.text;
                row.text = '';
            }
            dialogChildren.push(nativeRow(row));
            continue;
        }
        if (congestionVisible && row.text === '刷新' && scenario !== 'congestion_background_refresh_only') {
            row.bounds = {left: 500, top: 850, right: 800, bottom: 920};
            dialogChildren.push(nativeRow(row));
            continue;
        }
        if (scenario === 'sold_out_sibling' && row.text === '缺货登记') continue;
        if (scenario === 'sold_out_sibling' && row.text === '看台 ¥' + target.amount) {
            children.push(nativeRow({text: '', enabled: true,
                bounds: {left: 10, top: 500, right: 700, bottom: 770}},
                [nativeRow(row), nativeRow(records.find(record => record.text === '缺货登记'))]));
        } else if (['native_sku_double_label_card', 'native_sku_double_wrong_kind'].includes(scenario) &&
                state === 'sku' && ['看台 ¥' + target.amount, '内场' + target.amount + '元', '¥' + target.amount].includes(row.text)) {
            if (row.text !== '¥' + target.amount) {
                const amountRow = records.find(record => record.text === '¥' + target.amount);
                const bounds = {left:Math.min(row.bounds.left,amountRow.bounds.left),
                    top:Math.min(row.bounds.top,amountRow.bounds.top),
                    right:Math.max(row.bounds.right,amountRow.bounds.right),
                    bottom:Math.max(row.bounds.bottom,amountRow.bounds.bottom)};
                children.push(nativeRow({text:'',enabled:true,clickable:true,clickTarget:row,bounds},
                    [nativeRow({...row,clickable:false}),nativeRow({...amountRow,clickable:false})]));
            }
        } else if (scenario === 'native_sku_child_label' && [dateLabel, '看台 ¥' + target.amount, '确认'].includes(row.text)) {
            children.push(nativeRow({text:'', enabled:true, clickable:true, clickTarget:row, bounds:row.bounds},
                [nativeRow({...row, clickable:false})]));
        } else children.push(nativeRow(row));
    }
    if (wechatMethodChildren.length) {
        const method = records.find(row => row.text === '微信支付' && row.paymentSheet);
        paymentSheetChildren.push(nativeRow({text: '', enabled: true,
            bounds: {left: 60, top: method.bounds.top - 10, right: 1020, bottom: method.bounds.bottom + 10}},
            wechatMethodChildren));
    }
    if (paymentSheetChildren.length) {
        children.push(nativeRow({text: '', enabled: true,
            bounds: {left: 0, top: 1000, right: 1080, bottom: 2400}}, paymentSheetChildren));
    }
    if (dialogChildren.length) {
        if (scenario === 'congestion_unbounded_dialog') {
            children.push(...dialogChildren);
        } else {
            const frame = {text: '', enabled: true,
                bounds: {left: 140, top: 650, right: 920, bottom: 1000}};
            children.push(nativeRow(frame, dialogChildren));
            if (scenario === 'congestion_dialog_with_background_refresh') {
                children.push(nativeRow({text: '刷新', enabled: true,
                    bounds: {left: 10, top: 1500, right: 200, bottom: 1550}}));
            }
        }
    }
    const root = nativeRow({text: '', enabled: true,
        bounds: {left: 0, top: 0, right: 1080, bottom: 2400}}, children);
    if (nativeSkuScenario) root.findAccessibilityNodeInfosByText = label => {
        const found = [], stack = [root];
        while (stack.length) {
            const current = stack.pop();
            if (String(current.getText()).includes(label)) found.push(current);
            stack.push(...current.__children);
        }
        if (scenario === 'native_sku_duplicate_lookup' && label === '看台 ¥' + target.amount) found.push(found[0]);
        return {size: () => found.length, get: index => found[index]};
    };
    if (nativeOrderCase && state === 'order') root.findAccessibilityNodeInfosByText = label => {
        if (nativeOrderCase === 'marker_throw') throw new Error('Native query unavailable');
        const copies = records.filter(row => nativeOrderCase !== 'marker_missing' &&
            (row.text.includes(label) || String(row.desc || '').includes(label))).map(row => nativeRow({...row}));
        return {size: () => copies.length, get: index => copies[index]};
    };
    return root;
}
const context = {
    RegExp: function (pattern, flags) { regexCompilations++; return new RegExp(pattern, flags); },
    auto: { waitFor: () => {}, setMode: () => {}, service: {
        getRootInActiveWindow: nativeRoot
    } },
    android: { graphics: { Rect: function () { rectAllocations++; } }, util: { Log: { i: () => {} } },
        view: {accessibility: {AccessibilityNodeInfo: {ACTION_CLICK: 16}}} },
    toast: () => {},
    files: { write: (path, body) => diagnosticWrites.push({path, data: JSON.parse(body)}) },
    click: (x, y) => {
        const match = rows().filter(row => !androidSkuScenario || row.text).find(row => x >= row.bounds.left &&
            x <= row.bounds.right && y >= row.bounds.top &&
            y <= row.bounds.bottom);
        return match ? clickRow(match) : false;
    },
    sleep: ms => { sleeps.push(ms); now += ms; },
    Date: { now: () => now },
    device: { width: 1080, height: scenario.startsWith('fixed_reservation_') || androidSkuScenario ? 2376 : 2400 },
    currentPackage: () => scenario === 'stale_current_package' ? 'com.android.launcher' : target.package,
    text: value => selector('text', value),
    desc: value => selector('desc', value),
    textContains: value => selector('textContains', value),
    descContains: value => selector('descContains', value),
    textMatches: value => selector('textMatches', value),
    descMatches: value => selector('descMatches', value),
    className: value => selector('className', value),
    checkable: value => selector('checkable', value),
    selected: value => ({
        packageName: function () { return this; },
        find: () => {
            const results = rows().filter(row => row.selected === value).map(node);
            return { size: () => results.length, get: index => results[index] };
        }
    }),
    storages: { create: () => ({
        contains: key => Object.prototype.hasOwnProperty.call(stored, key),
        get: (key, fallback) => Object.prototype.hasOwnProperty.call(stored, key) ? stored[key] : fallback,
        put: (key, value) => { stored[key] = value; }
    }) },
    context: { getFilesDir: () => '/private' },
    java: { lang: { String: function (value) { this.getBytes = () => value; } },
        security: { MessageDigest: { getInstance: () => ({
            digest: value => { hashComputations++; return Array.from(crypto.createHash('sha256').update(value, 'utf8').digest()); }
        }) } }, io: { File: function (parent, name) {
        this.createNewFile = () => {
            const key = parent + '/' + name;
            if (lockFiles[key]) return false;
            lockFiles[key] = true;
            return true;
        };
    } } },
    console: { log: value => logs.push(String(value)) }
};
const shortPresses = [];
context.press = (x, y, duration) => {
    shortPresses.push({duration, state});
    if (scenario === 'entry_press_failed') return false;
    if (scenario === 'wechat_payment_sheet_full_flow_failed_confirm' && state === 'sku_selected') return false;
    if (scenario === 'wechat_payment_sheet_full_flow_failed_submit' && state === 'order') return false;
    if (scenario === 'wechat_payment_sheet_full_flow_failed_gateway' && state === 'payment_sheet') return false;
    return context.click(x, y);
};
if (scenario === 'wechat_payment_sheet_full_flow_no_press') delete context.press;
vm.runInNewContext(script, context, { timeout: 2000 });
process.stdout.write(JSON.stringify({ nativeActions, shortPresses, clicks, clickTimes, onSaleDetailReads, logs, stored, lockFiles, state, treeReads, selectorReads, diagnosticWrites, propertyReads, geometryReads, stateReads, detailReadStartedAfterSaleMs, sleeps, rectAllocations, congestionNonRefreshGeometryReads, congestionUnderlyingGeometryReads, hashComputations, regexCompilations, unrecycledNativeNodes, doubleNativeRecycles }));

