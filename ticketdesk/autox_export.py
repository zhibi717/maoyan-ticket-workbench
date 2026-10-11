"""Export a selected Maoyan target into a self-contained, read-only AutoX probe."""
from __future__ import annotations

import json
from pathlib import Path

from .maoyan import normal_date
from .paths import DATA, ROOT
from .profiles import ProfileStore


TEMPLATE = ROOT / 'autox' / 'maoyan_probe_template.js'
OUTPUT = DATA / 'autox-maoyan-probe.js'
SELECTION = DATA / 'selected-profile.txt'
PLACEHOLDER = 'var TARGET = null;'


def target_from_profile(profile: dict) -> dict:
    """Return event-only fields. Attendees, device IDs, and accounts stay on PC."""
    if profile['platform'] != 'maoyan':
        raise ValueError('当前选中的演出不是猫眼。')
    if len(profile['users']) != 1:
        raise ValueError('猫眼试用流程当前只支持 1 张，请核对观演人数。')
    title_token = profile['keyword'].split()[0]
    if len(title_token) < 2:
        raise ValueError('演出关键词过短，无法安全检查。')
    date = normal_date(profile['date'])
    if date is None:
        raise ValueError('目标日期无法识别。')
    month, day = map(int, date.split('.'))
    labels = [
        profile['event_time'][:10],
        f'{profile["event_time"][:4]}.{month:02d}.{day:02d}',
        f'{profile["event_time"][:4]}/{month:02d}/{day:02d}',
        f'{month}月{day}日', f'{month}月{day:02d}日',
        f'{month:02d}.{day:02d}', f'{month:02d}/{day:02d}',
        f'{month:02d}-{day:02d}', f'{month}.{day}',
    ]
    return {
        'package': 'com.sankuai.movie',
        'titleToken': title_token,
        'city': profile['city'],
        'dateLabels': list(dict.fromkeys(labels)),
        'amount': profile['amount'],
    }


def build_script(profile: dict, template: str | None = None) -> str:
    target = target_from_profile(profile)
    template = TEMPLATE.read_text(encoding='utf-8') if template is None else template
    if template.count(PLACEHOLDER) != 1:
        raise RuntimeError('AutoX 模板配置标记异常。')
    encoded = json.dumps(target, ensure_ascii=True, separators=(',', ':'))
    return template.replace(PLACEHOLDER, f'var TARGET = {encoded};', 1)


def export_selected(*, selection: Path = SELECTION, store: ProfileStore | None = None,
                    output: Path = OUTPUT) -> Path:
    if not selection.exists():
        raise ValueError('请先在工作台保存并选中一场猫眼演出。')
    identifier = selection.read_text(encoding='utf-8').strip()
    profile = (store or ProfileStore()).get(identifier)
    if profile is None:
        raise ValueError('已选演出资料不存在，请重新选择。')
    script = build_script(profile)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.tmp')
    temporary.write_text(script, encoding='utf-8')
    temporary.replace(output)
    return output


if __name__ == '__main__':
    try:
        print(f'已导出只读检测脚本：{export_selected()}')
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from None
