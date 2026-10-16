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
CONGESTION_PROBE_OUTPUT = DATA / 'autox-maoyan-congestion-probe.js'
CAPTURE_OUTPUT = DATA / 'autox-maoyan-selection-capture.js'


def masked_name_hashes(name: str) -> list[str]:
    """Accept common star masks without exporting any readable name fragment."""
    variants = set()
    for start in range(len(name)):
        for end in range(start + 1, len(name) + 1):
            if start == 0 and end == len(name):
                continue
            for symbol in ('*', '＊'):
                for count in {1, end - start}:
                    variants.add(name[:start] + symbol * count + name[end:])
    return sorted(hashlib.sha256(value.encode('utf-8')).hexdigest() for value in variants)


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
    fallback = profile.get('available_tier_fallback', False)
    if not isinstance(fallback, bool):
        raise ValueError('回流票替补开关无效。')
    date_mode = profile.get('date_selection_mode', 'select_target')
    if date_mode not in ('select_target', 'keep_current'):
        raise ValueError('日期选择模式无效。')
    target = {
        **event,
        'attendeeCount': 1,
        'attendeeNameSha256': hashlib.sha256(attendee.strip().encode('utf-8')).hexdigest(),
        'attendeeMaskedNameSha256': masked_name_hashes(attendee.strip()),
        'saleEpochMs': sale_ms,
        'attemptKey': f'{identifier}:{sale_ms}',
        'selectionSource': profile.get('selection_source', 'workbench'),
        'tierLabel': profile['price'],
        'availableTierFallback': fallback,
        'dateSelectionMode': date_mode,
        'selectionKey': 'selection:' + hashlib.sha256(json.dumps(
            [identifier, sale_ms, event, profile['price']], sort_keys=True
        ).encode('utf-8')).hexdigest(),
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


def build_selection_confirmation_rehearsal_script(profile: dict, template: str | None = None) -> str:
    """Choose one ticket and confirm to order review; never submit or pay."""
    script = build_selection_rehearsal_script(profile, template)
    gate = 'var CONFIRM_SELECTION_REHEARSAL = false;'
    if script.count(gate) != 1 or 'var SUBMIT_TO_PAYMENT = false;' not in script:
        raise RuntimeError('手机端确认选档演练开关异常。')
    return script.replace(gate, 'var CONFIRM_SELECTION_REHEARSAL = true;', 1)


def build_payment_script(profile: dict, template: str | None = None) -> str:
    """Submit an explicit order-creation action once, then stop at cashier."""
    script = build_live_script(profile, template)
    gate = 'var SUBMIT_TO_PAYMENT = false;'
    if script.count(gate) != 1:
        raise RuntimeError('手机端提交到支付页开关异常。')
    return script.replace(gate, 'var SUBMIT_TO_PAYMENT = true;', 1)


def build_fixed_coordinate_payment_script(profile: dict, coordinates: dict,
                                          template: str | None = None) -> str:
    """Timed first taps, then guarded order and Maoyan-to-WeChat submission."""
    if profile.get('available_tier_fallback', False):
        raise ValueError('回流票替补须使用正常选档脚本，不能使用固定坐标实验。')
    width = coordinates.get('width')
    height = coordinates.get('height')
    gap = coordinates.get('gapMs')
    if width != 1080 or height != 2376 or not isinstance(gap, int) or not 1 <= gap <= 1000:
        raise ValueError('固定坐标模式仅支持已核对的 1080×2376 手机和 1–1000 毫秒间隔。')
    points = {}
    for stage in ('entry', 'tier', 'confirm'):
        point = coordinates.get(stage)
        if (not isinstance(point, (list, tuple)) or len(point) != 2 or
                not all(isinstance(value, int) and not isinstance(value, bool) for value in point) or
                not 0 <= point[0] < width or not 0 <= point[1] < height):
            raise ValueError(f'{stage} 坐标无效。')
        points[stage] = list(point)
    script = build_wechat_script(profile, template)
    gates = ('var FAST_FIXED_PATH = false;', 'var FAST_COORDS = null;')
    if any(script.count(gate) != 1 for gate in gates):
        raise RuntimeError('固定坐标模式开关异常。')
    config = {'width': width, 'height': height, 'gapMs': gap, **points}
    return (script.replace(gates[0], 'var FAST_FIXED_PATH = true;', 1)
            .replace(gates[1], f'var FAST_COORDS = {json.dumps(config, separators=(",", ":"))};', 1))


def build_wechat_script(profile: dict, template: str | None = None) -> str:
    """Create an order, launch the verified WeChat route once, never pay."""
    script = build_payment_script(profile, template)
    gate = 'var OPEN_WECHAT_PAYMENT = false;'
    if script.count(gate) != 1:
        raise RuntimeError('手机端微信跳转开关异常。')
    script = script.replace(gate, 'var OPEN_WECHAT_PAYMENT = true;', 1)
    selected_gate = 'var USER_PRESELECTED = false;'
    if script.count(selected_gate) != 1:
        raise RuntimeError('手机端用户预选开关异常。')
    return script.replace(selected_gate, 'var USER_PRESELECTED = true;', 1)


def build_merchant_payment_script(profile: dict, template: str | None = None) -> str:
    """Create the configured order once and stop at the merchant cashier."""
    script = build_wechat_script(profile, template)
    gate = 'var STOP_AT_MERCHANT_PAYMENT = false;'
    if script.count(gate) != 1:
        raise RuntimeError('手机端商户付款页停止开关异常。')
    return script.replace(gate, 'var STOP_AT_MERCHANT_PAYMENT = true;', 1)


def build_flow_probe_script(profile: dict, template: str | None = None) -> str:
    """Inspect any supported flow page with the live selectors and no clicks."""
    script = build_live_script(profile, template)
    gate = 'var FLOW_PRECHECK_ONLY = false;'
    if script.count(gate) != 1:
        raise RuntimeError('手机端只读流程检查开关异常。')
    return script.replace(gate, 'var FLOW_PRECHECK_ONLY = true;', 1)


def build_congestion_probe_script(profile: dict, template: str | None = None) -> str:
    """Inspect the real congestion dialog and refresh bounds without any action."""
    script = build_flow_probe_script(profile, template)
    gate = 'var CONGESTION_PRECHECK_ONLY = false;'
    if script.count(gate) != 1:
        raise RuntimeError('手机端拥堵只读检查开关异常。')
    return script.replace(gate, 'var CONGESTION_PRECHECK_ONLY = true;', 1)


def build_selection_capture_script(profile: dict, template: str | None = None) -> str:
    """Record only explicit selected date/tier/quantity; never tap or lock an attempt."""
    script = build_flow_probe_script(profile, template)
    gate = 'var CAPTURE_SELECTION_ONLY = false;'
    if script.count(gate) != 1:
        raise RuntimeError('手机端记录预选开关异常。')
    return script.replace(gate, 'var CAPTURE_SELECTION_ONLY = true;', 1)


def export_live_selected(*, selection: Path = SELECTION,
                         store: ProfileStore | None = None,
                         output: Path = LIVE_OUTPUT) -> Path:
    if not selection.exists():
        raise ValueError('请先在工作台保存并选中一场猫眼演出。')
    identifier = selection.read_text(encoding='utf-8').strip()
    profile = (store or ProfileStore()).get(identifier)
    if profile is None:
        raise ValueError('已选演出资料不存在，请重新选择。')
    script = build_wechat_script(profile)
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

