"""Isolated adapter to a pinned, attributed HaTickets Mobile release."""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from .paths import DATA, ROOT
from .profiles import to_hatickets_config
from .engine.rehearsal_runtime import Phone, RunLock

UPSTREAM = ROOT / 'vendor' / 'HaTickets'


def interpret_result(mode, summary, code):
    """Only verified upstream outcomes may be shown as successful."""
    outcome = summary.get('outcome', 'summary_missing')
    if mode == 'live' and outcome == 'terminal_failure' and summary.get('terminal_reason') == 'submit_unverified':
        return 'unverified', '提交结果不确定。请先在大麦核对订单，不自动重试。'
    if code == 0 and mode == 'probe' and outcome == 'probe_ready':
        return 'inspected', '探测已完成；没有提交订单。'
    if code == 0 and mode == 'dryrun' and outcome == 'validation_ready':
        return 'inspected', '不提交联调已完成；请核对手机页面。'
    if code == 0 and mode == 'live' and outcome in ('order_submitted', 'order_pending_payment'):
        return 'submitted', '上游报告订单已提交；请在大麦核对订单状态并自行付款。'
    return 'error', f'流程结束，退出码 {code}；结果 {outcome}。详见本机运行摘要。'


def device_serial(profile):
    phone = Phone()
    lines = phone.cmd('devices').decode('utf-8','replace').splitlines()[1:]
    connected = [p[0] for row in lines if len(p := row.split()) >= 2 and p[1] == 'device']
    if profile['serial']:
        if profile['serial'] not in connected:
            raise RuntimeError('演出资料指定的 USB 手机未连接或未授权。')
        return profile['serial']
    if len(connected) != 1:
        raise RuntimeError('请连接一台已授权 USB 手机，或在资料中指定序列号。')
    return connected[0]


def run(controller, profile, mode):
    if not (UPSTREAM/'mobile/damai_app/__main__.py').exists():
        raise RuntimeError('HaTickets 源码未安装，见项目使用说明。')
    if mode not in ('probe','dryrun','live'):
        raise ValueError('运行方式无效。')
    with RunLock(DATA/'purchase.lock'):
        serial = device_serial(profile)
        config = to_hatickets_config(profile, serial, mode=mode)
        folder = DATA / 'hatickets-runs'
        folder.mkdir(parents=True, exist_ok=True)
        if mode == 'live':
            journal = folder / f'{profile["id"]}-live-attempt.json'
            try:
                with journal.open('x', encoding='utf-8') as file:
                    json.dump({'profile_id': profile['id'], 'state':'started',
                               'time_ns':time.time_ns()}, file)
                    file.flush()
                    os.fsync(file.fileno())
            except FileExistsError:
                raise RuntimeError('该演出已有一次正式尝试记录；请先在大麦核实订单。') from None
        run_id = str(time.time_ns())
        config_path = folder / f'{run_id}.json'
        result_path = folder / f'{run_id}-summary.json'
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
        env = os.environ.copy()
        env['HATICKETS_CONFIG_PATH'] = str(config_path)
        env['PYTHONPATH'] = str(UPSTREAM)
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PATH'] = str(ROOT/'tools/platform-tools') + os.pathsep + env.get('PATH','')
        # The subprocess executes the pinned upstream module; stdout is private.
        cmd = [sys.executable, '-m', 'mobile.damai_app', '--serial', serial,
               '--result-json', str(result_path)]
        controller.emit('started_clock', True)
        controller.emit('stage', 'preflight')
        controller.emit('status', {'probe':'准备只读探测。','dryrun':'准备不提交的流程联调。',
                                   'live':'准备按配置运行自动下单。'}[mode])
        controller.emit('log', f'HaTickets v0.5.0 已启动；模式 {mode}。')
        process = subprocess.Popen(cmd, cwd=UPSTREAM, env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding='utf-8', errors='replace',
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        controller.active_process = process
        try:
            # Read output off the main thread; never put user names or IDs in UI logs.
            lines = []
            def reader():
                for line in process.stdout:
                    lines.append(line)
                    if len(lines) > 300:
                        del lines[:100]
            output_thread = threading.Thread(target=reader, daemon=True)
            output_thread.start()
            while process.poll() is None:
                if controller.stopped.wait(.2):
                    process.terminate()
                    try: process.wait(timeout=3)
                    except subprocess.TimeoutExpired: process.kill()
                    break
            output_thread.join(timeout=2)
            if controller.stopped.is_set():
                return
            summary = json.loads(result_path.read_text(encoding='utf-8')) if result_path.exists() else {}
            outcome = summary.get('outcome', 'summary_missing')
            code = process.returncode
            controller.emit('outcome', interpret_result(mode, summary, code))
            controller.emit('log', f'上游结果：{outcome}；退出码：{code}。')
        finally:
            controller.active_process = None
