// Offline fake accessibility tree for the locked prototype's unit tests.
// This file is never deployed to a phone.
const fs = require('fs');
const vm = require('vm');
const crypto = require('crypto');

const script = fs.readFileSync(process.argv[2], 'utf8');
const scenario = process.argv[3];
const target = JSON.parse(script.match(/^var TARGET = (.+);$/m)[1]);
let now = target.saleEpochMs + 1000;
if (scenario === 'spaced_reservation') now = target.saleEpochMs - 30000;
let state = 'detail';
if (scenario === 'probe_order') state = 'order';
if (scenario === 'probe_sku') state = 'sku';
let attendeeChecked = false;
let attendeeCheckedAt = Infinity;
const clicks = [];
const logs = [];
const diagnosticWrites = [];
const stored = {};
const lockFiles = {};
let treeReads = 0;
let selectorReads = 0;
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
        checkable: label === '[观演人勾选框]',
        checked: !['attendee_unchecked', 'attendee_no_change', 'attendee_delayed', 'attendee_disabled', 'custom_checkable'].includes(scenario) && label === '[观演人勾选框]',
        enabled: !(scenario === 'disabled_confirm' && state === 'sku_selected' && confirmation) &&
            !(scenario === 'attendee_disabled' && label === '[观演人勾选框]') &&
            !(scenario === 'disabled_price' && label === '看台 ¥' + target.amount),
        selected: ((state === 'sku' || state === 'sku_selected') &&
            label === dateLabel && scenario !== 'sku') ||
            (scenario === 'sku_already_target' && label === '看台 ¥' + target.amount), bounds: {
        left: confirmation ? 640 + index * 10 : 20 + index * 2,
        top: confirmation ? 2070 + index * 5 :
            label.indexOf('立即') === 0 || label === '提交订单' ? 2100 : 400 + index * 70,
        right: confirmation ? 990 + index * 10 : 600 + index * 2,
        bottom: confirmation ? 2180 + index * 5 :
            label.indexOf('立即') === 0 || label === '提交订单' ? 2210 : 450 + index * 70
    }};
}
function rows() {
    if (state === 'detail') {
        const labels = [target.titleToken, target.city, '已预约', '立即购票'];
        if (scenario === 'spaced_reservation') labels[2] = '已   预\n约';
        if (scenario === 'ambiguous_entry') labels.push('立即购票');
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
        const labels = ['选择票档', dateLabel,
            '看台 ¥' + target.amount,
            scenario === 'wrong_quantity_after_price' ? '2张' : '1张', '确认'];
        if (scenario === 'ambiguous_confirm') labels.push('确认');
        return labels.map(item);
    }
    if (state === 'order') {
        const price = scenario === 'combined_order_summary' ? '看台 ￥' + target.amount + ' ×1张' :
            scenario === 'decimal_price_payment_label' ?
            '¥' + target.amount + '.00' : '¥' +
            (scenario === 'wrong_order_price' ? target.amount + 100 :
                scenario === 'substring_order_price' ? 11380 : target.amount);
        const labels = [target.titleToken, target.city,
            scenario === 'wrong_order_date' ? '2026-12-06 周日 19:00' : dateLabel,
            price, '实名观演人', scenario === 'wrong_attendee' ? '另一个人' : '测试观演人'];
        if (['instant_pay_order', 'combined_order_summary'].includes(scenario)) labels.unshift('\u786e\u8ba4\u8d2d\u7968');
        if (scenario !== 'attendee_missing') labels.push('[观演人勾选框]');
        if (scenario === 'attendee_ambiguous') labels.push('[观演人勾选框]');
        if (scenario !== 'combined_order_summary') labels.push(scenario === 'two_ticket_order' ? '2张' :
            scenario === 'eleven_ticket_order' ? '11张' : '1张');
        labels.push('取票方式',
            ['instant_pay_order', 'combined_order_summary'].includes(scenario) ? '立即支付' : '提交订单');
        if (scenario === 'decimal_price_payment_label') labels.push('支付方式');
        return labels.map(item);
    }
    if (state === 'payment') return [item('支付方式', 0)];
    if (state === 'challenge') return [item('人机验证', 0)];
    return [item('加载中', 0)];
}
function clickRow(row) {
    clicks.push(row.text);
    if (state === 'detail' && row.text === '立即购票') {
        state = ['sku_already_chosen', 'sku_already_target'].includes(scenario) ? 'sku_selected' :
            ['sku', 'sku_preselected', 'sold_out_sku', 'price_click_no_change',
             'duplicate_price_sku', 'wrong_quantity_after_price',
             'ambiguous_confirm', 'confirm_click_no_change',
             'disabled_confirm', 'price_auto_order', 'sold_out_sibling', 'disabled_price'].includes(scenario) ? 'sku' :
            scenario === 'unknown_after_entry' ? 'unknown' : 'order';
        if (scenario === 'challenge_after_entry') state = 'challenge';
    } else if (state === 'sku' && row.text === '看台 ¥' + target.amount) {
        if (scenario === 'price_auto_order') state = 'order';
        else if (scenario !== 'price_click_no_change') state = 'sku_selected';
    } else if (state === 'sku_selected' && row.text === '确认') {
        if (scenario !== 'confirm_click_no_change') state = 'order';
    } else if (state === 'order' && row.checkable) {
        if (scenario === 'attendee_delayed') attendeeCheckedAt = now + 120;
        else if (scenario !== 'attendee_no_change') attendeeChecked = true;
    } else if (state === 'order' && (row.text === '提交订单' || row.text === '立即支付')) {
        state = 'payment';
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
        getPackageName: () => scenario === 'wrong_package' ||
            scenario === 'package_changed_after_entry' && state !== 'detail' ? 'other.app' : target.package,
        getText: () => row.text,
        getContentDescription: () => row.desc || '',
        getClassName: () => row.className || 'android.view.View',
        getBoundsInScreen: rect => Object.assign(rect, row.bounds),
        isEnabled: () => row.enabled !== false,
        isChecked: () => row.checkable && (attendeeChecked || now >= attendeeCheckedAt || row.checked),
        isCheckable: () => !!row.checkable,
        isSelected: () => !!row.selected,
        getChildCount: () => children.length,
        getChild: index => children[index],
        recycle: () => {}
    };
}
function nativeRoot() {
    treeReads++;
    if (scenario === 'broken_native_tree') throw new Error('Private screen text must never be printed');
    const records = rows();
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
    auto: { waitFor: () => {}, setMode: () => {}, service: {
        getRootInActiveWindow: nativeRoot
    } },
    android: { graphics: { Rect: function () {} }, util: { Log: { i: () => {} } } },
    toast: () => {},
    files: { write: (path, body) => diagnosticWrites.push({path, data: JSON.parse(body)}) },
    click: (x, y) => {
        const match = rows().find(row => x >= row.bounds.left &&
            x <= row.bounds.right && y >= row.bounds.top &&
            y <= row.bounds.bottom);
        return match ? clickRow(match) : false;
    },
    sleep: ms => { now += ms; },
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
        put: (key, value) => { stored[key] = value; }
    }) },
    context: { getFilesDir: () => '/private' },
    java: { lang: { String: function (value) { this.getBytes = () => value; } },
        security: { MessageDigest: { getInstance: () => ({
            digest: value => Array.from(crypto.createHash('sha256').update(value, 'utf8').digest())
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
vm.runInNewContext(script, context, { timeout: 2000 });
process.stdout.write(JSON.stringify({ clicks, logs, stored, lockFiles, state, treeReads, selectorReads, diagnosticWrites }));
