"""Open the selected Maoyan AutoX script on one authorized Android phone."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .autox_live_export import (REHEARSAL_OUTPUT, CONGESTION_PROBE_OUTPUT, CAPTURE_OUTPUT,
                                build_selection_capture_script, build_wechat_script,
                                build_congestion_probe_script,
                                build_selection_rehearsal_script,
                                build_fixed_coordinate_payment_script)
from .paths import DATA, ROOT


ADB = ROOT / 'tools' / 'platform-tools' / 'adb.exe'
OUTPUT = DATA / 'autox-maoyan-live.js'
PHONE_OUTPUT = '/sdcard/Download/autox-maoyan-live.js'
REHEARSAL_PHONE_OUTPUT = '/sdcard/Download/autox-maoyan-rehearsal.js'
AUTOX_ACTIVITY = 'org.autojs.autoxjs.v7/org.autojs.autojs.external.open.OpenIntentActivity'
BENCHMARK_SOURCE = ROOT / 'tools' / 'autox_first_click_benchmark.js'
BENCHMARK_OUTPUT = DATA / 'autox-first-click-benchmark.js'
FIXED_PATH_OUTPUT = DATA / 'autox-maoyan-fixed-path.js'


def open_fixed_coordinate_payment_on_phone(profile: dict, coordinates: dict, *,
                                           adb: Path = ADB, output: Path = FIXED_PATH_OUTPUT,
                                           run=None) -> None:
    """Open the separate one-shot fast path; never reuse the standard live file."""
    _open_script_on_phone(profile, build_fixed_coordinate_payment_script(profile, coordinates),
                          adb=adb, output=output,
                          phone_output='/sdcard/Download/autox-maoyan-fixed-path.js',
                          run=_run if run is None else run)


def _run(args: list[str]) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError('无法连接手机；请检查 USB 调试和连接状态。') from exc
    if result.returncode:
        raise ValueError('手机端打开失败；请确认 AutoX 已安装并允许 USB 调试。')
    return result.stdout


def open_live_on_phone(profile: dict, *, adb: Path = ADB, output: Path = OUTPUT,
                       run=_run) -> None:
    """Prepare and open the file; the user taps Run and returns to Maoyan."""
    _open_script_on_phone(profile, build_wechat_script(profile), adb=adb,
                          output=output, phone_output=PHONE_OUTPUT, run=run)


def open_rehearsal_on_phone(profile: dict, *, adb: Path = ADB,
                            output: Path = REHEARSAL_OUTPUT, run=_run) -> None:
    """Open a selection-only rehearsal without a live attempt lock."""
    _open_script_on_phone(profile, build_selection_rehearsal_script(profile),
                          adb=adb, output=output,
                          phone_output=REHEARSAL_PHONE_OUTPUT, run=run)


def open_congestion_probe_on_phone(profile: dict, *, adb: Path = ADB,
                                   output: Path = CONGESTION_PROBE_OUTPUT, run=_run) -> None:
    """Open the read-only congestion inspector; it never refreshes or orders."""
    _open_script_on_phone(profile, build_congestion_probe_script(profile), adb=adb,
                          output=output, phone_output='/sdcard/Download/autox-maoyan-congestion-probe.js', run=run)


def open_selection_capture_on_phone(profile: dict, *, adb: Path = ADB,
                                    output: Path = CAPTURE_OUTPUT, run=_run) -> None:
    """Open a read-only capture; no entry, selection or order action."""
    _open_script_on_phone(profile, build_selection_capture_script(profile), adb=adb,
                          output=output, phone_output='/sdcard/Download/autox-maoyan-selection-capture.js', run=run)


def open_first_click_benchmark_on_phone(profile: dict, *, adb: Path = ADB,
                                        output: Path = BENCHMARK_OUTPUT,
                                        source: Path = BENCHMARK_SOURCE, run=_run) -> None:
    """Open the separate AutoX fixture; no ticket profile is embedded or run."""
    if not source.is_file():
        raise ValueError('手机按钮测速工具缺失，请重新安装工作台。')
    _open_script_on_phone(profile, source.read_text(encoding='utf-8'), adb=adb,
                          output=output, phone_output='/sdcard/Download/autox-first-click-benchmark.js', run=run)


def _open_script_on_phone(profile: dict, script: str, *, adb: Path, output: Path,
                          phone_output: str, run) -> None:
    if not adb.is_file():
        raise ValueError('找不到手机连接工具。')
    lines = run([str(adb), 'devices']).splitlines()[1:]
    connected = [line.split()[0] for line in lines
                 if len(line.split()) >= 2 and line.split()[1] == 'device']
    requested = profile.get('serial') or None
    if requested:
        if requested not in connected:
            raise ValueError('指定手机未连接或 USB 调试未授权。')
        serial = requested
    elif len(connected) == 1:
        serial = connected[0]
    else:
        raise ValueError('请只连接一台已授权 USB 手机。')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.tmp')
    temporary.write_text(script, encoding='utf-8')
    temporary.replace(output)
    base = [str(adb), '-s', serial]
    run(base + ['push', str(output), phone_output])
    run(base + ['shell', 'am', 'start', '-a', 'android.intent.action.VIEW',
                '-d', 'file://' + phone_output, '-t', 'application/x-javascript',
                '-n', AUTOX_ACTIVITY])

