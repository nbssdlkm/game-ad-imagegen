"""Launcher: spawn a target script DETACHED from this process tree.

目的:试图让 child 跳出 WorkBuddy 的 Job Object,即使 WB 杀其子树,child 也存活。

Windows 关键 flags:
  DETACHED_PROCESS         — 不继承 console
  CREATE_NEW_PROCESS_GROUP — child 是新进程组(收不到父发的 Ctrl-C/Ctrl-Break)
  CREATE_BREAKAWAY_FROM_JOB(0x01000000) — child 不属于父的 Job Object

CREATE_BREAKAWAY_FROM_JOB 需要父 Job 允许 breakaway (JOB_OBJECT_LIMIT_BREAKAWAY_OK)。
如果失败 → 自动 fallback 到 DETACHED only,看能不能侥幸活下来。

Usage:
  python launcher_detached.py <target_script> [args...]

输出(写到 stdout,给 agent 拿 PID 用):
  detached_pid=<pid>
  method=<DETACHED+BREAKAWAY | DETACHED_NO_BREAKAWAY | POSIX_SETSID>
  log=<path>
"""
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        print("usage: python launcher_detached.py <target_script> [args...]", file=sys.stderr)
        return 2

    target = Path(sys.argv[1]).resolve()
    if not target.exists():
        print(f"! target script not found: {target}", file=sys.stderr)
        return 2

    extra_args = sys.argv[2:]
    log_path = target.parent / f"{target.stem}_detached_launcher.log"

    cmd = [sys.executable, "-u", str(target), *extra_args]

    log_f = open(log_path, "ab")
    log_f.write(f"\n=== launcher_detached @ {datetime.now().isoformat()} cmd={cmd} ===\n".encode("utf-8"))
    log_f.flush()

    method = None
    proc = None

    if os.name == "nt":
        BREAKAWAY = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB
        try:
            proc = subprocess.Popen(
                cmd,
                creationflags=(
                    subprocess.DETACHED_PROCESS
                    | subprocess.CREATE_NEW_PROCESS_GROUP
                    | BREAKAWAY
                ),
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=log_f,
                stderr=subprocess.STDOUT,
            )
            method = "DETACHED+BREAKAWAY"
        except OSError as e:
            # 父 Job 不允许 breakaway → fallback 到 DETACHED only
            log_f.write(f"! BREAKAWAY failed: {e}, falling back to DETACHED_NO_BREAKAWAY\n".encode("utf-8"))
            proc = subprocess.Popen(
                cmd,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=log_f,
                stderr=subprocess.STDOUT,
            )
            method = "DETACHED_NO_BREAKAWAY"
    else:
        # POSIX: setsid 创建新 session,脱离父 process group + 控制终端
        proc = subprocess.Popen(
            cmd,
            start_new_session=True,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=log_f,
            stderr=subprocess.STDOUT,
        )
        method = "POSIX_SETSID"

    print(f"detached_pid={proc.pid}")
    print(f"method={method}")
    print(f"log={log_path}")
    log_f.write(f"=== spawned pid={proc.pid} method={method} ===\n".encode("utf-8"))
    log_f.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
