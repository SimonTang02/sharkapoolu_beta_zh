"""限制自动准备范围、识别人工检查点并保留 Chrome 标签页。"""
from __future__ import annotations

import os
import signal
import subprocess
import time


def stop_adapter(process):
    if process.poll() is None:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        try:
            process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.communicate()


def execute_preparation(plan, config: dict, monitor, root):
    policy = config["operator_controls"]
    deadline = time.monotonic() + plan.timeout_seconds
    for _ in range(policy["adapter_retries"] + 1):
        if time.monotonic() >= deadline:
            break
        checkpoint = monitor.interruption(plan.application_id)
        if checkpoint:
            plan.state, plan.result_code = checkpoint, 125
            return
        process = subprocess.Popen(plan.command, cwd=root, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, start_new_session=os.name == "posix")
        try:
            while True:
                try:
                    process.communicate(timeout=min(1, max(.01, deadline - time.monotonic())))
                    break
                except subprocess.TimeoutExpired:
                    checkpoint = monitor.interruption(plan.application_id)
                    if checkpoint or time.monotonic() >= deadline:
                        stop_adapter(process)
                        plan.state = checkpoint or "adapter_timeout"
                        plan.result_code = 125 if checkpoint else 124
                        return
            plan.result_code = process.returncode
            checkpoint = monitor.interruption(plan.application_id)
            if checkpoint:
                plan.state, plan.result_code = checkpoint, 125
                return
            if process.returncode == 0:
                plan.state = "adapter_prepared_not_submitted"
                return
            plan.state = "adapter_failed"
            # 适配器输出可能包含私有事实；绝不要将其写入错误信息。
            plan.error = "适配器未完成；查看该适配器的私有审查产物，勿盲目重试"
        finally:
            stop_adapter(process)
    if time.monotonic() >= deadline:
        plan.state, plan.result_code = "adapter_timeout", 124
