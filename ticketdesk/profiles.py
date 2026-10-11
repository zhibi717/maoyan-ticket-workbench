"""Validated event profiles used by the general mobile adapter."""
import datetime as dt
import json
import os
import re
import uuid
from pathlib import Path
from .paths import DATA

PROFILE_FILE = DATA / 'profiles.json'


def validate_profile(value):
    if not isinstance(value, dict):
        raise ValueError('演出资料格式错误。')
    def field(key, max_len=120):
        text = value.get(key, '')
        if not isinstance(text, str) or not text.strip() or len(text) > max_len:
            raise ValueError(f'请填写有效的{key}。')
        return text.strip()
    title = field('title')
    platform = value.get('platform', 'damai')
    if platform not in ('damai', 'maoyan'):
        raise ValueError('请选择大麦或猫眼。')
    keyword = field('keyword')
    city = field('city', 40)
    date = field('date', 80)
    event_time_text = field('event_time', 50)
    price = field('price', 80)
    selection_source = value.get('selection_source', 'workbench')
    if selection_source not in ('phone', 'workbench'):
        raise ValueError('请选择手机预选或工作台填写的票档来源。')
    if not re.search(r'\d', price):
        raise ValueError('票档请填写所选平台页面原文和金额。')
    amount_match = re.search(r'(?<!\d)(\d{2,5})(?!\d)', price)
    amount = int(amount_match.group(1)) if amount_match else None
    if amount is None or not 1 <= amount <= 100000:
        raise ValueError('票档金额无法识别。')
    raw_users = value.get('users')
    if isinstance(raw_users, str):
        users = [s.strip() for s in re.split(r'[,，\n]', raw_users) if s.strip()]
    elif isinstance(raw_users, list) and all(isinstance(x, str) for x in raw_users):
        users = [s.strip() for s in raw_users]
    else:
        raise ValueError('观演人须填写姓名列表。')
    if not 1 <= len(users) <= 6 or any(not 1 <= len(s) <= 30 for s in users) or len(set(users)) != len(users):
        raise ValueError('请填写 1–6 位不同观演人，每人一行。')
    sale_text = field('sale_time', 50)
    try:
        sale = dt.datetime.fromisoformat(sale_text)
        if sale.tzinfo is None or sale.utcoffset() is None:
            raise ValueError
        event_time = dt.datetime.fromisoformat(event_time_text)
        if event_time.tzinfo is None or event_time.utcoffset() is None:
            raise ValueError
    except ValueError:
        raise ValueError('开售时间须含时区，例如 2026-10-12T15:00:00+08:00。') from None
    index = value.get('price_index', 0)
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index <= 50:
        raise ValueError('票档兜底序号须为 0–50 的整数。')
    serial = value.get('serial', '')
    if not isinstance(serial, str) or len(serial) > 100:
        raise ValueError('USB 序列号无效。')
    target_title = value.get('target_title', '')
    target_venue = value.get('target_venue', '')
    if not all(isinstance(v, str) and len(v) <= 150 for v in (target_title, target_venue)):
        raise ValueError('标题或场馆过长。')
    identifier = value.get('id') or uuid.uuid4().hex
    if not isinstance(identifier, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', identifier):
        raise ValueError('演出资料 ID 无效。')
    return dict(id=identifier, platform=platform, title=title, keyword=keyword, city=city, date=date,
                event_time=event_time.isoformat(),
                price=price, amount=amount, users=users, sale_time=sale.isoformat(),
                selection_source=selection_source,
                price_index=index, serial=serial.strip(), target_title=target_title.strip(),
                target_venue=target_venue.strip())


class ProfileStore:
    def __init__(self, path=PROFILE_FILE):
        self.path = Path(path)

    def all(self):
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(data, list):
            raise ValueError('演出资料文件损坏。')
        return [validate_profile(item) for item in data]

    def get(self, identifier):
        return next((p for p in self.all() if p['id'] == identifier), None)

    def save(self, raw):
        profile = validate_profile(raw)
        profiles = self.all()
        profiles = [p for p in profiles if p['id'] != profile['id']]
        profiles.append(profile)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, self.path)
        return profile


def to_hatickets_config(profile, serial, *, mode):
    """Map a selected event to the pinned upstream schema."""
    if mode not in ('probe', 'dryrun', 'live'):
        raise ValueError('未知运行方式。')
    if profile['platform'] != 'damai':
        raise ValueError('HaTickets 只能用于大麦资料。')
    if not serial:
        raise ValueError('只连接一台已授权 USB 手机，或在资料中填写其序列号。')
    return dict(serial=serial, keyword=profile['keyword'], city=profile['city'],
                date=profile['date'], price=profile['price'],
                price_index=profile['price_index'], users=profile['users'],
                target_title=profile['target_title'] or None,
                target_venue=profile['target_venue'] or None,
                sell_start_time=profile['sale_time'],
                probe_only=mode == 'probe', if_commit_order=mode == 'live',
                auto_navigate=True, countdown_lead_ms=3000,
                wait_cta_ready_timeout_ms=0, rush_mode=False,
                rush_skip_session=False, rush_skip_price_dump=False,
                rush_aggressive_retry=False, fast_retry_count=2,
                fast_retry_interval_ms=120)
