# -*- coding: UTF-8 -*-
"""统一的 subprocess 文本模式封装（issue #50：Windows GBK 解码崩溃修复）。

背景：Windows（简体中文）上 ``subprocess.run(text=True)`` 未显式传 encoding 时
会按 locale 首选编码（cp936/GBK）解码子进程输出；``adb shell pm dump`` 等命令
输出的 UTF-8 中文字节按 GBK 对齐后可能落到非法组合上，在 Windows 读取线程内
抛出 UnicodeDecodeError（子线程崩溃、stdout 变 None）。

规则：``mobile/`` 内任何 text 模式的 subprocess 调用必须经由本模块的
:func:`run_captured` 封装，或自行显式传 ``encoding`` 参数
（tests/unit/test_proc_utils.py 内有 AST 守卫测试强制此规则）。

说明：不给子进程设置 ``PYTHONIOENCODING``——子进程全是原生二进制
（adb/magick/tesseract）而非 Python，该环境变量对其无效；adb 转发的本就是
设备端 UTF-8 字节流，问题纯在父进程解码侧。
"""

from __future__ import annotations

import subprocess
from typing import Optional


def run_captured(
    cmd,
    *,
    timeout: Optional[float] = None,
    check: bool = False,
    env: Optional[dict] = None,
) -> subprocess.CompletedProcess:
    """以 UTF-8 文本模式执行命令并捕获 stdout/stderr。

    显式 ``encoding="utf-8"`` 使 Windows/macOS/Linux 行为一致；macOS/Linux
    本就是 UTF-8 locale，正常路径输出逐字节相同、零行为变化。
    ``errors="replace"`` 只在此前必崩的非法字节场景生效（替换为 U+FFFD）。

    Args:
        cmd: 命令及参数列表（与 ``subprocess.run`` 首参一致）。
        timeout: 超时秒数；超时抛 ``subprocess.TimeoutExpired``（原样透传）。
        check: 为 True 时非零退出码抛 ``subprocess.CalledProcessError``（原样透传）。
        env: 传给子进程的环境变量映射；None 表示继承当前进程环境。

    Returns:
        ``subprocess.CompletedProcess``，其 stdout/stderr 为 UTF-8 解码后的 str。
    """
    return subprocess.run(  # noqa: S603 — 调用方保证命令来源可信
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=check,
        env=env,
    )
