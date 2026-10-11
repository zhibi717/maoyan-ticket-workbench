// Offline fake accessibility tree for the locked prototype's unit tests.
// This file is never deployed to a phone.
const fs = require('fs');
const vm = require('vm');
const crypto = require('crypto');

const script = fs.readFileSync(process.argv[2], 'utf8');
const scenario = process.argv[3];
const target = JSON.parse(script.match(/^var TARGET = (.+);$/m)[1]);
const actualAmount = scenario.startsWith('wechat_preselected_price') || scenario.startsWith('phone_') ? 580 : target.amount;
const chosenAmount = scenario.startsWith('phone_') ? 580 : target.amount;
let now = target.saleEpochMs + 1000;
if (['spaced_reservation', 'slow_countdown', 'slow_countdown_challenge'].includes(scenario)) now = target.saleEpochMs - 30000;
if (scenario === 'sale_during_sleep') now = target.saleEpochMs - 20060;
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
let rectAllocations = 0;
let congestionVisible = scenario.startsWith('probe_congestion');
let refreshCount = 0;
let congestionNonRefreshGeometryReads = 0;
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
const propertyReads = { className: 0, checkable: 0, checked: 0 };
const geometryReads = {};
const sleeps = [];
if (scenario === 'already_claimed') {
    lockFiles['/private/qiangpiao-maoyan-' + target.attemptKey.replace(/:/g, '-') + '.lock'] = true;
}
const dateToken = ['decimal_price_payment_label', 'sku', 'sold_out_sku'].includes(scenario) ? target.dateLabels[0] :
    target.dateLabels.find(label => label.indexOf('月') >= 0);
const dateLabel = dateToken + ' 周六 19:00';

function item(label, index) {
    const confirmation = label === '确认';
    return { text: label, desc: '',
        className: label === '[观演人勾选框]' ?
            scenario === 'custom_checkable' ? 'android.view.View' : 'android.widget.CheckBox' : 'android.widget.TextView',
        checkable: label === '[观演人勾选框]' || state === 'cashier' && label === '微信支付',
        checked: !['attendee_unchecked', 'attendee_no_change', 'slow_attendee_no_change', 'attendee_delayed', 'attendee_long_delay', 'attendee_disabled', 'custom_checkable'].includes(scenario) && label === '[观演人勾选框]',
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
            label === dateLabel && scenario !== 'sku' &&
            (scenario !== 'date_transition' || dateClicked)) ||
            (state === 'sku_selected' && selectedSkuAt !== Infinity && scenario !== 'price_wrong_selection' &&
                label === '看台 ¥' + target.amount) ||
            (['sku_already_target', 'retained_tier_changed', 'retained_date_changed',
              'prepared_to_sku', 'prepared_to_sku_no_change'].includes(scenario) &&
                label === '看台 ¥' + target.amount), bounds: {
        left: confirmation ? 640 + index * 10 : 20 + index * 2,
        top: confirmation ? 2070 + index * 5 :
            label.indexOf('立即') === 0 || label === '提交订单' ? 2100 : 400 + index * 70,
        right: confirmation ? 990 + index * 10 : 600 + index * 2,
        bottom: confirmation ? 2180 + index * 5 :
            label.indexOf('立即') === 0 || label === '提交订单' ? 2210 : 450 + index * 70
    }};
}
function rows() {
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
        const labels = [target.titleToken, target.city, '已预约', '立即购票'];
        if (scenario === 'slow_entry' && stateReads.detail < 5) labels.pop();
        if (scenario === 'fast_entry' && now < target.saleEpochMs + 21030) labels.pop();
        if (state === 'detail' && ['challenge_before_entry', 'wrong_detail_before_entry'].includes(scenario) && stateReads.detail >= 2) {
            if (scenario === 'challenge_before_entry') labels.push('人机验证');
            else labels[1] = '其他城市';
        }
        if (scenario === 'spaced_reservation') labels[2] = '已   预\n约';
        if (scenario === 'slow_countdown_challenge' && onSaleDetailReads >= 2) labels.push('人机验证');
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
        if (scenario === 'sold_out_sibling') labels.push('缺货登记');
        return labels.map(item);
    }
    if (state === 'sku_selected') {
        if (scenario === 'tier_long_delay' && now < selectedSkuAt + 1200) {
            return ['选择票档', dateLabel, '看台 ¥' + target.amount, '请选择票档'].map(item);
        }
        const labels = ['选择票档', dateLabel,
            '看台 ¥' + target.amount,
            scenario === 'wrong_quantity_after_price' ? '2张' : '1张', '确认'];
        if (scenario === 'quantity_long_delay' && now < selectedSkuAt + 1200) labels.splice(3, 1);
        if (scenario === 'ambiguous_confirm') labels.push('确认');
        return labels.map(item);
    }
    if (state === 'order') {
        const price = scenario === 'combined_order_summary' ? '看台 ￥' + target.amount + ' ×1张' :
            scenario === 'decimal_price_payment_label' ?
            '¥' + target.amount + '.00' : '¥' +
            (scenario === 'wrong_order_price' ? target.amount + 100 :
                scenario === 'substring_order_price' ? 11380 : actualAmount);
        const labels = [target.titleToken, target.city,
            scenario === 'wrong_order_date' ? '2026-12-06 周日 19:00' : dateLabel,
            price, '实名观演人', scenario === 'wrong_attendee' ? '另一个人' : '测试观演人'];
        if (['instant_pay_order', 'combined_order_summary', 'wechat_immediate_review'].includes(scenario)) labels.unshift('\u786e\u8ba4\u8d2d\u7968');
        if (scenario !== 'attendee_missing') labels.push('[观演人勾选框]');
        if (scenario === 'attendee_ambiguous') labels.push('[观演人勾选框]');
        if (scenario !== 'combined_order_summary') labels.push(scenario === 'two_ticket_order' ? '2张' :
            scenario === 'eleven_ticket_order' ? '11张' : '1张');
        labels.push('取票方式',
            ['instant_pay_order', 'combined_order_summary', 'wechat_immediate_review'].includes(scenario) ? '立即支付' : '提交订单');
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
        return labels.map(item);
    }
    if (state === 'payment') return (scenario === 'pending_after_submit' ?
        ['待付款', '去支付'] : ['收银台', '支付方式', '微信支付', '确认付款']).map(item);
    if (state === 'cashier') {
        const labels = ['收银台', '￥' + (scenario === 'wechat_cashier_wrong_amount' || scenario === 'wechat_preselected_price_changed' ? actualAmount + 10 : actualAmount), '微信支付',
            scenario === 'wechat_cashier_final_only' ? '确认付款' :
                scenario === 'wechat_cashier_explicit' ? '前往微信支付' : '立即支付'];
        if (scenario === 'wechat_cashier_autopay') labels.push('免密支付');
        return labels.map(item).map(row => {
            if (row.text === '微信支付') row.checked = scenario !== 'wechat_cashier_unselected' &&
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
    if (state === 'cashier' && ['立即支付', '前往微信支付'].includes(row.text)) {
        if (scenario !== 'wechat_cashier_timeout') state = 'wechat';
        return true;
    }
    if (row.text === '刷新' && congestionVisible) {
        refreshCount++;
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
        state = ['sku_already_chosen', 'sku_already_target', 'retained_tier_changed', 'retained_date_changed'].includes(scenario) ? 'sku_selected' :
            ['sku', 'sku_preselected', 'sold_out_sku', 'price_click_no_change',
             'duplicate_price_sku', 'wrong_quantity_after_price',
             'ambiguous_confirm', 'confirm_click_no_change',
             'disabled_confirm', 'price_auto_order', 'sold_out_sibling', 'disabled_price',
             'challenge_before_price', 'challenge_before_confirm', 'date_transition', 'tier_long_delay',
             'quantity_long_delay', 'confirm_long_delay', 'order_long_delay'].includes(scenario) ? 'sku' :
            scenario === 'unknown_after_entry' ? 'unknown' : 'order';
        if (scenario === 'challenge_after_entry') state = 'challenge';
        if (scenario.startsWith('phone_') || scenario === 'price_wrong_selection') state = 'sku';
        if (scenario.startsWith('congestion_') && scenario !== 'congestion_after_submit') congestionVisible = true;
    } else if (state === 'sku' && row.text === dateLabel && scenario === 'date_transition') {
        dateClicked = true;
        state = 'date_loading';
    } else if (state === 'sku' && row.text === '看台 ¥' + target.amount) {
        selectedSkuAt = now;
        if (scenario === 'price_auto_order') state = 'order';
        else if (scenario !== 'price_click_no_change') state = 'sku_selected';
    } else if (state === 'sku_selected' && row.text === '确认') {
        if (scenario === 'order_long_delay') { state = 'order_loading'; orderReadyAt = now + 5000; }
        else if (!['confirm_click_no_change', 'prepared_to_sku_no_change'].includes(scenario)) state = 'order';
    } else if (state === 'order' && row.checkable) {
        if (['attendee_delayed', 'attendee_long_delay'].includes(scenario)) attendeeCheckedAt = now +
            (scenario === 'attendee_long_delay' ? 1200 : 120);
        else if (!['attendee_no_change', 'slow_attendee_no_change'].includes(scenario)) attendeeChecked = true;
    } else if (state === 'order' && (row.text === '提交订单' || row.text === '立即支付')) {
        if (scenario === 'wechat_congestion_after_submit') congestionVisible = true;
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
        checked: () => attendeeChecked || now >= attendeeCheckedAt || row.checked,
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
    return {
        getPackageName: () => state === 'wechat' ? 'com.tencent.mm' : scenario === 'wrong_package' ||
            scenario === 'external_after_submit' && state === 'payment' ||
            scenario === 'package_changed_after_entry' && state !== 'detail' ? 'other.app' : target.package,
        getText: () => row.text,
        getContentDescription: () => row.desc || '',
        getClassName: () => { propertyReads.className++; return row.className || 'android.view.View'; },
        getBoundsInScreen: rect => {
            if (congestionVisible && row.text !== '刷新') congestionNonRefreshGeometryReads++;
            geometryReads[state] = (geometryReads[state] || 0) + 1;
            Object.assign(rect, row.bounds);
        },
        isEnabled: () => row.enabled !== false,
        isChecked: () => { propertyReads.checked++; return row.checkable && (attendeeChecked || now >= attendeeCheckedAt || row.checked); },
        isCheckable: () => { propertyReads.checkable++; return !!row.checkable; },
        isSelected: () => !!row.selected,
        getChildCount: () => children.length,
        getChild: index => children[index],
        recycle: () => {}
    };
}
function nativeRoot() {
    treeReads++;
    if (['slow_entry', 'slow_countdown', 'slow_countdown_challenge'].includes(scenario)) now += 120;
    if (state === 'detail' && now >= target.saleEpochMs) onSaleDetailReads++;
    if (scenario === 'slow_attendee_no_change' && state === 'order') now += 120;
    stateReads[state] = (stateReads[state] || 0) + 1;
    if (state === 'date_loading' && stateReads.date_loading >= 3) state = 'sku';
    if (state === 'order_loading' && now >= orderReadyAt) state = 'order';
    if (scenario === 'congestion_disappears' && refreshCount >= 1) congestionVisible = false;
    // Introduce a verification overlay specifically at the fresh pre-click read.
    if (scenario === 'challenge_before_price' && state === 'sku' && stateReads.sku === 2 ||
            scenario === 'challenge_before_confirm' && state === 'sku_selected' && stateReads.sku_selected === 2) {
        state = 'challenge';
    }
    if (scenario === 'broken_native_tree') throw new Error('Private screen text must never be printed');
    if (scenario === 'challenge_before_submit' && state === 'order' && stateReads.order >= 3) state = 'challenge';
    const records = rows();
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
    for (const row of records) {
        if (scenario === 'sold_out_sibling' && row.text === '缺货登记') continue;
        if (scenario === 'sold_out_sibling' && row.text === '看台 ¥' + target.amount) {
            children.push(nativeRow({text: '', enabled: true,
                bounds: {left: 10, top: 500, right: 700, bottom: 770}},
                [nativeRow(row), nativeRow(records.find(record => record.text === '缺货登记'))]));
        } else children.push(nativeRow(row));
    }
    return nativeRow({text: '', enabled: true,
        bounds: {left: 0, top: 0, right: 1080, bottom: 2400}}, children);
}
const context = {
    RegExp: function (pattern, flags) { regexCompilations++; return new RegExp(pattern, flags); },
    auto: { waitFor: () => {}, setMode: () => {}, service: {
        getRootInActiveWindow: nativeRoot
    } },
    android: { graphics: { Rect: function () { rectAllocations++; } }, util: { Log: { i: () => {} } } },
    toast: () => {},
    files: { write: (path, body) => diagnosticWrites.push({path, data: JSON.parse(body)}) },
    click: (x, y) => {
        const match = rows().find(row => x >= row.bounds.left &&
            x <= row.bounds.right && y >= row.bounds.top &&
            y <= row.bounds.bottom);
        return match ? clickRow(match) : false;
    },
    sleep: ms => { sleeps.push(ms); now += ms; },
    Date: { now: () => now },
    device: { width: 1080, height: 2400 },
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
    return context.click(x, y);
};
vm.runInNewContext(script, context, { timeout: 2000 });
process.stdout.write(JSON.stringify({ shortPresses, clicks, clickTimes, onSaleDetailReads, logs, stored, lockFiles, state, treeReads, selectorReads, diagnosticWrites, propertyReads, geometryReads, stateReads, sleeps, rectAllocations, congestionNonRefreshGeometryReads, hashComputations, regexCompilations }));

