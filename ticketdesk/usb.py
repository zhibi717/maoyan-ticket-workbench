"""Bridge the existing USB worker into the unified app without a second GUI."""
import datetime as dt
import time
from .engine.purchase_app import PurchaseApp, TARGET, can_run, prepared_phase
from .engine.rehearsal_runtime import Phone, Reader, RunLock, CHINA_TZ
from .paths import DATA


def run_usb(controller, inspect):
    if controller.target != TARGET:
        raise RuntimeError('配置发生变化，请退出并重新启动后核对。')
    if inspect:
        with RunLock(DATA / 'purchase.lock'):
            controller.emit('status', '正在只读检查 USB；不打开页面、不点击。')
            phone = Phone()
            phone.connect()
            if not phone.foreground():
                raise RuntimeError('USB 已连接，但大麦不在前台或被通知栏遮挡。请打开目标页。')
            started = time.monotonic()
            image = phone.screen()
            captured = time.monotonic()
            if controller.stopped.is_set():
                return
            reader = Reader()
            rows = reader.read(image)
            phase = prepared_phase(rows, image, TARGET)
            controller.emit('image', image)
            controller.emit('log', f'截图 {captured-started:.2f}s；首次加载和识别 {time.monotonic()-captured:.2f}s。')
            controller.emit('stage', phase or 'unknown')
            controller.emit('outcome', ('inspected', f'USB 已连接；识别阶段：{phase or "未识别到目标页面"}。没有点击。'))
        return
    allowed, issue = can_run(dt.datetime.now(CHINA_TZ))
    if not allowed:
        raise RuntimeError(issue)
    controller.emit('started_clock', True)
    worker = PurchaseApp.__new__(PurchaseApp)
    worker.reader = None
    worker.stopped = controller.stopped
    def emit(kind, value):
        controller.emit(kind, value)
        if kind == 'log' and value.startswith('已发送一次提交订单指令'):
            controller.emit('submit_count', 1)
            controller.emit('stage', 'submitted')
    worker.emit = emit
    # Existing engine emits structured evidence-based outcome, never infer success from a click.
    worker.worker(True)
