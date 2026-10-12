"""Inspect the connected phone without printing raw UI or personal data."""
from __future__ import annotations

import json
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ticketdesk.profiles import ProfileStore


def main():
    adb = ROOT / 'tools' / 'platform-tools' / 'adb.exe'
    def run(args):
        return subprocess.run([str(adb), *args], capture_output=True, check=True, timeout=20).stdout
    devices = run(['devices']).decode('utf-8').splitlines()[1:]
    serials = [line.split()[0] for line in devices if len(line.split()) >= 2 and line.split()[1] == 'device']
    if len(serials) != 1:
        raise ValueError('Exactly one authorized USB device is required.')
    base = ['-s', serials[0]]
    remote = '/data/local/tmp/qiangpiao-' + uuid.uuid4().hex + '.xml'
    try:
        run(base + ['shell', 'uiautomator', 'dump', remote])
        nodes = list(ET.fromstring(run(base + ['exec-out', 'cat', remote])).iter('node'))
    finally:
        run(base + ['shell', 'rm', '-f', remote])
    profile = ProfileStore().get((ROOT / 'data' / 'selected-profile.txt').read_text(encoding='utf-8').strip())
    labels = [node.get('text', '') + ' ' + node.get('content-desc', '') for node in nodes]
    def has(token):
        return any(token in label for label in labels)
    if '--probe-menu' in sys.argv:
        print(json.dumps({'probe_file': has('autox-maoyan-flow-probe.js'),
                          'run_bounds': [n.get('bounds') for n in nodes if n.get('text') == '运行脚本']},
                         ensure_ascii=True))
        return
    result = {
        'maoyan_window': any(n.get('package') == 'com.sankuai.movie' for n in nodes),
        'target_title': has(profile['keyword'].split()[0]),
        'target_city': has(profile['city']),
        'reserved': any(''.join(label.split()) == '已预约' for label in labels),
        'sku_page': has('选择票档'),
        'order_page': has('确认购票') or has('确认购买'),
        'attendee_section': has('实名观演人'),
        'target_attendee_text': any(n.get('text', '').strip() == profile['users'][0].strip() for n in nodes),
        'checkable_nodes': sum(n.get('checkable') == 'true' for n in nodes),
        'node_count': len(nodes),
    }
    print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('Read-only phone inspection did not complete; no UI data printed.') from None
