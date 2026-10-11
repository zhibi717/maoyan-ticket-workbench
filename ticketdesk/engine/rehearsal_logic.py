"""Recognize the detail-page entry before handing off to SKU selection."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Observation:
    kind: str
    message: str
    box: tuple | None = None


ENTRY_LABELS = frozenset(('立即购买', '立即预订', '立即抢票'))
BLOCKERS = ('验证码', '图片验证', '图形验证', '滑块', '安全验证',
            '请登录', '登录注册', '访问异常', '操作频繁')
ORDER_WORDS = ('提交订单', '确认订单', '立即提交', '确认购买',
               '待付款', '支付方式', '支付剩余', '支付倒计时')


def analyze(rows, size, title_tokens, entered=False):
    """Recognize the target detail page and its unique official purchase entry."""
    width, height = size
    labels = '\n'.join(text for text, _ in rows)
    if any(word in labels for word in BLOCKERS):
        return Observation('stop', '出现登录或安全验证，自动下单停止。')
    if any(word in labels for word in ORDER_WORDS):
        return Observation('stop', '已到订单或支付相关页面；请核实是否已有订单。')
    if not all(token in labels for token in title_tokens):
        return Observation('mismatch', '未确认庾澄庆广州站详情页，不点击。')
    if entered:
        return Observation('done', '已进入选票流程。')
    if any(word in labels for word in ('选择场次', '选择票档', '购买数量', '票张数')):
        return Observation('stop', '已在选票页；请重新打开目标演出详情页。')
    entry = [box for text, box in rows if text in ENTRY_LABELS and
             box[1] > height * .72 and box[2] > box[0] and box[3] > box[1]]
    if len(entry) == 1:
        return Observation('entry', '识别到唯一购票入口；准备进入选票流程。', entry[0])
    if len(entry) > 1:
        return Observation('stop', '购票入口有歧义，不点击。')
    if any(word in labels for word in ('预约', '预售', '即将开抢', '倒计时')):
        return Observation('wait', '仍是预约或倒计时状态，继续观察。')
    return Observation('unknown', '尚未识别购票入口，继续观察。')
