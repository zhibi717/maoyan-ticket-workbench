"ui";
// Local AutoX fixture only. No Maoyan, orders, account or payment actions.
var EXPECTED_PACKAGE = "org.autojs.autoxjs.v7";
var READY = "QIANGPIAOTESTREADY";
var WAIT = "QIANGPIAOTESTWAIT";
var records = [], currentTrial = null;
var rows = "";
for (var row = 0; row < 28; row++) rows += '<text text="Local fixture row ' + row + '" h="26" />';
ui.layout('<vertical padding="16"><text text="Local first-click benchmark" textSize="20sp" />' +
    '<text id="status" text="Local test only. No ticket or payment actions." />' +
    '<scroll h="0" layout_weight="1"><vertical>' + rows + '</vertical></scroll>' +
    '<button id="testButton" text="QiangpiaoTestWAIT" w="240" layout_gravity="right" /></vertical>');
ui.testButton.on("click", function () {
    if (currentTrial !== null && currentTrial.receivedAt === null) {
        currentTrial.receivedAt = Number(android.os.SystemClock.uptimeMillis());
    }
});
function now() { return Number(android.os.SystemClock.uptimeMillis()); }
function candidate(mode) {
    var started = now(), root = auto.service.getRootInActiveWindow();
    if (root === null) return null;
    var answer = null, rect = new android.graphics.Rect(), count = 0;
    try {
        if (String(root.getPackageName()) !== EXPECTED_PACKAGE) throw new Error("Fixture lost foreground");
        if (mode === "native") {
            var list = root.findAccessibilityNodeInfosByText(READY);
            try {
                for (var i = 0; i < list.size(); i++) {
                    var node = list.get(i);
                    count++;
                    if (String(node.getPackageName()) === EXPECTED_PACKAGE &&
                            String(node.getText()) === READY && node.isEnabled()) {
                        if (answer !== null) throw new Error("Ambiguous fixture control");
                        node.getBoundsInScreen(rect);
                        answer = {x: Math.floor((rect.left + rect.right) / 2),
                                  y: Math.floor((rect.top + rect.bottom) / 2)};
                    }
                }
            } finally { for (var j = 0; j < list.size(); j++) list.get(j).recycle(); }
        } else {
            var stack = [root];
            root = null;
            try {
                while (stack.length) {
                    var raw = stack.pop();
                    try {
                        count++;
                        var label = String(raw.getText() || "");
                        var desc = String(raw.getContentDescription() || "");
                        var pkg = String(raw.getPackageName());
                        if (pkg === EXPECTED_PACKAGE && (label === READY || desc === READY) && raw.isEnabled()) {
                            if (answer !== null) throw new Error("Ambiguous fixture control");
                            raw.getBoundsInScreen(rect);
                            answer = {x: Math.floor((rect.left + rect.right) / 2),
                                      y: Math.floor((rect.top + rect.bottom) / 2)};
                        }
                        for (var c = raw.getChildCount() - 1; c >= 0; c--) {
                            var child = raw.getChild(c);
                            if (child !== null) stack.push(child);
                        }
                    } finally { raw.recycle(); }
                }
            } finally { while (stack.length) stack.pop().recycle(); }
        }
    } finally { if (root !== null) root.recycle(); }
    if (answer !== null) {
        answer.readMs = now() - started;
        answer.nodeCount = count;
    }
    return answer;
}
threads.start(function () {
    try {
        if (auto.service === null) throw new Error("Accessibility unavailable");
        auto.setMode("normal");
        sleep(1000);
        // Alternate methods to reduce warm-up/order bias.
        for (var run = 0; run < 24; run++) {
            var trial = {mode: run % 4 === 0 || run % 4 === 3 ? "scan" : "native", touch: run % 4 >= 2 ? "press1" : "click", readyAt: null,
                         sentAt: null, receivedAt: null, clickCallMs: null};
            currentTrial = trial;
            ui.run(function () {
                ui.status.setText("Trial " + (run + 1) + "/24: " + trial.mode + " " + trial.touch);
                ui.testButton.setText(WAIT);
                ui.testButton.setEnabled(false);
                ui.testButton.postDelayed(new java.lang.Runnable({run: function () {
                    ui.testButton.setText(READY);
                    ui.testButton.setEnabled(true);
                    trial.readyAt = now();
                }}), 350);
            });
            var deadline = now() + 4000;
            while (now() < deadline) {
                var readStarted = now(), found = candidate(trial.mode);
                if (found !== null) {
                    trial.sentAt = now();
                    trial.readMs = found.readMs;
                    trial.nodeCount = found.nodeCount;
                    var clicked = trial.touch === "press1" ? press(found.x, found.y, 1) : click(found.x, found.y);
                    trial.clickCallMs = now() - trial.sentAt;
                    if (!clicked) throw new Error("Fixture click failed");
                    break;
                }
                var remaining = 20 - (now() - readStarted);
                if (remaining > 0) sleep(remaining);
            }
            var callbackDeadline = now() + 1500;
            while (trial.sentAt !== null && trial.receivedAt === null && now() < callbackDeadline) sleep(5);
            trial.readyToSendMs = trial.readyAt !== null && trial.sentAt !== null ? trial.sentAt - trial.readyAt : null;
            trial.readyToReceiveMs = trial.readyAt !== null && trial.receivedAt !== null ? trial.receivedAt - trial.readyAt : null;
            records.push(trial);
            if (trial.sentAt === null || trial.receivedAt === null) throw new Error("Fixture transition timed out");
            sleep(80);
        }
        var result = {finishedEpochMs: Date.now(), localFixtureOnly: true, trials: records};
        files.write("/sdcard/Download/qiangpiao-first-click-benchmark.json", JSON.stringify(result));
        ui.run(function () { ui.status.setText("Complete: 24 local trials. No ticket/payment actions."); });
    } catch (error) {
        files.write("/sdcard/Download/qiangpiao-first-click-benchmark.json", JSON.stringify({
            finishedEpochMs: Date.now(), localFixtureOnly: true, failed: true, error: String(error), trials: records}));
        ui.run(function () { ui.status.setText("Local benchmark stopped. No ticket/payment actions."); });
    }
});
