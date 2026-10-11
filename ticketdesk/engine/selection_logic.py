"""Conservative selection of one exact SKU before independent order review."""
from dataclasses import dataclass
import datetime as dt
import re

import numpy as np


BLOCKERS = ('验证码', '图片验证', '图形验证', '滑块', '安全验证',
            '请登录', '登录注册', '访问异常', '操作频繁')
ORDER_MARKERS = ('提交订单', '确认订单', '立即提交', '确认购买', '待付款', '支付方式', '支付剩余')


@dataclass(frozen=True)
class Step:
    kind: str
    message: str
    box: tuple | None = None
    key: str = ''


def clean(label):
    return re.sub(r'\s+', '', label).replace('￥', '¥').replace('：', ':')


def orange_text(image, box):
    pixels = np.asarray(image.crop(box).convert('RGB'))
    if pixels.size == 0:
        return False
    red, green, blue = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
    return float(np.mean((red > 185) & (green > 50) & (green < 180) & (blue < 105))) > .065


def unique_box(rows, height, predicate, y_min=0, y_max=1):
    boxes = [box for text, box in rows if predicate(clean(text)) and
             box[1] >= height*y_min and box[3] <= height*y_max]
    return boxes[0] if len(boxes) == 1 else None


def date_label(label, month, day, hour_minute, year=2026):
    visible_years = re.findall(r'20\d{2}', label)
    if visible_years and any(int(value) != year for value in visible_years):
        return False
    date = bool(re.search(
        rf'(?:{month}月0?{day}日|{year}[-./年]0?{month}[-./月]0?{day}日?)',
        label))
    return date and hour_minute in label


def tier_label(label, price):
    # A standalone SKU, not a range, marketing text, or an order total.
    return bool(re.fullmatch(rf'(?:看台|内场|普通|票档:)?¥?{price}(?:\.00)?元?', label))


def visible_tier_prices(rows, height):
    prices = []
    for text, box in rows:
        if not (height*.08 <= box[1] and box[3] <= height*.88):
            continue
        match = re.fullmatch(r'(?:看台|内场|普通|票档:)?¥?(\d{2,5})(?:\.00)?元?', clean(text))
        if match:
            prices.append(int(match.group(1)))
    return prices


def quantity_one(rows):
    labels = [clean(t) for t, _ in rows]
    explicit = [t for t in labels if re.fullmatch(r'(?:购买数量|票张数|数量):?\d{1,2}(?:张)?', t)]
    if len(explicit) == 1:
        return bool(re.fullmatch(r'(?:购买数量|票张数|数量):?1(?:张)?', explicit[0]))
    if explicit:
        return False
    labels = [(clean(t), box) for t, box in rows if re.fullmatch(r'(?:购买数量|票张数|数量):?', clean(t))]
    if len(labels) != 1:
        return False
    _, label_box = labels[0]
    beside = [clean(t) for t, box in rows if box[0] >= label_box[2] and
              # Damai puts the count at the far right of the quantity row.
              # Require a single standalone count on the same row instead of
              # an arbitrary short horizontal distance from its label.
              abs((box[1]+box[3]-label_box[1]-label_box[3])/2) <
              max(70, (label_box[3]-label_box[1])*1.25) and
              re.fullmatch(r'\d{1,2}(?:张)?', clean(t))]
    return len(beside) == 1 and beside[0] in ('1', '1张')


def is_sku_page(rows, image, target):
    """Recognize the target's SKU sheet after the purchase entry changes."""
    labels = '\n'.join(clean(text) for text, _ in rows)
    if not all(token in labels for token in target['title_tokens']):
        return False
    if any(word in labels for word in BLOCKERS + ORDER_MARKERS):
        return False
    if not all(any(clean(text).startswith(word) for text, _ in rows)
               for word in ('场次', '票档', '数量')):
        return False
    event = dt.datetime.fromisoformat(target['event_time'])
    has_date = any(date_label(clean(text), event.month, event.day,
                              event.strftime('%H:%M'), event.year)
                   for text, _ in rows)
    has_tier = any(tier_label(clean(text), target['ticket_tier_cny'])
                   for text, _ in rows)
    has_confirm = unique_box(rows, image.height,
                             lambda t: t in ('确定', '确认选择', '下一步'), .72, 1)
    return has_date and has_tier and has_confirm is not None


def sold_out_near(rows, box):
    center = (box[1] + box[3]) / 2
    return any(any(word in clean(text) for word in ('售罄', '无票', '缺货')) and
               abs((other[1] + other[3])/2 - center) < 55 and
               box[0] - 60 <= other[0] <= box[2] + 400
               for text, other in rows)


def select_step(rows, image, target, phase):
    """One safe action at a time. Caller must re-observe after each action."""
    labels = '\n'.join(clean(text) for text, _ in rows)
    if any(word in labels for word in BLOCKERS):
        return Step('stop', '出现登录或验证，自动下单停止。')
    if any(word in labels for word in ORDER_MARKERS):
        return Step('done', '已到订单或支付相关页面；等待独立订单核对。')
    if phase not in ('date', 'date_verify', 'tier', 'tier_verify', 'quantity', 'confirm'):
        return Step('stop', '未知选票阶段，自动下单停止。')
    performance = dt.datetime.fromisoformat(target['event_time'])
    month, day, time = performance.month, performance.day, performance.strftime('%H:%M')
    price = target['ticket_tier_cny']
    if not isinstance(price, int) or not (1 <= price <= 100000):
        return Step('stop', '未设置有效票档，自动下单停止。')
    if target.get('quantity') != 1:
        return Step('stop', '只支持明确的 1 张票。')
    date_box = unique_box(rows, image.height,
                          lambda t: date_label(t, month, day, time,
                                               performance.year), .08, .88)
    tier_box = unique_box(rows, image.height, lambda t: tier_label(t, price), .08, .88)
    if phase in ('date', 'date_verify'):
        if date_box is None:
            return Step('stop', '未唯一识别 11 月 14 日 19:00 场次。')
        if sold_out_near(rows, date_box):
            return Step('stop', '目标场次无票，自动下单停止。')
        if orange_text(image, date_box):
            return Step('next', '目标场次已选中。', key='tier')
        if phase == 'date_verify':
            return Step('wait', '等待场次选中反馈。')
        return Step('tap', '选择 11 月 14 日 19:00 场次。', date_box, 'date')
    if date_box is None or not orange_text(image, date_box):
        return Step('stop', '目标场次未保持选中，自动下单停止。')
    prices = visible_tier_prices(rows, image.height)
    if prices and min(prices) < price:
        return Step('stop', f'页面出现比 {price} 元更低的票档，需重新核对最低档。')
    if phase in ('tier', 'tier_verify'):
        if tier_box is None:
            return Step('stop', f'未唯一识别 {price} 元票档。')
        if sold_out_near(rows, tier_box):
            return Step('stop', f'{price} 元最低档无票，不改选更贵档位。')
        if orange_text(image, tier_box):
            return Step('next', f'{price} 元票档已选中。', key='quantity')
        if phase == 'tier_verify':
            return Step('wait', '等待票档选中反馈。')
        return Step('tap', f'选择 {price} 元票档。', tier_box, 'tier')
    if tier_box is None or not orange_text(image, tier_box):
        return Step('stop', '目标票档未保持选中，自动下单停止。')
    if sold_out_near(rows, tier_box):
        return Step('stop', f'{price} 元最低档无票，自动下单停止。')
    if not quantity_one(rows):
        return Step('stop', '未明确识别数量为 1 张，自动下单停止。')
    if phase == 'quantity':
        return Step('next', '场次、票档、1 张均已核实。', key='confirm')
    # A SKU sheet's neutral confirmation may advance to an order review.
    # Never touch a purchase or submit button whose effect is ambiguous.
    confirm = unique_box(rows, image.height, lambda t: t in ('确定', '确认选择', '下一步'), .72, 1)
    if confirm is None:
        return Step('done', '选票信息已核实；确认按钮含义不明，停在此页。')
    return Step('tap', '确认选票并进入订单核对页。', confirm, 'sku-confirm')
