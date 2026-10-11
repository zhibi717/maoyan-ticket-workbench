"""USB screenshot/OCR primitives and single-submit guard for Guangzhou."""
import datetime as dt
import io
import json
import msvcrt
import os
import re
import subprocess
import time
from pathlib import Path

import numpy as np
from PIL import Image
from rapidocr import RapidOCR


from ticketdesk.paths import ROOT, DATA
ADB = ROOT / 'tools/platform-tools/adb.exe'
CHINA_TZ = dt.timezone(dt.timedelta(hours=8))


def same_content(before, after):
    """Require the visible app body to be stable before a cropped recheck.

    The status bar can change its clock/battery without altering the order.
    Any meaningful change elsewhere sends the caller back to full-screen OCR.
    """
    if before.size != after.size:
        return False
    top = round(before.height*.07)
    left = np.asarray(before.crop((0, top, before.width, before.height)),
                      dtype=np.int16)
    right = np.asarray(after.crop((0, top, after.width, after.height)),
                       dtype=np.int16)
    changed = np.max(np.abs(left-right), axis=2) > 25
    return float(np.mean(changed)) < .002


class StaleFrameError(RuntimeError):
    """The action must be re-observed before it can be clicked."""


class SubmissionJournal:
    """Durable at-most-once guard, including after a process restart."""
    def __init__(self, path):
        self.path = Path(path)

    def attempted(self):
        return self.path.exists()

    def claim(self, fingerprint):
        with self.path.open('x', encoding='utf-8') as file:
            json.dump({'state': 'attempted', 'fingerprint': fingerprint,
                       'time': dt.datetime.now(dt.timezone.utc).isoformat()}, file)
            file.flush()
            os.fsync(file.fileno())

    def complete(self):
        data = json.loads(self.path.read_text(encoding='utf-8'))
        data['state'] = 'payment_page_observed'
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(data), encoding='utf-8')
        temp.replace(self.path)


class RunLock:
    def __init__(self, path):
        self.path = Path(path)
        self.file = None

    def __enter__(self):
        self.file = self.path.open('a+b')
        self.file.seek(0)
        if self.path.stat().st_size == 0:
            self.file.write(b'0')
            self.file.flush()
        self.file.seek(0)
        try:
            msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.file.close()
            self.file = None
            raise RuntimeError('已有另一个自动购票任务运行。')
        return self

    def __exit__(self, *args):
        if self.file:
            self.file.seek(0)
            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            self.file.close()


class Phone:
    def __init__(self):
        self.serial = None
        self.observed_at = 0

    def cmd(self, *args, timeout=15):
        prefix = [str(ADB)] + (['-s', self.serial] if self.serial else [])
        result = subprocess.run(prefix + list(args), capture_output=True, timeout=timeout,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError('USB 指令失败；请检查手机连接和调试授权。')
        return result.stdout

    def connect(self):
        lines = self.cmd('devices').decode().splitlines()[1:]
        devices = [s.split()[0] for s in lines if len(s.split()) >= 2 and s.split()[1] == 'device']
        if len(devices) != 1:
            raise RuntimeError('请只连接一台已允许 USB 调试的手机。')
        self.serial = devices[0]

    def focused_window(self):
        # The window manager reveals overlays such as NotificationShade that
        # can cover Damai even while its activity remains resumed.
        state = self.cmd('shell', 'dumpsys', 'window').decode('utf-8', 'replace')
        focused = [line for line in state.splitlines() if 'mCurrentFocus=' in line]
        return focused[0] if len(focused) == 1 else ''

    def foreground(self):
        return 'cn.damai/' in self.focused_window()

    def screen(self):
        image = Image.open(io.BytesIO(self.cmd('exec-out', 'screencap', '-p'))).convert('RGB')
        self.observed_at = time.monotonic()
        return image

    def refresh_if_stale(self, expected_image):
        if time.monotonic() - self.observed_at <= 4:
            return
        if expected_image is None or not same_content(expected_image, self.screen()):
            raise StaleFrameError('画面已变化，未点击；重新观察页面。')

    def tap(self, box, stopped, expected_image=None):
        if stopped.is_set():
            return False
        self.refresh_if_stale(expected_image)
        if not self.foreground():
            raise RuntimeError('大麦不在前台，未执行点击。')
        if stopped.is_set():
            return False
        x1, y1, x2, y2 = box
        self.cmd('shell', 'input', 'tap', str((x1+x2)//2), str((y1+y2)//2))
        return True


class Reader:
    def __init__(self):
        self.engine = RapidOCR(params={'Det.limit_type': 'max', 'Global.use_cls': False,
            'Global.log_level': 'error', 'EngineConfig.onnxruntime.intra_op_num_threads': 2,
            'EngineConfig.onnxruntime.inter_op_num_threads': 1})

    def read(self, image, bottom=False):
        y0 = int(image.height * float(bottom)) if bottom else 0
        crop = image.crop((0, y0, image.width, image.height))
        scale = min(1, 540/crop.width)
        scaled = crop.resize((round(crop.width*scale), round(crop.height*scale)))
        result = self.engine(np.asarray(scaled)[:, :, ::-1].copy())
        rows = []
        if result.txts is not None:
            for label, box, score in zip(result.txts, result.boxes, result.scores):
                if score < .90:
                    continue
                x1, y1 = np.floor(box.min(axis=0)/scale).astype(int)
                x2, y2 = np.ceil(box.max(axis=0)/scale).astype(int)
                rows.append((re.sub(r'\s+', '', label),
                             (max(0, int(x1)), max(0, int(y1)+y0),
                              min(image.width, int(x2)), min(image.height, int(y2)+y0))))
        return rows

    def read_rect(self, image, rect):
        """OCR one crop and return boxes in original screen coordinates."""
        x1, y1, x2, y2 = rect
        local = self.read(image.crop(rect))
        return [(text, (box[0]+x1, box[1]+y1, box[2]+x1, box[3]+y1))
                for text, box in local]

    def read_order_recheck(self, image):
        """Recheck the observed order layout with two short OCR passes.

        The first full-screen read already checked all blockers. Callers only
        use this on a second, visually stable frame; other layouts fall back
        to full-screen OCR.
        """
        width, height = image.size
        upper = self.read_rect(image, (0, round(height*.16),
                                      width, round(height*.66)))
        bottom = self.read_rect(image, (0, round(height*.86), width, height))
        return upper + bottom
