// Read-only diagnostic for AutoX accessibility on a Maoyan event detail page.
// It logs only package classification, node counts, and boolean token matches.
// It never logs screen text, clicks, screenshots, or personal information.
auto.waitFor();
toast("20 秒内请切回猫眼目标演出详情页");
sleep(20000);

var expectedPackage = "com.sankuai.movie";
var found = { total: 0, maoyan: 0, title: false, city: false, reservation: false };
var rootPackage = "无节点";

function scan(node, depth) {
    if (node == null || depth > 40 || found.total >= 2000) return;
    found.total++;
    var pkg = String(node.getPackageName() || "");
    if (pkg === expectedPackage) found.maoyan++;
    var value = String(node.getText() || "") + " " +
        String(node.getContentDescription() || "");
    if (value.indexOf("周杰伦") !== -1) found.title = true;
    if (value.indexOf("广州") !== -1) found.city = true;
    if (value.indexOf("已预约") !== -1) found.reservation = true;
    var childCount = node.getChildCount();
    for (var i = 0; i < childCount; i++) scan(node.getChild(i), depth + 1);
}

try {
    var root = auto.service.getRootInActiveWindow();
    if (root != null) {
        rootPackage = String(root.getPackageName() || "");
        scan(root, 0);
    }
    console.log("活动窗口包名：" + rootPackage);
    console.log("currentPackage：" + String(currentPackage()));
    console.log("节点总数：" + found.total + "，猫眼节点：" + found.maoyan);
    console.log("演出/城市/已预约：" + found.title + "/" +
        found.city + "/" + found.reservation);
} catch (ignored) {
    console.log("活动窗口诊断失败；未输出异常或屏幕内容。");
}

try {
    var windows = auto.service.getWindows();
    var count = windows == null ? 0 : windows.size();
    var maoyanWindows = 0;
    var packages = [];
    for (var w = 0; w < count; w++) {
        var windowRoot = windows.get(w).getRoot();
        var windowPackage = windowRoot == null ? "无节点" :
            String(windowRoot.getPackageName() || "");
        packages.push(windowPackage);
        if (windowPackage === expectedPackage) {
            maoyanWindows++;
        }
    }
    console.log("可读取窗口：" + count + "，其中猫眼窗口：" + maoyanWindows);
    console.log("窗口包名：" + packages.join(" / "));
} catch (ignoredAgain) {
    console.log("多窗口诊断不可用。");
}
