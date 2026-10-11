"""Build the Maoyan phone flow for the selected, locally stored event."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path

from .autox_export import PLACEHOLDER, SELECTION, target_from_profile
from .paths import DATA, ROOT
from .profiles import ProfileStore


TEMPLATE = ROOT / 'autox' / 'maoyan_live_template.js'
LIVE_OUTPUT = DATA / 'autox-maoyan-live.js'
REHEARSAL_OUTPUT = DATA / 'autox-maoyan-rehearsal.js'
FLOW_PROBE_OUTPUT = DATA / 'autox-maoyan-flow-probe.js'


def build_locked_prototype(profile: dict, template: str | None = None) -> str:
    """Build a one-ticket script with a local name digest, never a plaintext name."""
    event = target_from_profile(profile)
    identifier = profile.get('id')
    if not isinstance(identifier, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', identifier):
        raise ValueError('演出资料缺少有效 ID，无法限制重复尝试。')
    attendee = profile['users'][0]
    if not isinstance(attendee, str) or not attendee.strip():
        raise ValueError('唯一观演人姓名无效。')
    sale = dt.datetime.fromisoformat(profile['sale_time'])
    if sale.tzinfo is None or sale.utcoffset() is None:
        raise ValueError('开售时间必须包含时区。')
    sale_ms = int(sale.timestamp() * 1000)
    target = {
        **event,
        'attendeeCount': 1,
        'attendeeNameSha256': hashlib.sha256(attendee.strip().encode('utf-8')).hexdigest(),
        'saleEpochMs': sale_ms,
        'attemptKey': f'{identifier}:{sale_ms}',
    }
    template = TEMPLATE.read_text(encoding='utf-8') if template is None else template
    if (template.count(PLACEHOLDER) != 1 or
            template.count('var ENABLE_EXPERIMENTAL_LIVE = false;') != 1 or
            template.count('var SELECTION_REHEARSAL = false;') != 1):
        raise RuntimeError('手机端原型模板安全开关异常。')
    encoded = json.dumps(target, ensure_ascii=True, separators=(',', ':'))
    return template.replace(PLACEHOLDER, f'var TARGET = {encoded};', 1)


def build_live_script(profile: dict, template: str | None = None) -> str:
    """Enable one guarded attempt; the template still enforces the sale window."""
    script = build_locked_prototype(profile, template)
    gate = 'var ENABLE_EXPERIMENTAL_LIVE = false;'
    if script.count(gate) != 1 or script.count('var PRECHECK_ONLY = false;') != 1:
        raise RuntimeError('手机端正式流程安全开关异常。')
    return script.replace(gate, 'var ENABLE_EXPERIMENTAL_LIVE = true;', 1)


def build_selection_rehearsal_script(profile: dict, template: str | None = None) -> str:
    """Select the intended tier on a real page, without confirming or ordering."""
    script = build_live_script(profile, template)
    gate = 'var SELECTION_REHEARSAL = false;'
    if script.count(gate) != 1 or script.count('var PRECHECK_ONLY = false;') != 1:
        raise RuntimeError('手机端选档演练安全开关异常。')
    return script.replace(gate, 'var SELECTION_REHEARSAL = true;', 1)


def build_flow_probe_script(profile: dict, template: str | None = None) -> str:
    """Inspect any supported flow page with the live selectors and no clicks."""
    script = build_live_script(profile, template)
    gate = 'var FLOW_PRECHECK_ONLY = false;'
    if script.count(gate) != 1:
        raise RuntimeError('手机端只读流程检查开关异常。')
    return script.replace(gate, 'var FLOW_PRECHECK_ONLY = true;', 1)


def export_live_selected(*, selection: Path = SELECTION,
                         store: ProfileStore | None = None,
                         output: Path = LIVE_OUTPUT) -> Path:
    if not selection.exists():
        raise ValueError('请先在工作台保存并选中一场猫眼演出。')
    identifier = selection.read_text(encoding='utf-8').strip()
    profile = (store or ProfileStore()).get(identifier)
    if profile is None:
        raise ValueError('已选演出资料不存在，请重新选择。')
    script = build_live_script(profile)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.tmp')
    temporary.write_text(script, encoding='utf-8')
    temporary.replace(output)
    return output


if __name__ == '__main__':
    try:
        print(f'已生成猫眼 AutoX 本机运行文件：{export_live_selected()}')
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from None
