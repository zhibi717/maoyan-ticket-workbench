# -*- coding: UTF-8 -*-
"""Allow ``python -m mobile.damai_app`` (or ``python -m damai_app`` from the
``mobile/`` directory) to launch the bot, mirroring the pre-W4-01 behaviour
of running ``python mobile/damai_app.py`` directly.

U-12：main() 返回语义化退出码（常量见 :mod:`.run_report`），并在 finally
中无条件落盘机器可读运行摘要 JSON，供 cron/systemd 等外部编排消费。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime

try:
    from mobile.config import SERIAL_OVERRIDE_ENV_VAR
except ImportError:  # pragma: no cover — `python -m damai_app`（cwd=mobile/）模式
    from config import SERIAL_OVERRIDE_ENV_VAR  # type: ignore[no-redef]

from . import DamaiBot, logger
from .run_report import (
    DEFAULT_RESULT_JSON,
    EXIT_CONFIG_OR_DEVICE_ERROR,
    EXIT_INTERRUPTED,
    EXIT_RETRIES_EXHAUSTED,
    EXIT_SUCCESS,
    EXIT_TERMINAL_FAILURE,
    RESULT_JSON_ENV_VAR,
    RUN_SUMMARY_SCHEMA_VERSION,
    write_run_summary,
)


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="damai_app")
    parser.add_argument(
        "--serial",
        default=None,
        help="覆盖 config 中的 serial（只经环境变量透传，不写回配置文件）",
    )
    parser.add_argument(
        "--result-json",
        default=None,
        help=(
            f"运行摘要 JSON 输出路径（默认 {DEFAULT_RESULT_JSON}，"
            f"亦可用环境变量 {RESULT_JSON_ENV_VAR} 指定）"
        ),
    )
    return parser.parse_args(argv)


def _safe(getter, default=None):
    """摘要组装期兜底取值——finally 内任何异常都不能吞掉退出码。"""
    try:
        return getter()
    except Exception:
        return default


def main(argv=None) -> int:
    args = _parse_args(argv)

    if args.serial is not None:
        serial_override = args.serial.strip()
        if not serial_override:
            logger.error("--serial 不能为空字符串")
            return EXIT_CONFIG_OR_DEVICE_ERROR
        # 单一覆盖通道：写环境变量，由 Config.load_config 消费（U-12），
        # prompt_runner 等走同一 load_config 的入口自动获得同等覆盖能力。
        os.environ[SERIAL_OVERRIDE_ENV_VAR] = serial_override

    result_path = (
        args.result_json
        or os.environ.get(RESULT_JSON_ENV_VAR, "").strip()
        or DEFAULT_RESULT_JSON
    )

    t0 = time.monotonic()
    started_at = datetime.now().astimezone().isoformat()
    bot = None
    exit_code = EXIT_CONFIG_OR_DEVICE_ERROR
    outcome = "config_or_device_error"
    terminal_reason = None
    try:
        try:
            bot = DamaiBot()
        except KeyboardInterrupt:
            exit_code, outcome = EXIT_INTERRUPTED, "interrupted"
            terminal_reason = "keyboard_interrupt"
        except Exception as exc:
            # 覆盖 ConfigError / KeyError / FileNotFoundError / u2 连接失败等；
            # 用 logger.exception 保留 traceback，避免把编程错误误当环境问题掩盖。
            logger.exception(f"初始化失败（配置或设备错误）: {exc}")
            terminal_reason = f"init_error: {exc}"
        else:
            try:
                success = bot.run_with_retry(max_retries=3)
            except KeyboardInterrupt:
                exit_code, outcome = EXIT_INTERRUPTED, "interrupted"
                terminal_reason = "keyboard_interrupt"
            except Exception as exc:
                # 流程内异常已被 run_ticket_grabbing 内吞；走到这里通常是设备断连。
                logger.exception(f"运行期异常（按设备错误处理）: {exc}")
                terminal_reason = f"run_error: {exc}"
            else:
                if success:
                    exit_code = EXIT_SUCCESS
                    outcome = bot._last_run_outcome or "success"
                elif bot._terminal_failure_reason:
                    exit_code, outcome = EXIT_TERMINAL_FAILURE, "terminal_failure"
                    terminal_reason = bot._terminal_failure_reason
                else:
                    exit_code, outcome = EXIT_RETRIES_EXHAUSTED, "retries_exhausted"
        return exit_code
    finally:
        try:
            if bot and bot.driver:
                bot.driver.quit()
        except Exception:
            pass
        # 摘要组装全程 _safe 兜底：残缺 bot / Mock 属性绝不能在 finally 抛异常，
        # 否则会吞掉 return 的退出码（U-12 哨兵用例守护）。
        if bot is not None:
            serial = _safe(lambda: bot.config.serial)
            mode = _safe(bot._execution_mode_key)
        else:
            # 初始化失败时从环境变量兜底（--serial 已写入 / 脚本已 export）
            serial = os.environ.get(SERIAL_OVERRIDE_ENV_VAR, "").strip() or None
            mode = None
        write_run_summary(
            result_path,
            {
                "schema_version": RUN_SUMMARY_SCHEMA_VERSION,
                "outcome": outcome,
                "exit_code": exit_code,
                "serial": serial,
                "mode": mode,
                "attempts": _safe(
                    lambda: int(getattr(bot, "_attempts_made", 0) or 0), 0
                ),
                "duration_ms": int((time.monotonic() - t0) * 1000),
                "terminal_reason": terminal_reason,
                "started_at": started_at,
                "finished_at": datetime.now().astimezone().isoformat(),
            },
        )


if __name__ == "__main__":
    sys.exit(main())
