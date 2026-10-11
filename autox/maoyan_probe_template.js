// AutoX v7 Rhino (.js) read-only Maoyan page probe.
// Generate a configured copy with: python -m ticketdesk.autox_export
// This script never clicks, scrolls, submits, captures screenshots, or writes files.
var TARGET = null;

(function () {
    if (!TARGET || TARGET.package !== "com.sankuai.movie") {
        console.log("请先从电脑端导出猫眼只读检测脚本。");
        return;
    }

    // AutoX may open Android accessibility settings if its service is disabled.
    // The user switches to the Maoyan event page manually after the prompt.
    auto.waitFor();
    toast("20 秒内请手动打开猫眼目标演出页");
    sleep(20000);

    try {
        var root = auto.service.getRootInActiveWindow();
        var onMaoyan = root != null &&
            String(root.getPackageName() || "") === TARGET.package;
        console.log("活动窗口是猫眼：" + (onMaoyan ? "是" : "否"));
        if (!onMaoyan) {
            console.log("当前不是猫眼窗口，已停止只读检测。");
            return;
        }

        function visible(fragment) {
            return textContains(fragment).packageName(TARGET.package).exists() ||
                descContains(fragment).packageName(TARGET.package).exists();
        }

        function anyVisible(fragments) {
            for (var i = 0; i < fragments.length; i++) {
                if (visible(fragments[i])) return true;
            }
            return false;
        }

        function report(label, found) {
            console.log(label + "：" + (found ? "找到" : "未找到"));
        }

        var entryTexts = [
            "立即预订", "立即预定", "立即购票", "立即抢购",
            "立即抢票", "特惠购票", "立即购买"
        ];
        var entryPattern = /^(?:立即预订|立即预定|立即购票|立即抢购|立即抢票|特惠购票|立即购买)$/;
        function entryVisibleRegex() {
            return textMatches(entryPattern).packageName(TARGET.package).exists() ||
                descMatches(entryPattern).packageName(TARGET.package).exists();
        }
        var eventTimes = [];
        var entryTimes = [];
        var regexTimes = [];
        var titleFound = false;
        var cityFound = false;
        var entryFound = false;
        for (var sample = 0; sample < 12; sample++) {
            var eventStart = Date.now();
            var titleNow = visible(TARGET.titleToken);
            var cityNow = visible(TARGET.city);
            eventTimes.push(Date.now() - eventStart);
            var entryStart = Date.now();
            var entryNow = anyVisible(entryTexts);
            entryTimes.push(Date.now() - entryStart);
            var regexStart = Date.now();
            var regexNow = entryVisibleRegex();
            regexTimes.push(Date.now() - regexStart);
            titleFound = titleFound || titleNow;
            cityFound = cityFound || cityNow;
            entryFound = entryFound || entryNow || regexNow;
        }

        function timingSummary(times) {
            var sorted = times.slice().sort(function (a, b) { return a - b; });
            var middle = (sorted[5] + sorted[6]) / 2;
            return "最短 " + sorted[0] + " / 中位 " + middle +
                " / 最长 " + sorted[11] + " 毫秒";
        }

        console.log("猫眼只读检测（仅检查当前屏幕）");
        report("演出关键词", titleFound);
        report("城市", cityFound);
        report("已预约字样", anyVisible(["已预约", "已 预 约"]));
        report("预约设置页", visible("更新抢票提醒"));
        report("目标日期文本", anyVisible(TARGET.dateLabels));
        report("目标金额文本", visible(String(TARGET.amount)));
        report("购票入口", entryFound);
        console.log("12 次演出关键词与城市查询：" + timingSummary(eventTimes));
        console.log("12 次购票入口查询：" + timingSummary(entryTimes));
        console.log("12 次正则购票入口查询：" + timingSummary(regexTimes));
        console.log("检测结束：未点击、未下单；文字出现不代表票档已选中。");
        toast("只读检测完成，请回 AutoX 查看日志");
    } catch (error) {
        // Never print raw accessibility data or exception text: either could
        // contain names or account details shown elsewhere on the phone.
        console.log("只读检测未完成；请核对无障碍服务和猫眼当前页面。");
    }
})();
