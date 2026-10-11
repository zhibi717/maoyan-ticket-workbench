"""Desktop entry point; no purchase begins when the app opens."""
if __name__ == '__main__':
    try:
        from ticketdesk.server import main
        main()
    except Exception:
        import traceback
        from pathlib import Path
        root = Path(__file__).resolve().parent
        (root/'data').mkdir(exist_ok=True)
        (root/'data/startup-error.txt').write_text(traceback.format_exc(), encoding='utf-8')
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, '启动失败。请运行 setup.bat 安装依赖；详情见 data/startup-error.txt。', '抢票工作台', 0x10)
