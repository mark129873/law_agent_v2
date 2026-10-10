"""根目录运行：uv run --no-project start.py；Ctrl+C 同时停止前后端。"""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parent
WINDOWS = os.name == "nt"


def stop(process):
    # 只清理本脚本创建的进程组/进程树，不影响已有服务。
    if WINDOWS:
        if process.poll() is None:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            process.wait()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def main():
    processes = []
    try:
        uv, npm = shutil.which("uv"), shutil.which("npm")
        if not uv or not npm:
            raise RuntimeError("Please install uv and Node.js (including npm).")
        # cwd 指向各自目录：后端 uv 会使用 backend 的配置与虚拟环境。
        frontend = [npm, "run", "dev", "--", "--host", "127.0.0.1", "--port", "5173", "--strictPort"]
        if WINDOWS:
            # npm.cmd 需要 cmd 执行，双层引号兼容带空格的安装路径。
            frontend = f'"{os.environ.get("COMSPEC", "cmd.exe")}" /d /s /c ""{npm}" run dev -- --host 127.0.0.1 --port 5173 --strictPort"'
        commands = [
            ([uv, "run", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8100"], "backend"),
            (frontend, "frontend"),
        ]
        for command, directory in commands:
            options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if WINDOWS else {"start_new_session": True}
            processes.append(subprocess.Popen(command, cwd=ROOT / directory, **options))
        print("Starting http://127.0.0.1:5173/ | Ctrl+C stops both services.", flush=True)
        while all(process.poll() is None for process in processes):
            time.sleep(0.2)
        print("A service exited; stopping both services.", flush=True)
        return 1
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError) as exc:
        print(f"Startup failed: {exc}", flush=True)
        return 1
    finally:
        # 避免重复 Ctrl+C 打断清理，留下后台服务。
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        for process in reversed(processes):
            stop(process)


if __name__ == "__main__":
    raise SystemExit(main())
