// EXPERIMENTAL AutoX v7 Rhino prototype. Do not enable until the read-only
// phone probe and real Maoyan sale/selection/order layouts have been checked.
// The default flow uses guarded nodes. An explicit separate build can enable
// a one-shot fixed-coordinate start, with order verification before submission.
var TARGET = null;
var ENABLE_EXPERIMENTAL_LIVE = false;
var PRECHECK_ONLY = false;
var SELECTION_REHEARSAL = false;
var CONFIRM_SELECTION_REHEARSAL = false;
var FLOW_PRECHECK_ONLY = false;
var SUBMIT_TO_PAYMENT = false;
var CONGESTION_PRECHECK_ONLY = false;
var OPEN_WECHAT_PAYMENT = false;
var STOP_AT_MERCHANT_PAYMENT = false;
var USER_PRESELECTED = false;
var CAPTURE_SELECTION_ONLY = false;
var FAST_FIXED_PATH = false;
var FAST_COORDS = null;

(function () {
    if (!ENABLE_EXPERIMENTAL_LIVE) {
        console.log("猫眼手机端原型已锁定；须先验证真机页面，当前不会操作。");
        return;
    }
    if (!TARGET || TARGET.package !== "com.sankuai.movie" ||
            TARGET.attendeeCount !== 1 ||
            !TARGET.attemptKey || !TARGET.saleEpochMs ||
            !/^[0-9a-f]{64}$/.test(TARGET.attendeeNameSha256 || "")) {
        console.log("猫眼手机端原型资料不完整，已停止。");
        return;
    }

    var PACKAGE = TARGET.package;
    var ENTRY = ["立即预订", "立即预定", "立即购票", "立即抢购", "立即抢票", "特惠购票", "立即购买"];
    var ENTRY_PATTERN = /^(?:立即预订|立即预定|立即购票|立即抢购|立即抢票|特惠购票|立即购买)$/;
    var CHALLENGE = ["图片验证", "图片识别", "图形验证", "安全验证", "人机验证", "请完成验证", "滑块验证", "点击图片", "验证码"];
    // An order review can itself list available payment methods. Do not
    // classify that label alone as the payment screen.
    var PAYMENT = ["收银台", "确认付款", "输入支付密码"];
    var SUBMIT = ["提交订单", "立即提交"];
    var ORDER_TITLE = ["确认购票", "确认购买"];
    var ORDER_ACTION = ["提交订单", "立即提交", "立即支付"];
    var SKU_CONFIRM = ["确认", "确定"];
    var ENTRY_TRANSITION_POLL_MS = 20;
    var SELECTION_READY_POLL_MS = 20;
    var storage;
    var phase = "启动";
    var marks = [];
    var timeOrigin = TARGET.saleEpochMs;
    var screen = null;
    var screenReads = 0;
    var screenReadMs = 0;
    var screenReadMaxMs = 0;
    var screenRootReadMs = 0;
    var screenTreeCopyMs = 0;
    var readOrderFields = false;
    var readSelectionFields = false;
    var nativeSkuClickSupported = false;
    var patterns = {};
    var lastScreenReadMs = 0;
    var lastScreenReadStartedEpochMs = null;
    var screenReadMode = null;
    var labelReads = 0;
    var preparedProbe = null;
    var terminalState = "not_submitted";
    var retainedDateSelection = false;
    var retainedSingleDate = false;
    var entryFoundEpochMs = null;
    var entryClickEpochMs = null;
    var entryClickCallMs = null;
    var entryTouchCount = 0;
    var lastEntryTouchEpochMs = null;
    var entryDecisionReadMs = null;
    var entryNextPageEpochMs = null;
    var congestionRefreshes = 0;
    var lastCongestionRefreshMs = null;
    var congestionProbe = null;
    var datePattern = null;
    var rawAmount = String(TARGET.amount);
    var amountTokens = [rawAmount, rawAmount.replace(/\B(?=(\d{3})+(?!\d))/g, ",")];
    var nameDigests = {};
    var digestCacheCount = 0;
    var screenPackage = PACKAGE;
    var wechatLaunchAttempted = false;
    var submittedAmount = null;
    var touchTimings = [];
    var readTimings = [];
    var transitionTimings = [];
    var skuDecision = null;
    function beginTransition(name, sourceStage, nextActions) {
        var touch = null;
        for (var i = 0; i < touchTimings.length; i++) {
            if (touchTimings[i].stage === sourceStage) { touch = touchTimings[i]; break; }
        }
        var timing = {
            stage: name, sourceStage: sourceStage,
            actionStartedAfterOriginMs: touch ? touch.startedAfterOriginMs : Date.now() - timeOrigin,
            actionCallMs: touch ? touch.callMs : null,
            lastUnavailableObservedAfterOriginMs: null,
            firstReadyReadStartedAfterOriginMs: null,
            firstReadyObservedAfterOriginMs: null, readyState: null,
            nextActionStartedAfterOriginMs: null, readyToNextActionMs: null,
            finishedAfterOriginMs: null, completed: false,
            readCount: 0, readWallMs: 0,
            readBaseCount: screenReads, readBaseMs: screenReadMs,
            lastSampleReadNumber: null, nextActions: nextActions || []
        };
        if (transitionTimings.length < 16) transitionTimings.push(timing);
        return timing;
    }
    function observeTransition(timing, readyState) {
        if (!timing) return;
        timing.readCount = screenReads - timing.readBaseCount;
        timing.readWallMs = screenReadMs - timing.readBaseMs;
        if (readyState && timing.firstReadyObservedAfterOriginMs === null) {
            // These are accessibility observations, not render, server or
            // network timestamps. They include the native read and checks.
            timing.firstReadyReadStartedAfterOriginMs = lastScreenReadStartedEpochMs - timeOrigin;
            timing.firstReadyObservedAfterOriginMs = Date.now() - timeOrigin;
            timing.readyState = readyState;
        } else if (!readyState && timing.lastSampleReadNumber !== screenReads) {
            timing.lastUnavailableObservedAfterOriginMs = Date.now() - timeOrigin;
        }
        timing.lastSampleReadNumber = screenReads;
    }
    function completeTransition(timing) {
        if (!timing) return;
        timing.readCount = screenReads - timing.readBaseCount;
        timing.readWallMs = screenReadMs - timing.readBaseMs;
        timing.finishedAfterOriginMs = Date.now() - timeOrigin;
        timing.completed = true;
    }
    function linkReadyToAction(stage, started) {
        for (var i = 0; i < transitionTimings.length; i++) {
            var timing = transitionTimings[i];
            if (timing.firstReadyObservedAfterOriginMs !== null &&
                    timing.nextActionStartedAfterOriginMs === null && timing.nextActions.indexOf(stage) >= 0) {
                timing.nextActionStartedAfterOriginMs = started - timeOrigin;
                timing.readyToNextActionMs = timing.nextActionStartedAfterOriginMs - timing.firstReadyObservedAfterOriginMs;
            }
        }
    }
    function transitionReport() {
        return transitionTimings.map(function (timing) {
            return {
                stage: timing.stage, sourceStage: timing.sourceStage,
                actionStartedAfterOriginMs: timing.actionStartedAfterOriginMs,
                actionCallMs: timing.actionCallMs,
                lastUnavailableObservedAfterOriginMs: timing.lastUnavailableObservedAfterOriginMs,
                firstReadyReadStartedAfterOriginMs: timing.firstReadyReadStartedAfterOriginMs,
                firstReadyObservedAfterOriginMs: timing.firstReadyObservedAfterOriginMs,
                readyState: timing.readyState,
                nextActionStartedAfterOriginMs: timing.nextActionStartedAfterOriginMs,
                readyToNextActionMs: timing.readyToNextActionMs,
                finishedAfterOriginMs: timing.finishedAfterOriginMs, completed: timing.completed,
                readCount: timing.readCount, readWallMs: timing.readWallMs
            };
        });
    }
    function mark(name) {
        phase = name;
        marks.push(name + "=" + (Date.now() - timeOrigin) + "ms");
    }
    function report(message) {
        // Only fixed stage names and relative timings are emitted. No account,
        // attendee, or raw screen text is included in the diagnostic log.
        var line = message + "；阶段=" + phase + "；轨迹=" + marks.join(",") +
            "；页面读取=" + screenReads + "次/" + screenReadMs +
            "ms；最长读取=" + screenReadMaxMs + "ms；轻量读取=" + labelReads + "次";
        console.log(line);
        try { android.util.Log.i("QiangpiaoAutoX", line); } catch (ignored) {}
        if (FLOW_PRECHECK_ONLY) {
            try {
                files.write("/sdcard/Download/qiangpiao-readonly-result.json", JSON.stringify({
                    finishedEpochMs: Date.now(), readOnly: true,
                    elapsedMs: Date.now() - timeOrigin,
                    screenReads: screenReads, screenReadMs: screenReadMs,
                    screenReadMaxMs: screenReadMaxMs,
                    screenRootReadMs: screenRootReadMs, screenTreeCopyMs: screenTreeCopyMs,
                    readTimings: readTimings,
                    transitionTimings: transitionReport(),
                    congestionProbe: congestionProbe,
                    preparedProbe: preparedProbe,
                    message: line
                }));
            } catch (ignoredFile) {}
        }
        if (SELECTION_REHEARSAL && CONFIRM_SELECTION_REHEARSAL) {
            try {
                files.write("/sdcard/Download/qiangpiao-selection-confirm-result.json", JSON.stringify({
                    startedEpochMs: timeOrigin, finishedEpochMs: Date.now(), state: terminalState,
                    amount: TARGET.amount, marks: marks, touchTimings: touchTimings,
                    screenReads: screenReads, screenReadMs: screenReadMs,
                    readTimings: readTimings,
                    transitionTimings: transitionReport(),
                    entryClickToNextPageObservedMs: entryClickEpochMs === null || entryNextPageEpochMs === null ? null :
                        entryNextPageEpochMs - entryClickEpochMs,
                    skuDecision: skuDecision, message: line, automaticPayment: false
                }));
            } catch (ignoredRehearsalResult) {}
        }
        if (SUBMIT_TO_PAYMENT && !FLOW_PRECHECK_ONLY && !SELECTION_REHEARSAL) {
            try {
                files.write("/sdcard/Download/qiangpiao-payment-result.json", JSON.stringify({
                    finishedEpochMs: Date.now(), state: terminalState,
                    screenReads: screenReads, screenReadMs: screenReadMs,
                    screenRootReadMs: screenRootReadMs, screenTreeCopyMs: screenTreeCopyMs,
                    readTimings: readTimings,
                    transitionTimings: transitionReport(),
                    entryFoundAfterSaleMs: entryFoundEpochMs === null ? null : entryFoundEpochMs - TARGET.saleEpochMs,
                    entryClickAfterSaleMs: entryClickEpochMs === null ? null : entryClickEpochMs - TARGET.saleEpochMs,
                    entryDiscoveryToClickMs: entryFoundEpochMs === null || entryClickEpochMs === null ? null :
                        entryClickEpochMs - entryFoundEpochMs,
                    entryClickCallMs: entryClickCallMs,
                    entryTouchCount: entryTouchCount,
                    entryDecisionReadMs: entryDecisionReadMs,
                    fastFixedPath: FAST_FIXED_PATH,
                    configuredGapMs: FAST_FIXED_PATH && FAST_COORDS ? FAST_COORDS.gapMs : null,
                    entryClickToNextPageObservedMs: entryClickEpochMs === null || entryNextPageEpochMs === null ? null :
                        entryNextPageEpochMs - entryClickEpochMs,
                    congestionRefreshes: congestionRefreshes,
                    wechatLaunchAttempted: wechatLaunchAttempted,
                    touchTimings: touchTimings,
                    skuDecision: skuDecision,
                    marks: marks,
                    message: line, automaticPayment: false
                }));
            } catch (ignoredResult) {}
        }
    }

    function stop(message) { throw { safe: true, message: message }; }
    function visibleLabel(value) {
        // IEM order titles insert U+200B between Latin letters. Compare the
        // visible text, while retaining the original label for native lookup.
        return String(value || "").replace(/[\u200B\uFEFF]/g, "");
    }
    function nativeSkuClickOnce(node, stage) {
        if (["目标日期", "目标票档", "票档确认"].indexOf(stage) < 0 || !nativeSkuClickSupported) return null;
        var started = Date.now(), root = auto.service.getRootInActiveWindow();
        if (root === null) stop(stage + "原生窗口不可用，已停止。");
        var owned = [root], candidate = null, target = null;
        try {
            if (String(root.getPackageName() || "") !== PACKAGE) stop("点击前猫眼已不在前台，已停止。");
            var found = root.findAccessibilityNodeInfosByText(node.rawLabel || node.label), exact = [];
            for (var i = 0; i < found.size(); i++) {
                var item = found.get(i);
                owned.push(item);
                if (visibleLabel(item.getText()) === node.label && item.isEnabled()) exact.push(item);
            }
            if (exact.length !== 1) stop(stage + "原生候选不唯一，已停止。");
            candidate = exact[0];
            var expected = node.bounds(), current = new android.graphics.Rect();
            candidate.getBoundsInScreen(current);
            if (Math.abs(current.left - expected.left) > 4 || Math.abs(current.top - expected.top) > 4 ||
                    Math.abs(current.right - expected.right) > 4 || Math.abs(current.bottom - expected.bottom) > 4) {
                stop(stage + "控件位置正在变化，已停止。");
            }
            for (var depth = 0; candidate !== null && depth < 6; depth++) {
                if (candidate.isClickable() && candidate.isEnabled()) { target = candidate; break; }
                candidate = candidate.getParent();
                if (candidate !== null) owned.push(candidate);
            }
            if (target === null) stop(stage + "没有可点击父控件，已停止。");
            target.getBoundsInScreen(current);
            if (current.left < 0 || current.top < 0 || current.right > device.width || current.bottom > device.height ||
                    current.bottom - current.top > device.height * 0.3 ||
                    current.left > expected.left || current.top > expected.top ||
                    current.right < expected.right || current.bottom < expected.bottom) {
                stop(stage + "父控件范围不明确，已停止。");
            }
            // One native action. A rejected/uncertain action is never followed
            // by a coordinate touch that could toggle the selection again.
            var clicked = target.performAction(android.view.accessibility.AccessibilityNodeInfo.ACTION_CLICK);
            touchTimings.push({stage:stage, x:Math.floor((current.left+current.right)/2),
                y:Math.floor((current.top+current.bottom)/2), startedAfterOriginMs:started-timeOrigin,
                callMs:Date.now()-started, pressDurationMs:null, method:"accessibility_action"});
            return clicked;
        } finally {
            for (var j = owned.length - 1; j >= 0; j--) owned[j].recycle();
        }
    }
    function shortTouchOnce(x, y, stage) {
        // The same short-touch API used by the verified local entry fixture.
        // Every caller must validate its live control first. A failed dispatch
        // is never followed by click() or another press, to avoid duplicates.
        var started = Date.now();
        var duration = typeof press === "function" ? stage === "购票入口" ? 1 : 20 : null;
        var result = duration !== null ? press(x, y, duration) : click(x, y);
        if (touchTimings.length < 16) touchTimings.push({
            stage: stage, x: x, y: y, startedAfterOriginMs: started - timeOrigin,
            callMs: Date.now() - started, pressDurationMs: duration
        });
        return result;
    }
    // Shared methods avoid five new Rhino function objects per copied node.
    function rowBounds() { return this.area; }
    function rowText() { return this.label; }
    function rowEnabled() { return this.isEnabled; }
    function rowChecked() { return this.isChecked; }
    function rowParent() { return this.ancestor; }
    function readScreen(labelsOnly, allowWechat) {
        // One fresh Android tree per decision. AutoX normal-mode selectors
        // fetch the tree again for every find/exists; dozens of absent-label
        // queries took 12.7 seconds on the actual detail screen.
        var started = Date.now();
        var root = auto.service.getRootInActiveWindow();
        var activePackage = root === null ? "" : String(root.getPackageName() || "");
        var rootReadMs = Date.now() - started;
        if (root == null || activePackage !== PACKAGE &&
                !(allowWechat && OPEN_WECHAT_PAYMENT && activePackage === "com.tencent.mm")) {
            screen = null;
            if (root != null) root.recycle();
            stop("猫眼已不在前台，已停止。");
        }
        screenPackage = activePackage;
        nativeSkuClickSupported = typeof root.findAccessibilityNodeInfosByText === "function";
        // While the entry is loading, copy labels and entry bounds only. If
        // this same root already contains the target SKU, complete its fields
        // before releasing the nodes instead of fetching the entire tree again.
        // Every actual date/tier touch still rechecks a fresh window.
        var promoteSku = labelsOnly === "entry-transition";
        if (promoteSku) labelsOnly = "entry";
        var probeOrder = labelsOnly === "order-transition";
        if (probeOrder) labelsOnly = true;
        var nodes = [], stack = [{ raw: root, parent: null }], deferred = [];
        var nativeProbeMs = 0;
        // Copy coordinates into each row, but reuse the Java output argument.
        // This avoids a native Rect allocation for every node in the tree.
        var geometryRect = null;
        try {
            if (probeOrder && nativeSkuClickSupported) {
                // A native marker search can warm/find the new order without
                // first copying its entire text tree. It is only a hint: all
                // labels and order facts below still come from this live root.
                var probeStarted = Date.now(), probeOwned = [], orderMarker = false;
                try {
                    var probeMatches = root.findAccessibilityNodeInfosByText("实名观演人");
                    if (probeMatches !== null) {
                        for (var p = 0; p < probeMatches.size(); p++) {
                            if (p >= 1000 || Date.now() - started > 2000) stop("当前页面读取过慢或控件过多，已停止。");
                            var probeNode = probeMatches.get(p);
                            if (probeNode === null) continue;
                            if (probeNode !== root && probeOwned.indexOf(probeNode) < 0) probeOwned.push(probeNode);
                            if (String(probeNode.getPackageName() || "") === activePackage &&
                                    (visibleLabel(probeNode.getText()) === "实名观演人" ||
                                     visibleLabel(probeNode.getContentDescription()) === "实名观演人")) orderMarker = true;
                        }
                    }
                    if (orderMarker) labelsOnly = "order";
                } catch (probeError) {
                    if (probeError && probeError.safe) throw probeError;
                    // Unsupported/failed queries fall back to a normal text
                    // traversal, including all challenge and overlay labels.
                    labelsOnly = true;
                } finally {
                    while (probeOwned.length) probeOwned.pop().recycle();
                    nativeProbeMs = Date.now() - probeStarted;
                }
            }
            var compactOrder = labelsOnly === "order";
            while (stack.length) {
                if (nodes.length >= 1000 || deferred.length >= 1000 || Date.now() - started > 2000) {
                    stop("当前页面读取过慢或控件过多，已停止。");
                }
                var current = stack.pop(), raw = current.raw, retainedRaw = false;
                try {
                    var rawLabel = String(raw.getText() || "");
                    var label = visibleLabel(rawLabel);
                    var description = visibleLabel(raw.getContentDescription());
                    var orderFields = compactOrder || !labelsOnly && readOrderFields;
                    var selectionFields = !labelsOnly && readSelectionFields;
                    var classValue = orderFields ? String(raw.getClassName() || "") : "";
                    var isCheckable = (orderFields || selectionFields) && raw.isCheckable();
                    var checkbox = isCheckable || classValue === "android.widget.CheckBox";
                    var orderAction = ORDER_ACTION.indexOf(label) >= 0 || ORDER_ACTION.indexOf(description) >= 0;
                    var needsGeometry = compactOrder ? !!(label || description || checkbox) :
                        !labelsOnly || labelsOnly === "entry" &&
                        (ENTRY_PATTERN.test(label) || ENTRY_PATTERN.test(description)) ||
                        labelsOnly === "refresh" && (label === "刷新" || description === "刷新" ||
                            !label && !description || /前方拥堵|请刷新重试/.test(label + description));
                    if (labelsOnly === "prepared") needsGeometry = ENTRY_PATTERN.test(label) ||
                        ENTRY_PATTERN.test(description) || SKU_CONFIRM.indexOf(label) >= 0 ||
                        SKU_CONFIRM.indexOf(description) >= 0;
                    var rect = null;
                    if (needsGeometry) {
                        if (geometryRect === null) geometryRect = new android.graphics.Rect();
                        rect = geometryRect;
                        raw.getBoundsInScreen(rect);
                    }
                    // Selection needs enabled/selected flags. Checkbox details
                    // are only needed when inspecting the order review.
                    var row = {
                        label: label,
                        rawLabel: rawLabel,
                        description: description,
                        classValue: classValue,
                        packageValue: String(raw.getPackageName() || ""),
                        isEnabled: needsGeometry && (!compactOrder || checkbox || orderAction) && raw.isEnabled(),
                        isClickable: needsGeometry && !compactOrder && raw.isClickable(),
                        isChecked: (orderFields || selectionFields) &&
                            (isCheckable || classValue === "android.widget.CheckBox") && raw.isChecked(),
                        isCheckable: isCheckable,
                        isSelected: !labelsOnly && raw.isSelected(),
                        area: rect ? { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom } : null,
                        ancestor: current.parent
                    };
                    row.bounds = rowBounds;
                    row.text = rowText;
                    row.enabled = rowEnabled;
                    row.checked = rowChecked;
                    row.parent = rowParent;
                    if (row.packageValue === activePackage) nodes.push(row);
                    if (promoteSku) {
                        deferred.push({ raw: raw, row: row });
                        retainedRaw = true;
                    }
                    for (var c = raw.getChildCount() - 1; c >= 0; c--) {
                        var child = raw.getChild(c);
                        if (child != null) stack.push({ raw: child, parent: row });
                    }
                } finally { if (!retainedRaw) raw.recycle(); }
            }
            screen = nodes;
            if (promoteSku && has("选择票档") && dateNodes().length === 1) {
                for (var d = 0; d < deferred.length; d++) {
                    if (Date.now() - started > 2000) stop("当前页面读取过慢或控件过多，已停止。");
                    var pending = deferred[d], pendingRow = pending.row, pendingRaw = pending.raw;
                    if (pendingRow.packageValue !== activePackage) continue;
                    if (geometryRect === null) geometryRect = new android.graphics.Rect();
                    pendingRaw.getBoundsInScreen(geometryRect);
                    pendingRow.area = { left: geometryRect.left, top: geometryRect.top,
                        right: geometryRect.right, bottom: geometryRect.bottom };
                    pendingRow.isEnabled = pendingRaw.isEnabled();
                    pendingRow.isClickable = pendingRaw.isClickable();
                    pendingRow.isSelected = pendingRaw.isSelected();
                    pendingRow.classValue = readOrderFields ? String(pendingRaw.getClassName() || "") : "";
                    pendingRow.isCheckable = (readOrderFields || readSelectionFields) && pendingRaw.isCheckable();
                    pendingRow.isChecked = (readOrderFields || readSelectionFields) &&
                        (pendingRow.isCheckable || pendingRow.classValue === "android.widget.CheckBox") &&
                        pendingRaw.isChecked();
                }
                labelsOnly = false;
            }
        } finally {
            // Release any queued native nodes even if the bounded read aborts.
            while (stack.length) stack.pop().raw.recycle();
            while (deferred.length) deferred.pop().raw.recycle();
        }
        screen = nodes;
        screenReadMode = labelsOnly;
        lastScreenReadStartedEpochMs = started;
        screenReads++;
        var elapsed = Date.now() - started;
        // Keep the latest bounded timings, so long presale/queue waits cannot
        // fill the diagnostic before the purchase stages. No private UI text.
        if (readTimings.length >= 120) readTimings.shift();
        readTimings.push({
            startedAfterOriginMs: started - timeOrigin, elapsedMs: elapsed,
            mode: labelsOnly || "full", nodes: nodes.length, phase: phase,
            rootReadMs: rootReadMs, nativeProbeMs: nativeProbeMs,
            treeCopyMs: Math.max(0, elapsed - rootReadMs - nativeProbeMs)
        });
        lastScreenReadMs = elapsed;
        if (labelsOnly) labelReads++;
        screenReadMs += elapsed;
        screenRootReadMs += rootReadMs;
        screenTreeCopyMs += Math.max(0, elapsed - rootReadMs);
        screenReadMaxMs = Math.max(screenReadMaxMs, elapsed);
    }
    function checkPackage(labelsOnly) { readScreen(labelsOnly); }
    function pauseAfterRead(intervalMs) {
        // A slow native read already consumed the polling interval.
        var remaining = intervalMs - lastScreenReadMs;
        if (remaining > 0) sleep(remaining);
    }
    function matching(predicate) {
        if (screen == null) readScreen();
        var result = [];
        for (var i = 0; i < screen.length; i++) {
            if (predicate(screen[i])) result.push(screen[i]);
        }
        return result;
    }
    function matchingPattern(pattern, withDescription) {
        return matching(function (node) {
            return pattern.test(node.label) || withDescription && pattern.test(node.description);
        });
    }
    function has(fragment) {
        if (screen == null) readScreen();
        for (var i = 0; i < screen.length; i++) {
            if (screen[i].label.indexOf(fragment) >= 0 ||
                    screen[i].description.indexOf(fragment) >= 0) return true;
        }
        return false;
    }
    function hasPattern(pattern) {
        if (screen == null) readScreen();
        for (var i = 0; i < screen.length; i++) {
            if (pattern.test(screen[i].label) || pattern.test(screen[i].description)) return true;
        }
        return false;
    }
    function hasAny(words) {
        var key = words.join("\u0001");
        if (patterns[key]) return hasPattern(patterns[key]);
        var alternatives = [];
        for (var i = 0; i < words.length; i++) {
            alternatives.push(words[i].replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
        }
        // test() already searches within the label. Leading .* needlessly
        // backtracks at every character when a long notice has no match.
        var pattern = new RegExp("(?:" + alternatives.join("|") + ")");
        patterns[key] = pattern;
        return hasPattern(pattern);
    }
    function checkBlockers() {
        if (hasAny(CHALLENGE)) stop("猫眼出现验证，请在手机手动处理并核对订单。");
        if (hasAny(PAYMENT) || hasPaymentOrderSheet()) stop("猫眼出现支付页面，请在手机核对并自行付款。");
        if (hasCongestion()) {
            if (!SUBMIT_TO_PAYMENT || FLOW_PRECHECK_ONLY || SELECTION_REHEARSAL) {
                stop("当前有拥堵弹窗；只读或选档演练不会刷新，请在手机处理。");
            }
            recoverCongestion();
            if (hasAny(CHALLENGE)) stop("刷新后出现验证，请在手机手动处理。");
            if (hasAny(PAYMENT) || hasPaymentOrderSheet()) stop("刷新后已到支付页面，请自行决定付款。");
        }
    }
    function hasCongestion() {
        return has("前方拥堵") && has("请刷新重试");
    }
    function isDescendantOf(node, ancestor) {
        for (var depth = 0; node != null && depth < 32; depth++, node = node.parent()) {
            if (node === ancestor) return true;
        }
        return false;
    }
    function congestionRefreshNodes() {
        var buttons = exactNodes(["刷新"]);
        var headings = matchingPattern(/前方拥堵/, true);
        var prompts = matchingPattern(/请刷新重试/, true);
        return buttons.filter(function (button) {
            var parent = button.parent(), buttonBounds = button.area;
            for (var depth = 0; parent != null && depth < 12; depth++, parent = parent.parent()) {
                var b = parent.area;
                // The window root is not proof of belonging to a dialog.
                // Require a bounded common container for the hints and button.
                if (!b || !buttonBounds || b.left < 0 || b.top < 0 ||
                        b.right > device.width || b.bottom > device.height ||
                        b.right <= b.left || b.bottom <= b.top ||
                        b.bottom - b.top >= device.height * 0.70 ||
                        b.right - b.left >= device.width * 0.98 ||
                        b.left > buttonBounds.left || b.right < buttonBounds.right ||
                        b.top > buttonBounds.top || b.bottom < buttonBounds.bottom) continue;
                var headingInside = headings.some(function (node) { return isDescendantOf(node, parent); });
                var promptInside = prompts.some(function (node) { return isDescendantOf(node, parent); });
                if (!headingInside || !promptInside) continue;
                var underlying = matching(function (node) {
                    var identity = node.label + " " + node.description;
                    return isDescendantOf(node, parent) &&
                        (identity.indexOf(TARGET.titleToken) >= 0 || node.label === TARGET.city ||
                         node.description === TARGET.city ||
                         /实名观演人|选择票档|提交订单|立即支付|取票方式|配送方式/.test(identity));
                });
                if (!underlying.length) return true;
            }
            return false;
        });
    }
    function recoverCongestion() {
        var deadline = TARGET.saleEpochMs + 1200000;
        while (Date.now() < deadline) {
            // Refresh is a separate dialog action, never a second submit.
            // Each touch uses a new tree, and payment/verification always wins.
            var allowWechat = OPEN_WECHAT_PAYMENT && terminalState === "submission_attempted";
            readScreen("refresh", allowWechat);
            if (screenPackage === "com.tencent.mm") return;
            if (hasAny(CHALLENGE)) stop("拥堵重试出现验证，请在手机手动完成。");
            if (hasAny(PAYMENT) || hasPaymentOrderSheet() || has("待付款") && !has("实名观演人")) return;
            if (!hasCongestion()) {
                // Resume with a full fresh snapshot: SKU selection and order
                // checkbox decisions must not use refresh-only properties.
                readScreen(false, allowWechat);
                if (screenPackage === "com.tencent.mm") return;
                if (hasCongestion()) continue;
                return;
            }
            if (!orderIdentityMatches() && !(has("选择票档") && dateNodes().length === 1)) {
                stop("拥堵弹窗下未确认目标订单或票档，已停止刷新。");
            }
            if (congestionRefreshes >= 3000) stop("拥堵刷新已达本次上限，已停止；请在手机核对。");
            var refresh = unique(congestionRefreshNodes(), "拥堵弹窗内刷新按钮");
            if (!refresh.enabled()) {
                pauseAfterRead(80);
                continue;
            }
            var remaining = lastCongestionRefreshMs === null ? 0 :
                lastCongestionRefreshMs + 400 - Date.now();
            if (remaining > 0) {
                sleep(remaining);
                continue; // Re-read after waiting; never click the old snapshot.
            }
            if (Date.now() >= deadline) stop("已超过开售窗口，已停止刷新。");
            safeButtonBounds(refresh, "拥堵刷新");
            var b = refresh.bounds();
            lastCongestionRefreshMs = Date.now();
            congestionRefreshes++;
            if (!click(Math.floor((b.left + b.right) / 2), Math.floor((b.top + b.bottom) / 2))) {
                stop("拥堵刷新点击结果不明确，已停止；不会点击提交或支付。");
            }
            screen = null;
            mark("拥堵刷新");
        }
        stop("拥堵恢复超时或已超过开售窗口，已停止刷新。");
    }
    function exactNodes(words) {
        return matching(function (node) {
            return words.indexOf(node.label) >= 0 || words.indexOf(node.description) >= 0;
        });
    }
    function entryNodes() {
        return matchingPattern(ENTRY_PATTERN, true);
    }
    function confirmationNodes() {
        return exactNodes(SKU_CONFIRM).filter(function (node) { return node.enabled(); });
    }
    function containedTextNodes(words) {
        return matching(function (node) {
            for (var i = 0; i < words.length; i++) {
                if (node.label.indexOf(words[i]) >= 0) return true;
            }
            return false;
        });
    }
    function unique(nodes, reason) {
        if (nodes.length !== 1) stop(reason + "未唯一识别，已停止。");
        return nodes[0];
    }
    function safeButtonBounds(node, stage) {
        var b = node.bounds();
        if (!b || b.left < 0 || b.top < 0 || b.right > device.width ||
                b.bottom > device.height || b.right <= b.left || b.bottom <= b.top ||
                b.bottom - b.top > device.height * 0.30) {
            stop(stage + "控件范围异常，已停止。");
        }
        if (stage === "购票入口" && b.top < device.height * 0.45) {
            stop("购票入口不在已预期的页面下方，已停止。");
        }
        if (stage === "票档确认" &&
                (b.top < device.height * 0.70 || b.left < device.width * 0.45)) {
            stop("票档确认按钮不在页面右下方，已停止。");
        }
        if (stage === "提交订单" && b.top < device.height * 0.70) {
            stop("提交订单按钮不在预期的页面下方，已停止。");
        }
    }
    function clickFresh(stage, resolveNodes, verifyContext, beforeClick) {
        verifyContext();
        var node = unique(resolveNodes(), stage);
        if (stage === "购票入口" && !node.enabled()) stop("购票入口尚不可用，已停止。");
        safeButtonBounds(node, stage);
        // The matched label is often a non-clickable child of a button.
        // A touch at the freshly verified label bounds reaches its parent.
        var b = node.bounds();
        var x = Math.floor((b.left + b.right) / 2);
        var y = Math.floor((b.top + b.bottom) / 2);
        if (beforeClick) beforeClick();
        var clickStarted = Date.now();
        linkReadyToAction(stage, clickStarted);
        if (stage === "购票入口") {
            if (entryClickEpochMs === null) entryClickEpochMs = clickStarted;
            lastEntryTouchEpochMs = clickStarted;
            entryTouchCount++;
        }
        // Reuse the short-touch dispatch for the guarded date, tier, confirm
        // and order actions too. Page readiness is still observed separately.
        var clicked = nativeSkuClickOnce(node, stage);
        if (clicked === null) clicked = shortTouchOnce(x, y, stage);
        if (stage === "购票入口") entryClickCallMs = Date.now() - clickStarted;
        if (!clicked) stop(stage + "点击结果不明确，已停止；请在手机核对。");
        screen = null;
    }
    function verifyDetail(requireReservation, reuseDecision, entryOnly) {
        if (!reuseDecision) checkPackage(entryOnly ? "entry" : false);
        checkBlockers();
        if (!has(TARGET.titleToken) || !has(TARGET.city)) {
            stop("当前猫眼页面未同时显示目标演出与城市，已停止。");
        }
        var reservationPattern = /^\s*已\s*预\s*约\s*$/;
        if (requireReservation &&
                matchingPattern(reservationPattern, true).length === 0 &&
                entryNodes().length !== 1) {
            // The purchase label may replace the reservation label just
            // before the advertised sale second. This preflight validates
            // that state; the timed loop still prevents an early touch.
            stop("目标详情页既无预约状态也无唯一购票入口，已停止。");
        }
    }
    function priceNodes() {
        var nodes = containedTextNodes(amountTokens), result = [];
        var headers = exactNodes(["票档"]), upper = 0, lower = device.height;
        if (headers.length === 1 && headers[0].area) upper = headers[0].area.bottom;
        var footer = matching(function (node) {
            return node.area && node.area.top > device.height * 0.60 &&
                (node.label === "数量" || /^(?:总计|合计|应付)/.test(node.label) ||
                 /^\d+\s*(?:张|份)$/.test(node.label) || SKU_CONFIRM.indexOf(node.label) >= 0);
        });
        for (var f = 0; f < footer.length; f++) lower = Math.min(lower, footer[f].area.top);
        for (var i = 0; i < nodes.length; i++) {
            var label = String(nodes[i].text() || "").replace(/[,，]/g, "");
            var numbers = label.match(/\d+(?:\.\d{1,2})?/g) || [];
            var bounds = nodes[i].area;
            if (bounds && bounds.top >= upper && bounds.bottom <= lower &&
                    label.length <= 35 && numbers.length === 1 &&
                    Number(numbers[0]) === TARGET.amount &&
                    tierKindMatches(label) &&
                    !/(缺货|售罄|不可售|登记)/.test(label) && candidateAvailable(nodes[i])) result.push(nodes[i]);
        }
        // A single Maoyan card can contain both "内场1098元" and "¥1098".
        // Count selectable cards, not repeated amount labels inside one card.
        // Distinct cards and nodes without an identifiable card stay separate.
        var groups = [];
        for (var r = 0; r < result.length; r++) {
            var card = tierClickableCard(result[r]), group = null;
            if (card && !tierCardKindMatches(card)) continue;
            for (var g = 0; card && g < groups.length; g++) {
                if (groups[g].card === card) { group = groups[g]; break; }
            }
            if (group === null) groups.push({card:card, node:result[r]});
            else if (/(?:看台|内场|大屏|站票|坐票|VIP)/.test(result[r].label)) group.node = result[r];
        }
        return groups.map(function (group) { return group.node; });
    }
    function tierClickableCard(node) {
        var current = node;
        for (var depth = 0; current !== null && depth < 6; depth++) {
            var b = current.area;
            if (current.isClickable && b && b.bottom - b.top < device.height * 0.30) return current;
            current = current.ancestor;
        }
        return null;
    }
    function tierCardKindMatches(card) {
        var b = card.area;
        var kinds = matching(function (node) {
            var a = node.area;
            return a && a.left >= b.left && a.right <= b.right && a.top >= b.top && a.bottom <= b.bottom &&
                node.label.length <= 60 && /(?:看台|内场|大屏|站票|坐票|VIP)/.test(node.label);
        });
        for (var i = 0; i < kinds.length; i++) if (!tierKindMatches(kinds[i].label)) return false;
        return true;
    }
    function tierKindMatches(label) {
        // Amount alone cannot distinguish two different categories at the same price.
        var expected = String(TARGET.tierLabel || "").match(/(?:看台|内场|大屏|站票|坐票|VIP)/g) || [];
        var actual = label.match(/(?:看台|内场|大屏|站票|坐票|VIP)/g) || [];
        if (!actual.length) return true; // split text nodes remain subject to uniqueness checks
        for (var i = 0; i < expected.length; i++) if (actual.indexOf(expected[i]) < 0) return false;
        return true;
    }
    function recordSelectedTarget() {
        checkBlockers();
        verifyDetail(false, true);
        if (!preparedLayout()) stop("请先打开猫眼预约选档页，再记录手机预选。");
        var date = unique(dateNodes(), "预选场次");
        if (!nodeAlreadySelected(date)) stop("无法读取目标场次的选中状态；请改用工作台填写目标。");
        var candidates = matching(function (node) {
            var label = node.label.replace(/[,，]/g, "");
            var numbers = label.match(/\d+(?:\.\d{1,2})?/g) || [];
            return label.length <= 60 && /[¥￥元]|看台|内场|VIP/.test(label) &&
                numbers.length === 1 && Number(numbers[0]) > 0 && Number(numbers[0]) <= 100000 &&
                nodeAlreadySelected(node);
        });
        var tier = unique(candidates, "明确选中的票档");
        if (/(缺货|售罄|不可售|登记)/.test(tier.label)) stop("预选票档当前不可售，未记录。");
        var counts = matchingPattern(/\d+\s*(?:张|份)/, false);
        if (!counts.length) stop("无法确认预选数量为 1 张；请改用工作台填写目标。");
        for (var i = 0; i < counts.length; i++) {
            if (Number(counts[i].label.match(/(\d+)\s*(?:张|份)/)[1]) !== 1) {
                stop("预选数量不是 1 张，未记录。");
            }
        }
        var selectedDates = matching(function (node) {
            return /20\d{2}[-./年]\d{1,2}[-./月]\d{1,2}/.test(node.label) &&
                node.label.length <= 30 && nodeAlreadySelected(node);
        });
        if (selectedDates.length > 1) stop("预选场次不唯一，未记录。");
        var record = { version: 1, key: TARGET.selectionKey, date: TARGET.dateLabels[0],
            amount: Number(tier.label.replace(/[,，]/g, "").match(/\d+(?:\.\d{1,2})?/)[0]),
            tierLabel: tier.label, quantity: 1, at: Date.now() };
        storages.create("qiangpiao.autox.maoyan.selection.v1").put(TARGET.selectionKey, record);
        mark("已记录明确预选");
        report("手机预选已记录；改选场次或票档后须重新记录。未点击、未下单。");
    }
    function loadFrozenTarget() {
        if (TARGET.selectionSource !== "phone") return;
        var record = storages.create("qiangpiao.autox.maoyan.selection.v1").get(TARGET.selectionKey, null);
        if (!record || record.version !== 1 || record.key !== TARGET.selectionKey ||
                record.date !== TARGET.dateLabels[0] || record.quantity !== 1 ||
                typeof record.amount !== "number" || record.amount <= 0 || record.amount > 100000 ||
                typeof record.tierLabel !== "string" || record.tierLabel.length > 60 ||
                (record.tierLabel.replace(/[,，]/g, "").match(/\d+(?:\.\d{1,2})?/g) || []).length !== 1 ||
                Number((record.tierLabel.replace(/[,，]/g, "").match(/\d+(?:\.\d{1,2})?/) || [0])[0]) !== record.amount ||
                typeof record.at !== "number" || Date.now() < record.at || Date.now() - record.at > 86400000) {
            stop("手机预选未记录或已过期；请重新记录，或改用工作台填写目标。未点击购票。");
        }
        TARGET.amount = record.amount;
        TARGET.tierLabel = record.tierLabel;
        readSelectionFields = true;
        rawAmount = String(TARGET.amount);
        amountTokens = [rawAmount, rawAmount.replace(/\B(?=(\d{3})+(?!\d))/g, ",")];
        mark("已锁定记录的票档");
    }
    function candidateAvailable(node) {
        if (!node.enabled()) return false;
        // Some builds put the stock badge in a sibling instead of in the
        // price label. Inspect only compact ancestors belonging to this card.
        var badges = matchingPattern(/(?:缺货登记|售罄|不可售)/, false);
        var parent = node.parent();
        for (var depth = 0; parent != null && depth < 2; depth++, parent = parent.parent()) {
            if (!parent.enabled()) return false;
            var card = parent.bounds();
            if (card.bottom - card.top > device.height * 0.20 ||
                    card.right - card.left > device.width * 0.95) continue;
            for (var j = 0; j < badges.length; j++) {
                var badge = badges[j].bounds();
                if (badge.left >= card.left && badge.right <= card.right &&
                        badge.top >= card.top && badge.bottom <= card.bottom) return false;
            }
        }
        return true;
    }
    function dateNodes() {
        if (datePattern === null) {
            var alternatives = [];
            for (var j = 0; j < TARGET.dateLabels.length; j++) {
                alternatives.push(TARGET.dateLabels[j].replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
            }
            datePattern = new RegExp("(?:" + alternatives.join("|") + ")");
        }
        var nodes = matchingPattern(datePattern, false);
        var result = [];
        for (var i = 0; i < nodes.length; i++) {
            var label = String(nodes[i].text() || "");
            if (label.length <= 30 && label.indexOf(TARGET.titleToken) < 0) result.push(nodes[i]);
        }
        return result;
    }
    function visibleOrderAmount() {
        var amounts = [], nodes = matching(function (node) { return /[¥￥]/.test(node.label); });
        for (var i = 0; i < nodes.length; i++) {
            var label = nodes[i].label.replace(/[,，]/g, ""), pattern = /[¥￥]\s*(\d+(?:\.\d{1,2})?)/g, match;
            while ((match = pattern.exec(label)) !== null) {
                var value = Number(match[1]);
                if (value > 0 && value <= 100000 && amounts.indexOf(value) < 0) amounts.push(value);
            }
        }
        return amounts.length === 1 ? amounts[0] : null;
    }
    function orderIdentityMatches() {
        if (!has(TARGET.titleToken) || !has(TARGET.city) || !has("实名观演人")) return false;
        if (dateNodes().length !== 1) return false;
        var amounts = containedTextNodes([String(TARGET.amount)]), amountMatched = false;
        for (var i = 0; i < amounts.length; i++) {
            var label = String(amounts[i].text() || "").replace(/[,，]/g, "");
            var currency = label.match(/[¥￥]\s*(\d+(?:\.\d{1,2})?)/);
            if (currency && Number(currency[1]) === TARGET.amount) amountMatched = true;
        }
        if (!amountMatched || visibleOrderAmount() !== TARGET.amount) return false;
        // "2026张智霖" is part of an event title, not 2026 tickets.
        // A quantity must end at the unit or a separator, never an artist name.
        var quantityPattern = /(\d+)\s*张(?=$|[\s,，;；。:：、）)\]】])/;
        var quantities = matchingPattern(quantityPattern, false);
        var one = false;
        for (var j = 0; j < quantities.length; j++) {
            var quantity = String(quantities[j].text() || "").match(quantityPattern);
            if (quantity && Number(quantity[1]) !== 1) return false;
            if (quantity) one = true;
        }
        return one;
    }
    function attendeeRegion() {
        var heading = unique(exactNodes(["实名观演人"]), "实名观演人区域").bounds();
        var next = unique(exactNodes(["取票方式", "配送方式"]), "取票方式区域").bounds();
        if (next.top <= heading.bottom) stop("实名观演人区域边界异常，已停止。");
        return { top: heading.bottom, bottom: next.top };
    }
    function nameDigest(value) {
        // Cache computations, never UI decisions. A new name still receives
        // its own hash and must match the target on the fresh screen.
        // Runtime memory only: nothing here is written to files or logs.
        var key = "\u0001" + value;
        if (Object.prototype.hasOwnProperty.call(nameDigests, key)) return nameDigests[key];
        var bytes = new java.lang.String(value).getBytes("UTF-8");
        var digest = java.security.MessageDigest.getInstance("SHA-256").digest(bytes);
        var output = "";
        for (var i = 0; i < digest.length; i++) {
            var hex = (digest[i] & 255).toString(16);
            output += hex.length === 1 ? "0" + hex : hex;
        }
        if (digestCacheCount < 64) {
            nameDigests[key] = output;
            digestCacheCount++;
        }
        return output;
    }
    function orderCheckboxes(region) {
        region = region || attendeeRegion();
        var nodes = matching(function (node) {
            return node.classValue === "android.widget.CheckBox" || node.isCheckable;
        });
        var result = [];
        for (var i = 0; i < nodes.length; i++) {
            var node = nodes[i], b = node.bounds();
            if (b.top >= region.top && b.bottom <= region.bottom) result.push(node);
        }
        return result;
    }
    function targetAttendeeBox() {
        var region = attendeeRegion();
        var labels = matchingPattern(/^.{1,50}$/, false);
        var matches = [], maskedMatches = [];
        var maskedHashes = TARGET.attendeeMaskedNameSha256 || [];
        for (var i = 0; i < labels.length; i++) {
            var node = labels[i], b = node.bounds();
            if (b.top < region.top || b.bottom > region.bottom) continue;
            var value = String(node.text() || "").replace(/^\s+|\s+$/g, "");
            var digest = nameDigest(value);
            if (digest === TARGET.attendeeNameSha256) matches.push(node);
            else if (/[*＊]/.test(value) && value.replace(/[*＊]/g, "").length &&
                    maskedHashes.indexOf(digest) >= 0) maskedMatches.push(node);
        }
        var box = unique(orderCheckboxes(region), "唯一观演人勾选框");
        if (matches.length === 1) return box;
        if (matches.length > 1) stop("目标观演人未唯一识别，已停止。");
        // A partial name is acceptable only for the user's already selected
        // sole attendee. Never choose an unchecked person by a partial name.
        if (USER_PRESELECTED && box.checked() && maskedMatches.length === 1) return box;
        stop("目标观演人未唯一识别或掩码观演人未预选，已停止。");
    }
    function ensureOnlyAttendeeSelected(reuseOrderDecision) {
        readOrderFields = true;
        if (!reuseOrderDecision) checkPackage("order");
        if (pageKind(true) !== "order" || !orderIdentityMatches()) {
            stop("订单信息与目标演出不一致，已停止。");
        }
        var box = targetAttendeeBox();
        if (box.checked()) return "已勾选";
        if (!box.enabled()) stop("观演人勾选框不可用，已停止。");
        safeButtonBounds(box, "观演人勾选框");
        var b = box.bounds();
        if (!shortTouchOnce(Math.floor((b.left + b.right) / 2),
                   Math.floor((b.top + b.bottom) / 2), "观演人勾选框")) {
            stop("观演人勾选结果不明确，已停止。");
        }
        mark("点击唯一观演人");
        var selectedDeadline = Date.now() + 2000;
        while (Date.now() < selectedDeadline) {
            checkPackage("order");
            if (pageKind(true) !== "order") stop("勾选后订单页已变化，请在手机核对。");
            if (targetAttendeeBox().checked()) return "已自动勾选";
            pauseAfterRead(Math.min(40, Math.max(0, selectedDeadline - Date.now())));
        }
        stop("未确认观演人已勾选，已停止；请在手机核对。");
    }
    function finishAtOrder() {
        if (SELECTION_REHEARSAL) {
            if (CONFIRM_SELECTION_REHEARSAL) terminalState = "order_review_reached";
            report("演练已到订单页；请在手机核对，未提交或支付。");
            return;
        }
        // The order-transition reader just fetched these fields. There is no
        // intervening action or wait. Submission still obtains a new snapshot.
        var attendee = ensureOnlyAttendeeSelected(screen !== null && screenReadMode === "order");
        if (SUBMIT_TO_PAYMENT) {
            submitToPayment();
            return;
        }
        report("已进入订单页，唯一观演人" + attendee + "；请在手机核对并自行处理支付。");
    }
    function submitToPayment() {
        // In WeChat mode, "立即支付" is accepted only on the identified
        // purchase review, never as a generic payment confirmation button.
        clickFresh("提交订单", function () {
            if (!OPEN_WECHAT_PAYMENT && exactNodes(SUBMIT).length === 0 && exactNodes(["立即支付"]).length) {
                stop("订单页仅显示立即支付，其动作尚未经真机确认；请手动点击进入支付页。");
            }
            var actions = SUBMIT;
            if (OPEN_WECHAT_PAYMENT && exactNodes(ORDER_TITLE).length === 1) actions = SUBMIT.concat(["立即支付"]);
            return exactNodes(actions).filter(function (node) { return node.enabled(); });
        }, function () {
            readOrderFields = true;
            checkPackage("order");
            if (pageKind(true) !== "order" || !orderIdentityMatches() || !targetAttendeeBox().checked()) {
                stop("提交前订单或观演人状态已变化，已停止。");
            }
            if (hasAny(["免密", "自动扣款", "先享后付"])) {
                stop("页面出现自动扣款相关选项，请手动处理；程序不会提交或支付。");
            }
        }, function () {
            // Persist before the single tap. No retry even if click() or the
            // following page read fails; the existing atomic attempt lock stays.
            if (USER_PRESELECTED) submittedAmount = visibleOrderAmount();
            storage.put(TARGET.attemptKey, { state: "submission_attempted", at: Date.now() });
            terminalState = "submission_attempted";
        });
        mark("已点击一次提交订单");
        var paymentTiming = beginTransition("提交后支付页面", "提交订单");
        var deadline = Date.now() + 8000;
        while (Date.now() < deadline) {
            // Observe the submission, then optionally initiate the verified
            // WeChat gateway. Final payment confirmation is never clicked.
            readScreen(true, OPEN_WECHAT_PAYMENT);
            if (screenPackage === "com.tencent.mm") {
                observeTransition(paymentTiming, "wechat");
                completeTransition(paymentTiming);
                finishAtWechat();
                return;
            }
            if (hasAny(CHALLENGE)) {
                terminalState = "verification_required";
                stop("提交后出现验证，请在手机手动完成；程序不会再次提交或付款。");
            }
            if (hasCongestion()) recoverCongestion();
            if (screenPackage === "com.tencent.mm") {
                observeTransition(paymentTiming, "wechat");
                completeTransition(paymentTiming);
                finishAtWechat(); return;
            }
            if (hasPaymentOrderSheet()) {
                observeTransition(paymentTiming, "payment_sheet_candidate");
                finishAtPaymentOrderSheet(paymentTiming); return;
            }
            if (hasAny(PAYMENT) || has("待付款") && !has("实名观演人")) {
                observeTransition(paymentTiming, "payment_page");
                completeTransition(paymentTiming);
                if (OPEN_WECHAT_PAYMENT && !STOP_AT_MERCHANT_PAYMENT) {
                    openWechatFromCashier();
                    return;
                }
                terminalState = has("待付款") ? "pending_payment" : "payment_page";
                mark("已到支付页面并停止");
                report("已确认进入待付款或支付页面；自动操作已结束，请自行决定付款。");
                return;
            }
            observeTransition(paymentTiming, null);
            pauseAfterRead(80);
        }
        terminalState = "submission_unconfirmed";
        stop("已提交一次，但未确认支付页面；请查看猫眼订单，程序不会重试或付款。");
    }
    function hasPaymentOrderSheet() {
        // Do not require the amount to match before blocking the underlying
        // order controls. A mismatched or unknown payment sheet also wins.
        return has("支付订单") && has("支付剩余时间") && has("确认支付");
    }
    function paymentOrderSheetScope() {
        var heading = unique(exactNodes(["支付订单"]), "支付订单标题");
        var action = unique(exactNodes(["确认支付"]), "支付订单确认按钮");
        safeButtonBounds(action, "支付订单按钮");
        if (!action.enabled()) stop("支付订单按钮尚不可用，未继续支付。");
        if (action.area.top < device.height * 0.70) stop("支付订单按钮位置异常，未继续支付。");
        var parent = heading.parent();
        for (var depth = 0; parent != null && depth < 12; depth++, parent = parent.parent()) {
            var b = parent.area;
            if (!b || b.left < 0 || b.top < device.height * 0.20 ||
                    b.right > device.width || b.bottom > device.height ||
                    b.right <= b.left || b.bottom <= b.top ||
                    b.bottom < device.height * 0.75 ||
                    b.bottom - b.top >= device.height * 0.75 || !isDescendantOf(action, parent)) continue;
            var nodes = matching(function (node) { return isDescendantOf(node, parent); });
            var timer = false, methods = false, underlyingOrder = false;
            for (var i = 0; i < nodes.length; i++) {
                var label = nodes[i].label + " " + nodes[i].description;
                if (label.indexOf("支付剩余时间") >= 0) timer = true;
                if (nodes[i].label === "支付方式" || nodes[i].description === "支付方式") methods = true;
                if (/实名观演人|确认购票|确认购买|取票方式|配送方式/.test(label) ||
                        label.indexOf(TARGET.titleToken) >= 0) underlyingOrder = true;
            }
            if (timer && methods && !underlyingOrder) return parent;
        }
        stop("支付订单弹窗边界未确认，未继续支付。");
    }
    function finishAtPaymentOrderSheet(paymentTiming) {
        terminalState = "payment_sheet_manual_required";
        readOrderFields = true;
        readScreen(false, OPEN_WECHAT_PAYMENT);
        if (screenPackage === "com.tencent.mm") { finishAtWechat(); return; }
        if (hasAny(CHALLENGE)) stop("支付订单弹窗出现验证，请在手机处理。");
        if (!hasPaymentOrderSheet()) stop("支付订单弹窗已变化，请在手机核对。");
        var scope = paymentOrderSheetScope();
        var nodes = matching(function (node) { return isDescendantOf(node, scope); });
        var amounts = [];
        for (var i = 0; i < nodes.length; i++) {
            var label = nodes[i].label.replace(/[,，]/g, "");
            var pattern = /[¥￥]\s*(\d+(?:\.\d{1,2})?)/g, match;
            while ((match = pattern.exec(label)) !== null) {
                var value = Number(match[1]);
                if (value > 0 && value <= 100000 && amounts.indexOf(value) < 0) amounts.push(value);
            }
            // Some layouts place the currency sign in a separate TextView.
            if (/^\s*\d{1,6}\.\d{2}\s*$/.test(label)) {
                var decimal = Number(label);
                if (decimal > 0 && decimal <= 100000 && amounts.indexOf(decimal) < 0) amounts.push(decimal);
            }
        }
        if (amounts.length !== 1 || amounts[0] !== TARGET.amount) {
            stop("支付订单弹窗金额未确认或与目标不一致，未继续支付。");
        }
        completeTransition(paymentTiming);
        if (OPEN_WECHAT_PAYMENT && !STOP_AT_MERCHANT_PAYMENT) {
            launchWechatFromPaymentOrderSheet(scope);
            return;
        }
        terminalState = "pending_payment_sheet";
        mark("支付订单弹窗停止");
        report("已识别目标金额的支付订单弹窗；脚本已停止，请自行决定付款。未点击确认支付。");
    }
    function sheetWechatSelected(scope) {
        var labels = exactNodes(["微信支付"]).filter(function (node) { return isDescendantOf(node, scope); });
        if (labels.length !== 1) return false;
        var selected = matching(function (node) {
            return isDescendantOf(node, scope) && node.isCheckable && node.checked();
        });
        if (selected.length !== 1) return false;
        if (selected[0] === labels[0] || isDescendantOf(labels[0], selected[0])) return true;
        // A radio circle can be a sibling of the method label. Accept only
        // their short, bounded row, never the card containing other methods.
        var parent = labels[0].parent();
        for (var depth = 0; parent != null && depth < 8; depth++, parent = parent.parent()) {
            var b = parent.area;
            if (parent === scope) break;
            if (b && b.bottom > b.top && b.bottom - b.top < device.height * 0.12 &&
                    isDescendantOf(parent, scope) && isDescendantOf(selected[0], parent)) return true;
        }
        return false;
    }
    function launchWechatFromPaymentOrderSheet(scope) {
        // This is the identified Maoyan merchant gateway after a submitted
        // order, not the equally named final confirmation inside WeChat.
        if (!SUBMIT_TO_PAYMENT || !USER_PRESELECTED || screenPackage !== PACKAGE ||
                hasAny(["免密", "自动扣款", "先享后付", "输入支付密码"]) || !sheetWechatSelected(scope)) {
            stop("支付订单弹窗未确认唯一已选微信方式，请在手机处理。");
        }
        var action = unique(exactNodes(["确认支付"]).filter(function (node) {
            return isDescendantOf(node, scope) && node.enabled();
        }), "猫眼微信跳转按钮");
        if (wechatLaunchAttempted) stop("本次已尝试唤起微信，已停止；不会重复操作。");
        safeButtonBounds(action, "猫眼微信跳转");
        var b = action.bounds();
        wechatLaunchAttempted = true;
        terminalState = "wechat_launch_attempted";
        storage.put(TARGET.attemptKey, { state: "wechat_launch_attempted", at: Date.now() });
        if (!shortTouchOnce(Math.floor((b.left + b.right) / 2), Math.floor((b.top + b.bottom) / 2),
                "猫眼支付订单微信跳转")) {
            stop("猫眼微信跳转点击结果不明确，请自行核对；不会重复点击或付款。");
        }
        screen = null;
        mark("猫眼支付订单跳转微信一次");
        var deadline = Date.now() + 10000;
        while (Date.now() < deadline) {
            readScreen(true, true);
            if (screenPackage === "com.tencent.mm") { finishAtWechat(); return; }
            if (hasAny(CHALLENGE)) stop("跳转微信时出现验证，请在手机手动处理。");
            pauseAfterRead(80);
        }
        terminalState = "wechat_launch_unconfirmed";
        stop("已点击猫眼支付订单跳转，但未确认微信付款页；不会重复点击或付款。");
    }
    function paymentAmountMatches() {
        return matching(function (node) {
            var text = node.label.replace(/[,，]/g, "");
            var amount = text.match(/[¥￥]\s*(\d+(?:\.\d{1,2})?)/) ||
                text.match(/^\s*(\d+(?:\.\d{1,2})?)\s*$/);
            return amount !== null && Number(amount[1]) === (submittedAmount !== null ? submittedAmount : TARGET.amount);
        }).length > 0;
    }
    function finishAtWechat() {
        // No click, input, intent or gesture is ever issued in WeChat.
        terminalState = hasAny(["微信支付", "确认支付", "付款", "支付方式"]) && paymentAmountMatches() ?
            "wechat_payment_page" : "wechat_foreground_unconfirmed";
        mark("微信前台停止");
        report(terminalState === "wechat_payment_page" ?
            "已识别微信付款页面及目标金额；脚本已停止，是否付款由你决定。" :
            "已进入微信，付款页面或金额未确认；脚本已停止，请自行核对。");
    }
    function wechatSelected() {
        var labels = exactNodes(["微信支付"]);
        if (labels.length !== 1) return false;
        var node = labels[0], bounds = node.bounds();
        if (node.isCheckable && node.checked()) return true;
        return matching(function (candidate) {
            if (!candidate.isCheckable || !candidate.checked()) return false;
            var b = candidate.bounds();
            return b && bounds && b.left <= bounds.left && b.top <= bounds.top &&
                b.right >= bounds.right && b.bottom >= bounds.bottom &&
                b.bottom - b.top < device.height * 0.25;
        }).length === 1;
    }
    function openWechatFromCashier() {
        readOrderFields = true;
        readScreen(false, true);
        if (screenPackage === "com.tencent.mm") { finishAtWechat(); return; }
        if (hasAny(CHALLENGE)) stop("收银台出现验证，请在手机手动处理。");
        if (!has("收银台") || has("实名观演人") || !paymentAmountMatches() ||
                hasAny(["免密", "自动扣款", "先享后付", "输入支付密码"])) {
            terminalState = "cashier_manual_required";
            stop("收银台跳转条件或金额未确认；请手动唤起微信，脚本不会付款。");
        }
        var direct = exactNodes(["前往微信支付", "跳转微信支付", "打开微信支付"]);
        var candidates = direct.length ? direct : wechatSelected() ? exactNodes(["立即支付"]) : [];
        terminalState = "cashier_manual_required";
        var action = unique(candidates.filter(function (node) { return node.enabled(); }), "唤起微信按钮");
        if (wechatLaunchAttempted) stop("本次已尝试唤起微信，已停止；不会重复操作。");
        safeButtonBounds(action, "唤起微信");
        var b = action.bounds();
        wechatLaunchAttempted = true;
        terminalState = "wechat_launch_attempted";
        storage.put(TARGET.attemptKey, { state: "wechat_launch_attempted", at: Date.now() });
        if (!shortTouchOnce(Math.floor((b.left + b.right) / 2), Math.floor((b.top + b.bottom) / 2),
                "猫眼收银台微信跳转")) {
            stop("唤起微信的点击结果不明确；请自行核对，程序不会重试。");
        }
        mark("已尝试唤起微信一次");
        var deadline = Date.now() + 10000;
        while (Date.now() < deadline) {
            readScreen(true, true);
            if (screenPackage === "com.tencent.mm") { finishAtWechat(); return; }
            if (hasAny(CHALLENGE)) stop("唤起微信时出现验证，请在手机手动处理。");
            pauseAfterRead(80);
        }
        terminalState = "wechat_launch_unconfirmed";
        stop("未确认微信付款页面；已停止，请自行检查，不重试或付款。");
    }
    function targetDateAlreadySelected() {
        var target = unique(dateNodes(), "目标日期");
        return nodeAlreadySelected(target);
    }
    function onlyTargetDateAvailable() {
        var choices = matching(function (node) {
            return node.area && node.label.length <= 45 &&
                /^(?:20\d{2}[-./]\d{1,2}[-./]\d{1,2}|(?:20\d{2}年)?\d{1,2}月\d{1,2}日)(?:\s|$)/.test(node.label);
        });
        return choices.length === 1 && dateNodes().length === 1 &&
            choices[0] === dateNodes()[0] && priceNodes().length === 1;
    }
    function nodeAlreadySelected(target) {
        var b = target.bounds();
        // Some Maoyan builds expose the red selected card through Android's
        // selected flag. If they do not, the safe fallback is to tap the date.
        var marked = matching(function (node) { return node.isSelected || node.isCheckable && node.isChecked; });
        for (var i = 0; i < marked.length; i++) {
            var card = marked[i].bounds();
            if (card.left <= b.left && card.top <= b.top &&
                    card.right >= b.right && card.bottom >= b.bottom &&
                    card.bottom - card.top < device.height * 0.25) return true;
        }
        return false;
    }
    function pageKind(reuseDecision) {
        if (!reuseDecision) checkPackage();
        checkBlockers();
        if ((hasAny(ORDER_TITLE) && hasAny(ORDER_ACTION) || exactNodes(SUBMIT).length) &&
                orderIdentityMatches()) return "order";
        // The real Maoyan selection page initially says "请选择票档". Quantity
        // and confirm controls appear only after a tier has been selected, so
        // requiring them here blocks the fast path before the price click.
        // The actual Maoyan SKU sheet omits the event title and city. The
        // detail page was checked immediately before opening this sheet; use
        // the unique target date on the current Maoyan window here.
        if (has("选择票档") && dateNodes().length === 1) return "sku";
        return "unknown";
    }
    function verifySelectedSku() {
        if (pageKind() !== "sku" || has("请选择票档")) {
            stop("目标票档尚未使确认按钮可用，已停止。");
        }
        var tier = unique(priceNodes(), "目标票档");
        if (retainedDateSelection && !targetDateAlreadySelected() ||
                retainedSingleDate && !onlyTargetDateAvailable()) {
            stop("确认前预选场次状态已变化，已停止。");
        }
        if (!skuSelectionMatches(tier)) stop("无法确认目标票档已选中或合计金额，已停止。");
        if (!skuQuantityIsOne()) stop("未确认 1 张数量，已停止。");
        var button = unique(confirmationNodes(), "票档确认按钮");
        safeButtonBounds(button, "票档确认");
    }
    function skuSelectionMatches(tier) {
        var amounts = skuTotalAmounts();
        if (amounts.length) return amounts.length === 1 && amounts[0] === TARGET.amount;
        return nodeAlreadySelected(tier);
    }
    function skuTotalAmounts() {
        // The actual Android layout splits "总计:￥" and "580" into sibling
        // text nodes. Only pair compact labels on the same bottom row.
        var totals = matching(function (node) {
            return node.area && node.area.top > device.height * 0.75 &&
                node.label.length <= 40 && /^(?:总计|合计|应付)/.test(node.label);
        });
        var amounts = [];
        for (var i = 0; i < totals.length; i++) {
            var label = totals[i].label.replace(/[,，]/g, "");
            var value = label.match(/^(?:总计|合计|应付)\s*[:：]?\s*[¥￥]?\s*(\d+(?:\.\d{1,2})?)(?:\s*元)?$/);
            if (value && amounts.indexOf(Number(value[1])) < 0) amounts.push(Number(value[1]));
            if (value) continue;
            var anchor = totals[i].area;
            var siblings = matching(function (node) {
                var b = node.area;
                return b && b.left >= anchor.right - 2 && b.left - anchor.right < device.width * 0.20 &&
                    b.top < anchor.bottom && b.bottom > anchor.top &&
                    /^[¥￥]?\s*\d[\d,]*(?:\.\d{1,2})?(?:\s*元)?$/.test(node.label);
            });
            for (var j = 0; j < siblings.length; j++) {
                var number = Number(siblings[j].label.replace(/[¥￥元,\s]/g, ""));
                if (number > 0 && amounts.indexOf(number) < 0) amounts.push(number);
            }
        }
        return amounts;
    }
    function skuQuantityNodes() {
        var headers = exactNodes(["数量"]);
        return matching(function (node) {
            if (!/^\d+\s*(?:张|份)$/.test(node.label)) return false;
            if (headers.length !== 1 || !headers[0].area || !node.area) return true;
            var header = headers[0].area, b = node.area;
            return Math.abs((b.top + b.bottom - header.top - header.bottom) / 2) < device.height * 0.06;
        });
    }
    function skuQuantityIsOne() {
        var quantities = skuQuantityNodes();
        if (quantities.length > 1) stop("票档数量控件不唯一，已停止。");
        if (quantities.length === 1 && Number(quantities[0].label.match(/\d+/)[0]) !== 1) {
            stop("票档数量不是 1 张，已停止。");
        }
        return quantities.length === 1;
    }
    function waitForSkuConfirmation(timeoutMs, selectionTiming) {
        var deadline = Date.now() + timeoutMs;
        while (Date.now() < deadline) {
            var kind = pageKind(screen !== null);
            if (kind === "order") {
                observeTransition(selectionTiming, "order");
                completeTransition(selectionTiming);
                return kind;
            }
            if (kind === "sku") {
                unique(priceNodes(), "目标票档");
                if (retainedDateSelection && !targetDateAlreadySelected() ||
                        retainedSingleDate && !onlyTargetDateAvailable()) {
                    stop("确认前预选场次状态已变化，已停止。");
                }
                if (!has("请选择票档") && skuQuantityIsOne() && confirmationNodes().length === 1 &&
                        skuSelectionMatches(unique(priceNodes(), "目标票档"))) {
                    observeTransition(selectionTiming, "sku_controls");
                    completeTransition(selectionTiming);
                    return kind;
                }
            }
            observeTransition(selectionTiming, null);
            sleep(Math.max(2, SELECTION_READY_POLL_MS - lastScreenReadMs));
            checkPackage();
        }
        stop("票档确认控件未及时就绪，已停止；不会重复选档。");
    }
    function readTransitionKind(firstRead, preserveEntry, expectedKind) {
        // Entry polls also complete an already-ready SKU from the same root.
        // Other transitions keep their first complete decision read.
        checkPackage(expectedKind === "order" ? "order-transition" : preserveEntry ? "entry-transition" : firstRead ? false : true);
        var kind = pageKind(true);
        if (kind === "sku" && screenReadMode && expectedKind !== "order") {
            // Other transitions only copied text. Complete them on a new
            // snapshot; an entry SKU was already completed from its live root.
            checkPackage();
            kind = pageKind(true);
        }
        // A marker-supported order transition already includes attendee fields.
        // A label-only fallback fetches them in finishAtOrder. Submission always
        // obtains a fresh complete order snapshot before its single action.
        return kind;
    }
    function waitForKind(timeoutMs) {
        var deadline = Date.now() + timeoutMs;
        var firstRead = true;
        while (Date.now() < deadline) {
            var kind = readTransitionKind(firstRead, false);
            firstRead = false;
            if (kind !== "unknown") return kind;
            pauseAfterRead(80);
        }
        stop("页面转换后未识别出选票或订单页，已停止。");
    }
    function waitForKindAfterEntry(timeoutMs) {
        var deadline = Date.now() + timeoutMs;
        var firstRead = true;
        var entryTiming = beginTransition("入口后页面", "购票入口", ["目标日期", "目标票档", "提交订单"]);
        while (Date.now() < deadline) {
            var kind = readTransitionKind(firstRead, true);
            firstRead = false;
            if (kind !== "unknown") {
                observeTransition(entryTiming, kind);
                completeTransition(entryTiming);
                return kind;
            }
            observeTransition(entryTiming, null);
            // A second entry touch is allowed only when a fresh read still
            // shows the exact same event, city and one enabled entry control.
            // Never repeat over a loading, queue or verification overlay.
            if (has(TARGET.titleToken) && has(TARGET.city) &&
                    !hasAny(["加载中", "正在加载", "排队中", "正在跳转"])) {
                var entries = entryNodes();
                if (entries.length > 1) stop("购票入口出现多个候选，已停止。");
                if (entries.length === 1 && entries[0].enabled() && entryTouchCount < 3) {
                    var waitMs = lastEntryTouchEpochMs + 250 - Date.now();
                    if (waitMs > 0) {
                        pauseAfterRead(waitMs);
                        continue; // Never click from a snapshot taken before the wait.
                    }
                    clickFresh("购票入口", entryNodes,
                        function () { verifyDetail(false, true); });
                    mark("同页重试购票入口");
                    continue;
                }
            }
            // Yield even when a slow read already used the interval; do not
            // busy-loop on Android's accessibility service during loading.
            sleep(Math.max(2, ENTRY_TRANSITION_POLL_MS - lastScreenReadMs));
        }
        stop("入口点击后未识别到选票或订单页，已停止；请在手机核对。");
    }
    function continueSkuFlow() {
        if (!targetDateAlreadySelected() && onlyTargetDateAvailable()) {
            // This Android sheet has already populated tiers for its sole
            // date but supplies no selected flags. Re-tapping that date can
            // reset the existing tier and race the following price touch.
            retainedSingleDate = true;
            mark("沿用唯一场次");
        } else if (!targetDateAlreadySelected()) {
            clickFresh("目标日期", dateNodes, function () {
                if (pageKind() !== "sku") stop("目标日期选择页已变化，已停止。");
            });
            mark("点击目标日期");
            // Read immediately, retry only while the sheet is transitioning.
            waitForKind(1500);
        } else {
            retainedDateSelection = true;
            mark("沿用已选日期");
        }
        // Reuse only the current decision, never a pre-click snapshot.
        // A date click cleared screen, so that path must fetch a new tree.
        if (pageKind(screen !== null) === "order") {
            finishAtOrder();
            return;
        }
        else {
            var tierDeadline = Date.now() + 2500, tier = null;
            while (Date.now() < tierDeadline) {
                var availableTiers = priceNodes();
                if (availableTiers.length > 1) stop("目标票档未唯一识别，已停止。");
                if (availableTiers.length === 1) { tier = availableTiers[0]; break; }
                // A date tap can replace all tier controls asynchronously.
                // Observe fresh trees until the requested tier is available.
                pauseAfterRead(80);
                checkPackage();
                if (pageKind(true) !== "sku") stop("等待票档时选票页已变化，已停止。");
            }
            if (tier === null) stop("目标票档不可售或未及时更新，已停止。");
            // Select the configured card exactly once, without first testing
            // whether Maoyan's reservation restored the tier. Post-click
            // quantity/amount/confirmation checks still use fresh snapshots.
            skuDecision = { tierCandidates: availableTiers.length,
                selectionMode: "explicit_click", preselectionChecked: false };
            clickFresh("目标票档", priceNodes, function () {
                if (pageKind() !== "sku") stop("目标票档选择页已变化，已停止。");
                if (retainedDateSelection && !targetDateAlreadySelected() ||
                        retainedSingleDate && !onlyTargetDateAvailable()) {
                    stop("选档前预选场次状态已变化，已停止。");
                }
            });
            mark("点击目标票档");
            var selectionTiming = beginTransition("票档控件就绪", "目标票档", ["票档确认"]);
            var choiceDeadline = Date.now() + 2000;
            while (Date.now() < choiceDeadline &&
                    (screen === null || has("请选择票档"))) {
                checkPackage();
                checkBlockers();
                if (!has("请选择票档")) break;
                observeTransition(selectionTiming, null);
                sleep(Math.max(2, SELECTION_READY_POLL_MS - lastScreenReadMs));
            }
            if (has("请选择票档")) {
                stop("目标票档未被选中或已不可售，已停止。");
            }
            var selectedPage = pageKind(true);
            if (selectedPage !== "sku" && selectedPage !== "order") {
                stop("票档点击后页面状态不明确，已停止；请在手机核对。");
            }
            mark("票档按钮已更新");
            if (selectedPage === "order") {
                observeTransition(selectionTiming, "order");
                completeTransition(selectionTiming);
                finishAtOrder();
                return;
            }
            if (SELECTION_REHEARSAL && !CONFIRM_SELECTION_REHEARSAL) {
                // Rehearsal only observes the tier response, not a proven
                // ready confirmation control. Do not label it ready here.
                completeTransition(selectionTiming);
                report("选档演练已点中目标票档；未点击确认，请在猫眼核对。");
                return;
            }
            // Button text, quantity and enabled state can arrive in
            // separate accessibility updates. Ready pages return without
            // another read or delay; slower pages are observed only.
            if (waitForSkuConfirmation(2000, selectionTiming) === "order") {
                finishAtOrder();
                return;
            }
            mark("票档选择已核对");
            clickFresh("票档确认", confirmationNodes,
                verifySelectedSku);
            mark("点击票档确认");
            var orderTiming = beginTransition("确认后订单页面", "票档确认", ["提交订单"]);
            var orderDeadline = Date.now() + 8000;
            while (Date.now() < orderDeadline) {
                // The confirmation was already tapped. Never populate or act
                // on the old SKU; order actions will fetch fresh full fields.
                var nextKind = readTransitionKind(false, false, "order");
                if (nextKind === "order") {
                    observeTransition(orderTiming, "order");
                    completeTransition(orderTiming);
                    mark("到达订单页");
                    finishAtOrder();
                    return;
                }
                observeTransition(orderTiming, null);
                pauseAfterRead(80);
            }
            stop("已点击票档确认，但未确认进入订单页；请在手机核对，程序不会重复点击。");
            return;
        }
    }
    function preparedLayout() {
        return has("选择票档") || exactNodes(["场次"]).length === 1 &&
            exactNodes(["票档"]).length === 1;
    }
    function preparedLayoutWithIdentity() {
        return preparedLayout() && has(TARGET.titleToken) && has(TARGET.city);
    }
    function verifyPrepared(reuseDecision) {
        if (!reuseDecision) checkPackage(false);
        verifyDetail(false, true);
        if (!preparedLayout() || has("请选择票档")) {
            stop("预选页面状态已变化，已停止。");
        }
        {
            var date = unique(dateNodes(), "预选场次");
            var tier = unique(priceNodes(), "预选票档");
            if (!nodeAlreadySelected(date) || !nodeAlreadySelected(tier)) {
                stop("未能确认预选场次与票档均为目标，已停止；请在手机核对。");
            }
        }
        var counts = matchingPattern(/\d+\s*(?:张|份)/, false);
        for (var i = 0; i < counts.length; i++) {
            var value = counts[i].label.match(/(\d+)\s*(?:张|份)/);
            if (value && Number(value[1]) !== 1) stop("预选票数不是 1 张，已停止。");
        }
    }
    function preparedActions() {
        return exactNodes(ENTRY.concat(SKU_CONFIRM));
    }
    function runPreparedStart() {
        verifyPrepared(false);
        mark("已核对预选页面");
        if (PRECHECK_ONLY || SELECTION_REHEARSAL) {
            report("预选页面已核对；当前检查模式不点击、不提交。");
            return;
        }
        // Preserve the prepared sheet rather than opening it again.
        // Missing or disabled controls are observed, never repeatedly tapped.
        while (Date.now() < TARGET.saleEpochMs) {
            var remaining = TARGET.saleEpochMs - Date.now();
            pauseAfterRead(Math.min(remaining > 30000 ? 1000 :
                                   remaining > 3000 ? 200 : 20, remaining));
            verifyPrepared(false);
        }
        var deadline = Date.now() + 60000;
        var action = null;
        while (Date.now() < deadline) {
            var candidates = preparedActions();
            if (candidates.length > 1) stop("预选页推进按钮不唯一，已停止。");
            if (candidates.length === 1 && candidates[0].enabled()) {
                action = candidates[0];
                break;
            }
            pauseAfterRead(20);
            verifyPrepared(false);
        }
        if (action === null) stop("预选页推进按钮未及时可用，已停止。");
        var confirmation = SKU_CONFIRM.indexOf(action.label) >= 0;
        var actionLabel = action.label;
        entryFoundEpochMs = Date.now();
        clickFresh("购票入口", preparedActions, function () {
            // In user-preselected mode this snapshot was just read by the
            // readiness loop, with button bounds/state. No intervening wait.
            verifyPrepared(USER_PRESELECTED);
            if (unique(preparedActions(), "预选推进").label !== actionLabel) {
                stop("预选推进动作已变化，已停止。");
            }
            if (confirmation) {
                unique(exactNodes(["1张", "1 张", "1份", "1 份"]), "预选确认票数");
                safeButtonBounds(unique(preparedActions(), "预选确认"), "票档确认");
            }
        }, claimAttempt);
        mark("从预选页面直接推进");
        var orderDeadline = Date.now() + 8000;
        while (Date.now() < orderDeadline) {
            var preparedKind = pageKind();
            if (preparedKind === "order") {
                finishAtOrder();
                return;
            }
            // A reservation entry can open the actual SKU sheet first.
            // A confirmation already tapped must never be tapped again.
            if (!confirmation && preparedKind === "sku" && !preparedLayoutWithIdentity()) {
                continueSkuFlow();
                return;
            }
            pauseAfterRead(20);
        }
        stop("预选页已点击一次但订单页未确认，已停止；不会重复点击。");
    }
    function claimAttempt() {
        // Java File.createNewFile is atomic across two script engines. A
        // crashed script leaves the marker in AutoX private storage, which
        // prevents two engines from tapping the same selection at once.
        var name = "qiangpiao-maoyan-" + TARGET.attemptKey.replace(/:/g, "-") + ".lock";
        var marker = new java.io.File(context.getFilesDir(), name);
        if (!marker.createNewFile()) {
            stop("这场演出已有一次手机端选档尝试；请核对猫眼页面。");
        }
        storage.put(TARGET.attemptKey, { state: "started", at: Date.now() });
    }

    function waitUntilEpoch(epochMs) {
        while (Date.now() < epochMs) sleep(Math.min(20, epochMs - Date.now()));
    }
    function runFixedCoordinatePath() {
        var path = FAST_COORDS;
        if (!path || device.width !== path.width || device.height !== path.height ||
                !path.entry || !path.tier || !path.confirm ||
                !SUBMIT_TO_PAYMENT || !USER_PRESELECTED || !OPEN_WECHAT_PAYMENT) {
            stop("固定坐标模式配置或手机尺寸不匹配，已停止。");
        }
        function touchConfigured(stage, point) {
            var started = Date.now();
            if (stage === "固定入口") {
                entryClickEpochMs = started;
                lastEntryTouchEpochMs = started;
                entryTouchCount++;
            }
            if (!shortTouchOnce(point[0], point[1], stage)) {
                stop(stage + "触摸结果不明确，已停止；不会重试。");
            }
            if (stage === "固定入口") entryClickCallMs = Date.now() - started;
            mark(stage);
        }
        // The last identity read occurs before opening; sale-time touches do
        // not fetch a full accessibility tree or retry a missed coordinate.
        while (Date.now() < TARGET.saleEpochMs - 1000) {
            sleep(Math.min(200, TARGET.saleEpochMs - 1000 - Date.now()));
        }
        checkPackage(true);
        // The normal reservation label may already have become the purchase
        // label just before sale. Identity must still match, but an early
        // switch is not an error.
        verifyDetail(false, true);
        waitUntilEpoch(TARGET.saleEpochMs);
        if (currentPackage() !== PACKAGE) stop("开售时猫眼不在前台，已停止。");
        claimAttempt();
        touchConfigured("固定入口", path.entry);
        waitUntilEpoch(TARGET.saleEpochMs + path.gapMs);
        touchConfigured("固定目标票档", path.tier);
        waitUntilEpoch(TARGET.saleEpochMs + path.gapMs * 2);
        touchConfigured("固定票档确认", path.confirm);

        // An order is submitted only after the target date, amount, quantity
        // and single attendee are confirmed by the existing order guards.
        var deadline = Date.now() + 8000;
        while (Date.now() < deadline) {
            if (pageKind() === "order") {
                mark("固定路径到达订单页");
                finishAtOrder();
                return;
            }
            pauseAfterRead(40);
        }
        stop("固定点击后未确认进入目标订单页；不重试或提交，请在手机核对。");
    }

    try {
        auto.waitFor();
        // The device reported a stale accessibility root in AutoX's cached
        // fast mode even while Maoyan was foreground. Normal mode passed the
        // same on-device precheck and keeps the page guard reliable.
        auto.setMode("normal");
        toast("20 秒内请手动切到猫眼目标演出页");
        sleep(20000);
        if (SELECTION_REHEARSAL) timeOrigin = Date.now();
        if (FLOW_PRECHECK_ONLY) {
            timeOrigin = Date.now();
            readOrderFields = true;
            checkPackage();
            if (CAPTURE_SELECTION_ONLY) {
                recordSelectedTarget();
                return;
            }
            loadFrozenTarget();
            if (TARGET.selectionSource === "phone") checkPackage();
            if (nameDigest("qiangpiao") !== "571ca4acfff4ab88e8e95a21b5ef78cc614c1ca65de69db58a108fdd4bb671f2") {
                stop("手机端姓名摘要校验异常，已停止只读检查。");
            }
            if (CONGESTION_PRECHECK_ONLY) {
                if (hasAny(CHALLENGE)) stop("只读拥堵检查发现验证，请在手机处理；未点击。");
                if (hasAny(PAYMENT)) stop("只读拥堵检查发现支付页；未点击。");
                var buttons = congestionRefreshNodes();
                congestionProbe = {
                    present: hasCongestion(),
                    targetMatches: orderIdentityMatches() || has("选择票档") && dateNodes().length === 1,
                    refreshCandidates: buttons.length,
                    refreshEnabled: buttons.length === 1 && buttons[0].enabled(),
                    validBounds: false
                };
                if (buttons.length === 1) {
                    safeButtonBounds(buttons[0], "拥堵刷新");
                    congestionProbe.validBounds = true;
                }
                mark("拥堵弹窗只读检查完成");
                report(congestionProbe.present && congestionProbe.targetMatches &&
                    congestionProbe.refreshEnabled && congestionProbe.validBounds ?
                    "只读检查：目标拥堵弹窗与唯一可用刷新按钮已识别；未点击。" :
                    "只读检查：当前未满足自动刷新识别条件；未点击。");
                return;
            }
            var probeDates = dateNodes(), probeTiers = priceNodes();
            preparedProbe = {
                layout: preparedLayout(),
                titleMatches: has(TARGET.titleToken), cityMatches: has(TARGET.city),
                dateCandidates: probeDates.length, tierCandidates: probeTiers.length,
                dateSelected: probeDates.length === 1 && nodeAlreadySelected(probeDates[0]),
                tierSelected: probeTiers.length === 1 && nodeAlreadySelected(probeTiers[0])
            };
            if (preparedLayout() && has(TARGET.titleToken) && has(TARGET.city)) {
                verifyPrepared(true);
                mark("预选页面只读检查完成");
                report("只读检查通过：预选演出、场次、票档及选中状态已匹配；未点击。");
                return;
            }
            var probeKind = pageKind(true);
            if (probeKind === "order") {
                var probeBox = targetAttendeeBox();
                mark("订单页只读检查完成");
                report("只读检查通过：目标订单与观演人已匹配；勾选=" + probeBox.checked() + "；未点击。");
            } else if (probeKind === "sku") {
                var probePrices = priceNodes();
                preparedProbe = { layout: "sku", dateCandidates: dateNodes().length,
                    tierCandidates: probePrices.length,
                    explicitlySelected: probePrices.length === 1 && nodeAlreadySelected(probePrices[0]),
                    totalMatches: probePrices.length === 1 && skuSelectionMatches(probePrices[0]),
                    quantityMatches: skuQuantityIsOne(), confirmCandidates: confirmationNodes().length };
                mark("选票页只读检查完成");
                report("只读检查完成：目标日期已识别，可用目标票档数量=" + probePrices.length + "；未点击。");
            } else {
                verifyDetail(false, true);
                mark("详情页只读检查完成");
                report("只读检查通过：目标演出与城市已匹配；未点击。读取次数=" + screenReads + "，累计读取=" + screenReadMs + "ms");
            }
            return;
        }
        // Detail preflight only needs labels. Geometry is fetched at the
        // fresh pre-click validation, not during the initial identity check.
        checkPackage(true);
        var now = Date.now();
        if (!PRECHECK_ONLY && !SELECTION_REHEARSAL &&
                (now < TARGET.saleEpochMs - 600000 || now > TARGET.saleEpochMs + 1200000)) {
            stop("当前不在目标开售前 10 分钟到开售后 20 分钟窗口，已停止。");
        }
        if (!SELECTION_REHEARSAL) storage = storages.create("qiangpiao.autox.maoyan.prototype.v1");
        if (!SELECTION_REHEARSAL && storage.contains(TARGET.attemptKey)) {
            stop("这场演出已有一次手机端选档尝试；请核对猫眼页面。");
        }
        loadFrozenTarget();
        if (SELECTION_REHEARSAL && CONFIRM_SELECTION_REHEARSAL && has("选择票档")) {
            checkPackage();
            verifyDetail(false, true);
            if (pageKind(true) !== "sku") stop("选档确认演练未识别目标选票页，已停止。");
            continueSkuFlow();
            return;
        }
        if (FAST_FIXED_PATH) {
            // Maoyan can replace "已预约" with the purchase entry before the
            // advertised sale second. Accept either state on this same event.
            verifyDetail(false, true);
            if (matchingPattern(/^\s*已\s*预\s*约\s*$/, true).length === 0 &&
                    entryNodes().length !== 1) {
                stop("目标详情页既无预约状态也无唯一购票入口，已停止。");
            }
            runFixedCoordinatePath();
            return;
        }
        if (preparedLayout()) {
            runPreparedStart();
            return;
        }
        verifyDetail(!SELECTION_REHEARSAL && now < TARGET.saleEpochMs, true);
        var reuseInitialEntryDecision = SELECTION_REHEARSAL || now >= TARGET.saleEpochMs;
        if (PRECHECK_ONLY) {
            console.log("猫眼手机端预检通过：演出、城市和预约状态已识别；未点击。");
            return;
        }
        while (!SELECTION_REHEARSAL && Date.now() < TARGET.saleEpochMs) {
            checkPackage(true);
            verifyDetail(false, true);
            var remaining = TARGET.saleEpochMs - Date.now();
            if (remaining <= 0) {
                // This read itself crossed the sale time. Search its labels
                // immediately instead of reading the same detail page again.
                // A read before sale followed by sleep must never use this path.
                reuseInitialEntryDecision = true;
                break;
            }
            pauseAfterRead(Math.min(remaining > 30000 ? 1000 :
                                   remaining > 3000 ? 200 : 20, remaining));
        }
        mark(SELECTION_REHEARSAL ? "选档演练开始" : "开售等待结束");

        // A crowded on-sale page can lag behind the announced start time.
        // Keep looking for a single real purchase control without tapping
        // a guessed location.
        var entryDeadline = Date.now() + (SELECTION_REHEARSAL ? 5000 : 60000);
        var entryNode = null;
        while (Date.now() < entryDeadline) {
            // Fetch one fresh entry snapshot per poll: all labels for the
            // identity/challenge guards, geometry/state only for real entry
            // candidates. Do not traverse another entire tree just because a
            // candidate appeared. A read started before sale is never a tap
            // snapshot, even when that read happened to finish after sale.
            if (!reuseInitialEntryDecision || screenReadMode !== "entry" ||
                    lastScreenReadStartedEpochMs < TARGET.saleEpochMs) checkPackage("entry");
            reuseInitialEntryDecision = false;
            verifyDetail(false, true);
            var possibleEntry = entryNodes();
            if (possibleEntry.length > 1) stop("购票入口出现多个候选，已停止。");
            if (possibleEntry.length === 1) {
                entryFoundEpochMs = Date.now();
                // The same fresh snapshot already contains the state and
                // bounds. If still disabled, observe a new snapshot next poll.
                if (possibleEntry[0].enabled()) {
                    entryNode = possibleEntry[0];
                    entryDecisionReadMs = lastScreenReadMs;
                    break;
                }
            }
            pauseAfterRead(20);
        }
        if (!entryNode) stop("开售后未找到唯一购票入口，已停止。");
        mark("发现购票入口");

        // The loop just read the live control. No sleep or page action occurs
        // between that read and this touch; do not fetch it a third time.
        clickFresh("购票入口", entryNodes,
            function () { verifyDetail(false, true); },
            function () { if (!SELECTION_REHEARSAL) claimAttempt(); });
        mark("点击购票入口");

        var kind = waitForKindAfterEntry(5000);
        entryNextPageEpochMs = Date.now();
        mark(kind === "sku" ? "识别选票页" : "识别订单页");
        if (kind === "order") {
            finishAtOrder();
            return;
        }
        if (kind === "sku") {
            continueSkuFlow();
            return;
        }
        stop("未识别到目标选票页，已停止。");
    } catch (error) {
        // Unexpected errors can include raw UI values. Print only our own
        // fixed safe messages, never an exception's original text or stack.
        report(error && error.safe ? error.message :
            "手机端原型遇到未知页面或权限异常，已停止；请在猫眼核对。");
    }
})();

