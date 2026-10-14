// Read-only AutoX v7 benchmark. No selection, order or payment actions.
// Full-copy baseline is generated from the current local production reader.
var READ_BENCH_RUN_ID = "manual-read-benchmark";
var READ_BENCH_MARKERS = null;
(function () {
    var result = {runId:READ_BENCH_RUN_ID, startedEpochMs:Date.now(), readOnly:true, samples:[]};
    var benchPhase="wait_service";
    files.write("/sdcard/Download/qiangpiao-read-benchmark-started.json",JSON.stringify(result));
    var PACKAGE = "com.sankuai.movie", OPEN_WECHAT_PAYMENT = false;
    var ENTRY_PATTERN = /^(?:立即预订|立即预定|立即购票|立即抢购|立即抢票|特惠购票|立即购买)$/;
    var SKU_CONFIRM = ["确认","确定"];
    var screen = null, screenPackage = null, screenReadMode = null, nativeSkuClickSupported = false;
    var readOrderFields = true, readSelectionFields = true;
    var screenReads = 0, lastScreenReadStartedEpochMs = 0, lastScreenReadMs = 0;
    var labelReads = 0, screenReadMs = 0, screenRootReadMs = 0, screenTreeCopyMs = 0, screenReadMaxMs = 0;
    function visibleLabel(value) { return String(value || "").replace(/[\u200B\uFEFF]/g, ""); }
    function rowBounds() { return this.area; }
    function rowText() { return this.label; }
    function rowEnabled() { return this.isEnabled; }
    function rowChecked() { return this.isChecked; }
    function rowParent() { return this.ancestor; }
    function stop(message) {
        var failure=new Error(message);
        failure.code=message==="当前页面读取过慢或控件过多，已停止。"?"full_read_budget":
            message==="猫眼已不在前台，已停止。"?"wrong_foreground":"native_read_unavailable";
        throw failure;
    }
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
        var nodes = [], stack = [{ raw: root, parent: null }];
        // Copy coordinates into each row, but reuse the Java output argument.
        // This avoids a native Rect allocation for every node in the tree.
        var geometryRect = null;
        try {
            while (stack.length) {
                if (nodes.length >= 1000 || Date.now() - started > 2000) {
                    result.lastFullReadProgress={nodes:nodes.length,elapsedMs:Date.now()-started,
                        nodeLimitHit:nodes.length>=1000,timeLimitHit:Date.now()-started>2000};
                    stop("当前页面读取过慢或控件过多，已停止。");
                }
                var current = stack.pop(), raw = current.raw;
                try {
                    var rawLabel = String(raw.getText() || "");
                    var label = visibleLabel(rawLabel);
                    var description = visibleLabel(raw.getContentDescription());
                    var needsGeometry = !labelsOnly || labelsOnly === "entry" &&
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
                    var orderFields = !labelsOnly && readOrderFields;
                    var selectionFields = !labelsOnly && readSelectionFields;
                    var classValue = orderFields ? String(raw.getClassName() || "") : "";
                    var isCheckable = (orderFields || selectionFields) && raw.isCheckable();
                    var row = {
                        label: label,
                        rawLabel: rawLabel,
                        description: description,
                        classValue: classValue,
                        packageValue: String(raw.getPackageName() || ""),
                        isEnabled: needsGeometry && raw.isEnabled(),
                        isClickable: needsGeometry && raw.isClickable(),
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
                    for (var c = raw.getChildCount() - 1; c >= 0; c--) {
                        var child = raw.getChild(c);
                        if (child != null) stack.push({ raw: child, parent: row });
                    }
                } finally { raw.recycle(); }
            }
        } finally {
            // Release any queued native nodes even if the bounded read aborts.
            while (stack.length) stack.pop().raw.recycle();
        }
        screen = nodes;
        screenReadMode = labelsOnly;
        lastScreenReadStartedEpochMs = started;
        screenReads++;
        var elapsed = Date.now() - started;
        lastScreenReadMs = elapsed;
        if (labelsOnly) labelReads++;
        screenReadMs += elapsed;
        screenRootReadMs += rootReadMs;
        screenTreeCopyMs += Math.max(0, elapsed - rootReadMs);
        screenReadMaxMs = Math.max(screenReadMaxMs, elapsed);
    }

    function nowMono() { return Number(android.os.SystemClock.elapsedRealtime()); }
    function directedQueries(markers) {
        var started = nowMono(), root = auto.service.getRootInActiveWindow();
        if (root === null) stop("Active window unavailable");
        var rootMs = nowMono()-started, matches=0, exactMatches=0, counts=[];
        try {
            if (String(root.getPackageName() || "") !== PACKAGE) stop("Maoyan not in foreground");
            var rect = new android.graphics.Rect();
            for (var i=0;i<markers.length;i++) {
                var found = root.findAccessibilityNodeInfosByText(markers[i]);
                var exact=0;
                for (var j=0;j<found.size();j++) {
                    var node=found.get(j);
                    try {
                        // Read current properties; never keep native nodes between samples.
                        if (visibleLabel(node.getText()) === markers[i] ||
                                visibleLabel(node.getContentDescription()) === markers[i]) { exact++; exactMatches++; }
                        node.isEnabled();
                        node.getBoundsInScreen(rect); matches++;
                    } finally { node.recycle(); }
                }
                counts.push({marker:markers[i],matches:found.size(),exactMatches:exact});
            }
        } finally { root.recycle(); }
        return {rootMs:rootMs,totalMs:nowMono()-started,queries:markers.length,
            matches:matches,exactMatches:exactMatches,counts:counts};
    }
    function fullCopy(labelsOnly) {
        var started=nowMono();
        var beforeRoot=screenRootReadMs;
        readScreen(labelsOnly);
        var scene="detail_or_other";
        for(var i=0;i<screen.length;i++) {
            if(screen[i].label==="选择票档") scene="sku";
            if(screen[i].label==="确认购票"||screen[i].label==="确认购买") scene="order_review";
            if(screen[i].label==="支付订单") {scene="merchant_payment";break;}
        }
        var sample={rootMs:screenRootReadMs-beforeRoot,totalMs:nowMono()-started,nodes:screen.length,scene:scene};
        screen=null;
        return sample;
    }
    function median(values) {
        values.sort(function(a,b){return a-b;});
        var mid=Math.floor(values.length/2);
        return values.length%2?values[mid]:(values[mid-1]+values[mid])/2;
    }
    try {
        auto.waitFor(); auto.setMode("normal");
        benchPhase="return_to_maoyan_grace";
        console.log("只读测速：请在12秒内切回猫眼，程序不执行购票动作。");
        sleep(12000);
        var methods=["full_properties","labels_only","direct_3","direct_7"];
        var markers=READ_BENCH_MARKERS || ["选择票档","确认购票","支付订单","票档","数量","确认","立即支付"];
        if(markers.length!==7) stop("Benchmark marker configuration invalid");
        for (var round=0;round<6;round++) {
            var order=round%2?methods.slice().reverse():methods;
            for(var j=0;j<order.length;j++) {
                var method=order[j], sample;
                benchPhase=method;
                var methodStarted=nowMono();
                try {
                    if(method==="full_properties") sample=fullCopy(false);
                    else if(method==="labels_only") sample=fullCopy(true);
                    else sample=directedQueries(method==="direct_3"?markers.slice(0,3):markers);
                } catch(sampleError) {
                    if(sampleError.code!=="full_read_budget") throw sampleError;
                    sample={failed:true,errorCode:"full_read_budget",totalMs:nowMono()-methodStarted,
                        progress:result.lastFullReadProgress||null};
                    screen=null;
                }
                sample.method=method; sample.round=round; result.samples.push(sample);
            }
            sleep(60);
        }
        result.summary={};
        for(var m=0;m<methods.length;m++) {
            var matching=result.samples.filter(function(s){return s.method===methods[m]&&s.round>0;});
            var values=matching.filter(function(s){return !s.failed;}).map(function(s){return s.totalMs;});
            var failures=matching.length-values.length;
            // Do not present the successful subset as the typical completion
            // time when slow reads were stopped by the production budget.
            result.summary[methods[m]]={medianMs:failures?null:values.length?median(values):null,
                maxMs:failures?null:values.length?Math.max.apply(null,values):null,
                completedOnlyMedianMs:values.length?median(values):null,
                completedOnlyMaxMs:values.length?Math.max.apply(null,values):null,
                successes:values.length,failures:failures};
        }
        result.completed=true;
        result.comparisonComplete=result.samples.every(function(s){return !s.failed;});
    } catch(error) {
        result.completed=false;
        result.errorClass="read_or_service_unavailable";
        result.failedPhase=benchPhase;
        result.errorName=String(error.name || "NativeError");
        result.errorCode=String(error.code || "uncategorized");
        // No exception text or raw UI text is persisted.
    } finally {
        result.finishedEpochMs=Date.now();
        files.write("/sdcard/Download/qiangpiao-read-benchmark-result.json",JSON.stringify(result));
        console.log(JSON.stringify({readOnly:true,completed:result.completed,summary:result.summary||null}));
    }
})();
