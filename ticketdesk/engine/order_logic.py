"""Fail-closed review of one official Damai order before a single submit tap."""
from dataclasses import dataclass
from decimal import Decimal
import datetime as dt
import hashlib
import re

import numpy as np

from .selection_logic import clean, date_label, quantity_one, tier_label


@dataclass(frozen=True)
class OrderDecision:
    kind: str
    message: str
    box: tuple | None = None
    fingerprint: str = ''


def values(rows, label, pattern):
    found = []
    for text, box in rows:
        text = clean(text)
        match = re.fullmatch(label + r':?' + pattern, text)
        if match:
            found.append(match.group(1))
        elif re.fullmatch(label + r':?', text):
            _, y1, x2, y2 = box
            for candidate, other in rows:
                same_line = abs((other[1] + other[3] - y1 - y2) / 2) < max(22, (y2-y1)*.75)
                if same_line and x2 <= other[0] <= x2 + 700:
                    adjacent = re.fullmatch(pattern, clean(candidate))
                    if adjacent:
                        found.append(adjacent.group(1))
    return set(found)


def selected_viewer_mark(image, name_box, id_box):
    """Require the selected check near the unique viewer, not just a name."""
    x1, x2 = round(image.width*.84), round(image.width*.98)
    y1, y2 = max(0, name_box[1]-10), min(image.height, id_box[3]+10)
    pixels = np.asarray(image.crop((x1, y1, x2, y2)).convert('RGB'))
    if pixels.size == 0:
        return False
    orange = ((pixels[:, :, 0] > 180) & (pixels[:, :, 1] < 160) &
              (pixels[:, :, 2] < 150) &
              (pixels[:, :, 0].astype(np.int16) >
               pixels[:, :, 1].astype(np.int16) + 60))
    return int(orange.sum()) >= max(50, round(orange.size*.003))


def viewer_facts(rows, image):
    name_pattern = r'(?:观演人|持票人|入场人|姓名):?([\u4e00-\u9fff·]{2,16})'
    id_pattern = (r'(?:身份证|证件号|证件号码):?'
                  r'([0-9A-Za-z]{2,6}[*＊•·]{3,}[0-9A-Za-z]{2,4})')
    inline_names = [(match.group(1), box) for text, box in rows
                    for match in [re.fullmatch(name_pattern, clean(text))]
                    if match]
    inline_ids = [(match.group(1), box) for text, box in rows
                  for match in [re.fullmatch(id_pattern, clean(text))]
                  if match]
    if len(inline_names) == 1 and len(inline_ids) == 1:
        name, name_box = inline_names[0]
        masked_id, id_box = inline_ids[0]
        if selected_viewer_mark(image, name_box, id_box):
            return name, masked_id
        return None

    # The current Damai review puts the selected viewer's name and masked ID
    # on separate lines below 实名观演人, without inline field labels.
    heading = [box for text, box in rows if clean(text) == '实名观演人']
    delivery = [box for text, box in rows if clean(text) == '配送方式']
    if len(heading) != 1 or len(delivery) != 1:
        return None
    top, bottom = heading[0][3], delivery[0][1]
    if not top < bottom:
        return None
    people = [(clean(text), box) for text, box in rows
              if top < box[1] < bottom and box[0] < image.width*.5 and
              re.fullmatch(r'[\u4e00-\u9fff·]{2,16}', clean(text))]
    identity = []
    for text, box in rows:
        if not top < box[1] < bottom:
            continue
        match = re.fullmatch(
            r'(?:身份证|证件号|证件号码):?([0-9A-Za-z]{2,6}[*＊•·]{3,}[0-9A-Za-z]{2,4})',
            clean(text))
        if match:
            identity.append((match.group(1), box))
    if len(people) != 1 or len(identity) != 1:
        return None
    name, name_box = people[0]
    masked_id, id_box = identity[0]
    if not selected_viewer_mark(image, name_box, id_box):
        return None
    return name, masked_id


def order_evidence(rows, image, target):
    labels = '\n'.join(clean(text) for text, _ in rows)
    if not all(token in labels for token in target['title_tokens']):
        return None, '订单演出不是已指定的庾澄庆广州站，未提交。'
    event = dt.datetime.fromisoformat(target['event_time'])
    date_rows = [clean(text) for text, _ in rows
                 if date_label(clean(text), event.month, event.day,
                               event.strftime('%H:%M'), event.year)]
    if len(date_rows) != 1:
        return None, f'订单场次未唯一核实为 {event.month} 月 {event.day} 日 {event:%H:%M}。'
    price = target['ticket_tier_cny']
    tier_rows = [clean(text) for text, _ in rows if tier_label(clean(text), price)]
    ticket_line = [clean(text) for text, _ in rows if re.fullmatch(
        rf'¥?{price}(?:\.00)?票档[×xX]1张', clean(text))]
    if len(tier_rows) + len(ticket_line) != 1:
        return None, f'订单票档未唯一核实为 {price} 元。'
    if not ticket_line and not quantity_one(rows):
        return None, '订单数量未明确核实为 1 张。'
    totals = values(rows, r'(?:合计|实付款|应付|应付金额|订单金额|总计)',
                    r'(?:¥)?(\d+(?:\.\d{1,2})?)(?:元)?')
    if not totals:
        bottom_amounts = [match.group(1) for text, box in rows
                          if box[1] >= image.height*.88
                          for match in [re.fullmatch(
                              r'¥(\d+(?:\.\d{1,2})?)明细', clean(text))]
                          if match]
        if len(bottom_amounts) == 1:
            totals = set(bottom_amounts)
    if not totals or any(Decimal(amount) != Decimal(price) for amount in totals):
        return None, f'订单实付金额未明确核实为 {price} 元。'
    viewer = viewer_facts(rows, image)
    if viewer is None:
        return None, '未唯一识别到 1 位观演人及其遮盖的证件号。'
    # OCR may count the privacy mask differently at a smaller crop. The
    # visible prefix and suffix, not the number or glyph of mask marks,
    # identify the same preselected viewer across independent reads.
    masked_id = re.sub(r'[*＊•·]{3,}', '*', viewer[1])
    private_digest = hashlib.sha256((viewer[0] + '|' + masked_id).encode('utf-8')).hexdigest()
    fingerprint = hashlib.sha256(
        f"{target['item_id']}|{target['event_time']}|{price}|1|{private_digest}".encode('utf-8')
    ).hexdigest()
    return fingerprint, ''


def decide_order(rows, image, target, submitted=False):
    labels = '\n'.join(clean(text) for text, _ in rows)
    payment_page = (any(clean(text) == '大麦' for text, _ in rows) and
                    any(clean(text) == '付款' for text, _ in rows) and
                    any(clean(text) in (f"¥{target['ticket_tier_cny']}.00",
                                             f"¥{target['ticket_tier_cny']}")
                        for text, _ in rows))
    verification_words = ('验证码', '滑块', '安全验证', '图片验证', '图形验证')
    if any(word in labels for word in verification_words +
           ('请登录', '登录注册', '访问异常', '操作频繁')):
        if submitted and any(word in labels for word in verification_words):
            return OrderDecision('verify', '大麦要求图片验证；请在手机上完成，程序只等待结果。')
        return OrderDecision('stop', '需要登录或验证，自动下单已停止。')
    if submitted:
        if payment_page:
            return OrderDecision('done', '看到大麦对应金额的付款页；请本人核对并完成付款。')
        if any(word in labels for word in ('待付款', '待支付', '订单已提交')):
            return OrderDecision('done', '官方页面显示待付款／订单已提交，请在大麦核实并自行付款。')
        if any(word in labels for word in ('支付方式', '支付倒计时', '支付剩余')):
            return OrderDecision('stop', '疑似进入支付页，但未识别到明确订单状态；请在大麦核实。')
        return OrderDecision('wait', '已发送一次提交指令，等待官方订单结果；不会再点击。')
    if payment_page:
        return OrderDecision('stop', '已经在付款页，请自行核对；程序没有本次提交记录。')
    if any(word in labels for word in ('待付款', '待支付', '订单已提交')):
        return OrderDecision('stop', '页面已有订单状态，本次还未提交；请先在大麦核实。')
    if any(word in labels for word in ('排队中', '正在排队', '正在提交', '正在加载')):
        return OrderDecision('wait', '等待官方页面处理。')
    buttons = [box for text, box in rows
               if clean(text) in ('提交订单', '确认订单', '立即提交')
               and box[1] >= image.height*.72]
    if len(buttons) != 1:
        return OrderDecision('wait', '尚未唯一识别提交订单按钮。')
    fingerprint, issue = order_evidence(rows, image, target)
    if issue:
        return OrderDecision('stop', issue)
    return OrderDecision('submit', '订单场次、480 元、1 张、观演人及实付金额均匹配；准备提交一次。',
                         buttons[0], fingerprint)
