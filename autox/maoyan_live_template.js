// EXPERIMENTAL AutoX v7 Rhino prototype. Do not enable until the read-only
// phone probe and real Maoyan sale/selection/order layouts have been checked.
// No fixed coordinates, blind retries, order submission, or payment actions.
var TARGET = null;
var ENABLE_EXPERIMENTAL_LIVE = false;
var PRECHECK_ONLY = false;
var SELECTION_REHEARSAL = false;
var FLOW_PRECHECK_ONLY = false;

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
    var storage;
    var phase = "启动";
    var marks = [];
    var timeOrigin = TARGET.saleEpochMs;
    var screen = null;
    var screenReads = 0;
    var screenReadMs = 0;
    function mark(name) {
        phase = name;
        marks.push(name + "=" + (Date.now() - timeOrigin) + "ms");
    }
    function report(message) {
        // Only fixed stage names and relative timings are emitted. No account,
        // attendee, or raw screen text is included in the diagnostic log.
        var line = message + "；阶段=" + phase + "；轨迹=" + marks.join(",");
        console.log(line);
        try { android.util.Log.i("QiangpiaoAutoX", line); } catch (ignored) {}
        if (FLOW_PRECHECK_ONLY) {
            try {
                files.write("/sdcard/Download/qiangpiao-readonly-result.json", JSON.stringify({
                    finishedEpochMs: Date.now(), readOnly: true,
                    elapsedMs: Date.now() - timeOrigin,
                    screenReads: screenReads, screenReadMs: screenReadMs,
                    message: line
                }));
            } catch (ignoredFile) {}
        }
    }

    function stop(message) { throw { safe: true, message: message }; }
    function readScreen() {
        // One fresh Android tree per decision. AutoX normal-mode selectors
        // fetch the tree again for every find/exists; dozens of absent-label
        // queries took 12.7 seconds on the actual detail screen.
        var started = Date.now();
        var root = auto.service.getRootInActiveWindow();
        if (root == null || String(root.getPackageName() || "") !== PACKAGE) {
            screen = null;
            if (root != null) root.recycle();
            stop("猫眼已不在前台，已停止。");
        }
        var nodes = [], stack = [{ raw: root, parent: null }];
        try {
            while (stack.length) {
                if (nodes.length >= 1000 || Date.now() - started > 2000) {
                    stop("当前页面读取过慢或控件过多，已停止。");
                }
                var current = stack.pop(), raw = current.raw;
                try {
                    var rect = new android.graphics.Rect();
                    raw.getBoundsInScreen(rect);
                    var row = {
                        label: String(raw.getText() || ""),
                        description: String(raw.getContentDescription() || ""),
                        classValue: String(raw.getClassName() || ""),
                        packageValue: String(raw.getPackageName() || ""),
                        isEnabled: raw.isEnabled(),
                        isChecked: raw.isChecked(),
                        isCheckable: raw.isCheckable(),
                        isSelected: raw.isSelected(),
                        area: { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom },
                        ancestor: current.parent
                    };
                    row.bounds = function () { return this.area; };
                    row.text = function () { return this.label; };
                    row.enabled = function () { return this.isEnabled; };
                    row.checked = function () { return this.isChecked; };
                    row.parent = function () { return this.ancestor; };
                    if (row.packageValue === PACKAGE) nodes.push(row);
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
        screenReads++;
        screenReadMs += Date.now() - started;
    }
    function checkPackage() { readScreen(); }
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
        return matching(function (node) {
            return node.label.indexOf(fragment) >= 0 || node.description.indexOf(fragment) >= 0;
        }).length > 0;
    }
    function hasAny(words) {
        var alternatives = [];
        for (var i = 0; i < words.length; i++) {
            alternatives.push(words[i].replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
        }
        var pattern = new RegExp(".*(?:" + alternatives.join("|") + ").*");
        return matchingPattern(pattern, true).length > 0;
    }
    function checkBlockers() {
        if (hasAny(CHALLENGE)) stop("猫眼出现验证，请在手机手动处理并核对订单。");
        if (hasAny(PAYMENT)) stop("猫眼出现支付页面，请在手机核对并自行付款。");
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
        if (b.left < 0 || b.top < 0 || b.right > device.width ||
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
    }
    function clickFresh(stage, resolveNodes, verifyContext) {
        verifyContext();
        var node = unique(resolveNodes(), stage);
        safeButtonBounds(node, stage);
        // The matched label is often a non-clickable child of a button.
        // A touch at the freshly verified label bounds reaches its parent.
        var b = node.bounds();
        var x = Math.floor((b.left + b.right) / 2);
        var y = Math.floor((b.top + b.bottom) / 2);
        if (!click(x, y)) stop(stage + "点击结果不明确，已停止；请在手机核对。");
        screen = null;
    }
    function verifyDetail(requireReservation, reuseDecision) {
        if (!reuseDecision) checkPackage();
        checkBlockers();
        if (!has(TARGET.titleToken) || !has(TARGET.city)) {
            stop("当前猫眼页面未同时显示目标演出与城市，已停止。");
        }
        var reservationPattern = /^\s*已\s*预\s*约\s*$/;
        if (requireReservation &&
                matchingPattern(reservationPattern, true).length === 0) {
            stop("目标详情页未显示已预约状态，已停止。");
        }
    }
    function priceNodes() {
        var raw = String(TARGET.amount);
        var formatted = raw.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
        var nodes = containedTextNodes([raw, formatted]), result = [];
        for (var i = 0; i < nodes.length; i++) {
            var label = String(nodes[i].text() || "").replace(/[,，]/g, "");
            var numbers = label.match(/\d+(?:\.\d{1,2})?/g) || [];
            if (label.length <= 35 && numbers.length === 1 &&
                    Number(numbers[0]) === TARGET.amount &&
                    !/(缺货|售罄|不可售|登记)/.test(label) && candidateAvailable(nodes[i])) result.push(nodes[i]);
        }
        return result;
    }
    function candidateAvailable(node) {
        if (!node.enabled()) return false;
        // Some builds put the stock badge in a sibling instead of in the
        // price label. Inspect only compact ancestors belonging to this card.
        var badges = matchingPattern(/.*(?:缺货登记|售罄|不可售).*/, false);
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
        var alternatives = [];
        for (var j = 0; j < TARGET.dateLabels.length; j++) {
            alternatives.push(TARGET.dateLabels[j].replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
        }
        var pattern = new RegExp(".*(?:" + alternatives.join("|") + ").*");
        var nodes = matchingPattern(pattern, false);
        var result = [];
        for (var i = 0; i < nodes.length; i++) {
            var label = String(nodes[i].text() || "");
            if (label.length <= 30 && label.indexOf(TARGET.titleToken) < 0) result.push(nodes[i]);
        }
        return result;
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
        if (!amountMatched) return false;
        var quantities = matchingPattern(/.*\d+\s*张.*/, false);
        var one = false;
        for (var j = 0; j < quantities.length; j++) {
            var quantity = String(quantities[j].text() || "").match(/(\d+)\s*张/);
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
        var bytes = new java.lang.String(value).getBytes("UTF-8");
        var digest = java.security.MessageDigest.getInstance("SHA-256").digest(bytes);
        var output = "";
        for (var i = 0; i < digest.length; i++) {
            var hex = (digest[i] & 255).toString(16);
            output += hex.length === 1 ? "0" + hex : hex;
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
        var matches = [];
        for (var i = 0; i < labels.length; i++) {
            var node = labels[i], b = node.bounds();
            if (b.top >= region.top && b.bottom <= region.bottom &&
                    nameDigest(String(node.text() || "").replace(/^\s+|\s+$/g, "")) === TARGET.attendeeNameSha256) {
                matches.push(node);
            }
        }
        unique(matches, "目标观演人");
        return unique(orderCheckboxes(region), "唯一观演人勾选框");
    }
    function ensureOnlyAttendeeSelected() {
        if (pageKind() !== "order" || !orderIdentityMatches()) {
            stop("订单信息与目标演出不一致，已停止。");
        }
        var box = targetAttendeeBox();
        if (box.checked()) return "已勾选";
        if (!box.enabled()) stop("观演人勾选框不可用，已停止。");
        safeButtonBounds(box, "观演人勾选框");
        var b = box.bounds();
        if (!click(Math.floor((b.left + b.right) / 2),
                   Math.floor((b.top + b.bottom) / 2))) {
            stop("观演人勾选结果不明确，已停止。");
        }
        mark("点击唯一观演人");
        var selectedDeadline = Date.now() + 600;
        while (Date.now() < selectedDeadline) {
            if (pageKind() !== "order") stop("勾选后订单页已变化，请在手机核对。");
            if (targetAttendeeBox().checked()) return "已自动勾选";
            sleep(40);
        }
        stop("未确认观演人已勾选，已停止；请在手机核对。");
    }
    function finishAtOrder() {
        if (SELECTION_REHEARSAL) {
            report("演练已到订单页；请在手机核对，未提交或支付。");
            return;
        }
        var attendee = ensureOnlyAttendeeSelected();
        report("已进入订单页，唯一观演人" + attendee + "；请在手机核对并自行处理支付。");
    }
    function targetDateAlreadySelected() {
        var target = unique(dateNodes(), "目标日期");
        return nodeAlreadySelected(target);
    }
    function nodeAlreadySelected(target) {
        var b = target.bounds();
        // Some Maoyan builds expose the red selected card through Android's
        // selected flag. If they do not, the safe fallback is to tap the date.
        var marked = matching(function (node) { return node.isSelected; });
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
        unique(priceNodes(), "目标票档");
        unique(exactNodes(["1张", "1 张", "1份", "1 份"]), "1 张数量");
        var button = unique(confirmationNodes(), "票档确认按钮");
        safeButtonBounds(button, "票档确认");
    }
    function waitForKind(timeoutMs) {
        var deadline = Date.now() + timeoutMs;
        while (Date.now() < deadline) {
            var kind = pageKind();
            if (kind !== "unknown") return kind;
            sleep(80);
        }
        stop("页面转换后未识别出选票或订单页，已停止。");
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
            checkPackage();
            if (nameDigest("qiangpiao") !== "571ca4acfff4ab88e8e95a21b5ef78cc614c1ca65de69db58a108fdd4bb671f2") {
                stop("手机端姓名摘要校验异常，已停止只读检查。");
            }
            var probeKind = pageKind(true);
            if (probeKind === "order") {
                var probeBox = targetAttendeeBox();
                mark("订单页只读检查完成");
                report("只读检查通过：目标订单与观演人已匹配；勾选=" + probeBox.checked() + "；未点击。");
            } else if (probeKind === "sku") {
                var probePrices = priceNodes();
                mark("选票页只读检查完成");
                report("只读检查完成：目标日期已识别，可用目标票档数量=" + probePrices.length + "；未点击。");
            } else {
                verifyDetail(false, true);
                mark("详情页只读检查完成");
                report("只读检查通过：目标演出与城市已匹配；未点击。读取次数=" + screenReads + "，累计读取=" + screenReadMs + "ms");
            }
            return;
        }
        checkPackage();
        var now = Date.now();
        if (!PRECHECK_ONLY && !SELECTION_REHEARSAL &&
                (now < TARGET.saleEpochMs - 600000 || now > TARGET.saleEpochMs + 1200000)) {
            stop("当前不在目标开售前 10 分钟到开售后 20 分钟窗口，已停止。");
        }
        if (!SELECTION_REHEARSAL) storage = storages.create("qiangpiao.autox.maoyan.prototype.v1");
        if (!SELECTION_REHEARSAL && storage.contains(TARGET.attemptKey)) {
            stop("这场演出已有一次手机端选档尝试；请核对猫眼页面。");
        }
        verifyDetail(!SELECTION_REHEARSAL && now < TARGET.saleEpochMs);
        if (PRECHECK_ONLY) {
            console.log("猫眼手机端预检通过：演出、城市和预约状态已识别；未点击。");
            return;
        }
        while (!SELECTION_REHEARSAL && Date.now() < TARGET.saleEpochMs) {
            checkPackage();
            var remaining = TARGET.saleEpochMs - Date.now();
            sleep(Math.min(remaining > 30000 ? 1000 :
                           remaining > 3000 ? 200 : 50, remaining));
        }
        mark(SELECTION_REHEARSAL ? "选档演练开始" : "开售等待结束");

        // A crowded on-sale page can lag behind the announced start time.
        // Keep looking for a single real purchase control without tapping
        // a guessed location.
        var entryDeadline = Date.now() + (SELECTION_REHEARSAL ? 5000 : 60000);
        var entryNode = null;
        while (Date.now() < entryDeadline) {
            checkPackage();
            var possibleEntry = entryNodes();
            if (possibleEntry.length > 1) stop("购票入口出现多个候选，已停止。");
            if (possibleEntry.length === 1) {
                entryNode = possibleEntry[0];
                break;
            }
            sleep(80);
        }
        if (!entryNode) stop("开售后未找到唯一购票入口，已停止。");
        mark("发现购票入口");

        // Re-read the live control and mark the attempt before the first action.
        entryNode = unique(entryNodes(), "购票入口");
        safeButtonBounds(entryNode, "购票入口");
        if (!SELECTION_REHEARSAL) claimAttempt();
        clickFresh("购票入口", entryNodes,
            function () { verifyDetail(false); });
        mark("点击购票入口");

        var kind = waitForKind(5000);
        mark(kind === "sku" ? "识别选票页" : "识别订单页");
        if (kind === "order") {
            finishAtOrder();
            return;
        }
        if (kind === "sku") {
            if (!targetDateAlreadySelected()) {
                clickFresh("目标日期", dateNodes, function () {
                    if (pageKind() !== "sku") stop("目标日期选择页已变化，已停止。");
                });
                mark("点击目标日期");
                sleep(100);
            } else mark("沿用已选日期");
            if (pageKind() === "order") {
                finishAtOrder();
                return;
            }
            else {
                if (!has("请选择票档")) {
                    if (!nodeAlreadySelected(unique(priceNodes(), "已选目标票档"))) {
                        stop("票档页已有选择，但未确认是目标金额，请在手机核对。");
                    }
                    mark("沿用已选票档");
                } else {
                    clickFresh("目标票档", priceNodes, function () {
                        if (pageKind() !== "sku") stop("目标票档选择页已变化，已停止。");
                    });
                    mark("点击目标票档");
                }
                var choiceDeadline = Date.now() + 700;
                while (Date.now() < choiceDeadline) {
                    checkPackage();
                    checkBlockers();
                    if (!has("请选择票档")) break;
                    sleep(40);
                }
                if (has("请选择票档")) {
                    stop("目标票档未被选中或已不可售，已停止。");
                }
                var selectedPage = pageKind();
                if (selectedPage !== "sku" && selectedPage !== "order") {
                    stop("票档点击后页面状态不明确，已停止；请在手机核对。");
                }
                mark("票档按钮已更新");
                if (selectedPage === "order") {
                    finishAtOrder();
                    return;
                }
                if (SELECTION_REHEARSAL) {
                    report("选档演练已点中目标票档；未点击确认，请在猫眼核对。");
                    return;
                }
                clickFresh("票档确认", confirmationNodes,
                    verifySelectedSku);
                mark("点击票档确认");
                var orderDeadline = Date.now() + 4000;
                while (Date.now() < orderDeadline) {
                    if (pageKind() === "order") {
                        mark("到达订单页");
                        finishAtOrder();
                        return;
                    }
                    sleep(80);
                }
                stop("已点击票档确认，但未确认进入订单页；请在手机核对，程序不会重复点击。");
                return;
            }
        }
        stop("未识别到目标选票页，已停止。");
    } catch (error) {
        // Unexpected errors can include raw UI values. Print only our own
        // fixed safe messages, never an exception's original text or stack.
        report(error && error.safe ? error.message :
            "手机端原型遇到未知页面或权限异常，已停止；请在猫眼核对。");
    }
})();
