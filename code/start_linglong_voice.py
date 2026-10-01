#!/usr/bin/env python3
"""
灵龙收音 + 语音交互启动脚本（运维工具）
======================================
完整流程（6 步）：
1. SSH 跳板确认（DGX 跳转）
2. 检查 USB 设备
3. 建立 SSH 反向隧道（DGX -> 灵龙 4 端口）
4. 同步 linglong_voice_v3.py 到灵龙
5. 杀旧 listener + 启新
6. 启动 Windows v3_log_tail.py 实时转播

凭证全部来自环境变量（参见 .env.example）：
  LINGLONG_USER     默认 user
  LINGLONG_HOST     默认 192.168.1.12
  LINGLONG_PASSWORD 必填
  WORKSPACE_DIR     Windows 工作区目录（可选）

依赖：dgx_ssh.py（团队内部统一 SSH 入口，不在开源仓库内）。
"""
import os
import sys
import time
import subprocess

LINGLONG_USER = os.environ.get("LINGLONG_USER", "user")
LINGLONG_HOST = os.environ.get("LINGLONG_HOST", "192.168.1.12")
LINGLONG_PASS = os.environ.get("LINGLONG_PASSWORD", "")
WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", os.path.join(os.path.expanduser("~"), ".openclaw", "workspace"))

if not LINGLONG_PASS:
    print("[ERROR] 未设置 LINGLONG_PASSWORD 环境变量（见 .env.example），退出。")
    sys.exit(1)

sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))
try:
    from dgx_ssh import dgx_run
except ImportError:
    print(f"[ERROR] 未找到 dgx_ssh 模块（{WORKSPACE_DIR}/scripts/dgx_ssh.py）。")
    print("        该模块是团队内部 SSH 跳板工具，不在开源仓库中。")
    sys.exit(1)


def ssh(cmd: str) -> str:
    """在灵龙本体上执行命令（凭证来自环境变量，不出现在命令行历史中明文扩散）"""
    full = (f"sshpass -p {LINGLONG_PASS} ssh -o StrictHostKeyChecking=accept-new "
            f"{LINGLONG_USER}@{LINGLONG_HOST} {cmd}")
    out = dgx_run(full)
    return out[0] if isinstance(out, (list, tuple)) else str(out)


def sync(msg, step_no, total_steps=6):
    print(f"[SYNC {step_no}/{total_steps}] {msg}")


def step2_check_usb():
    sync("Step 2: 检查 USB 设备", 2)
    out = ssh("'arecord -l; aplay -l'")
    print(out[:3000])


def step3_start_tunnel():
    sync("Step 3: 启动 SSH 反向隧道", 3)
    dgx_run("ps aux | grep 'ssh.*-R.*linglong' | grep -v grep | awk '{print $2}' | xargs -r kill -9 2>/dev/null; echo killed_old")
    cmd = (f"nohup sshpass -p {LINGLONG_PASS} ssh -N -o StrictHostKeyChecking=accept-new "
           "-o ServerAliveInterval=30 -o ServerAliveCountMax=3 "
           "-o ExitOnForwardFailure=yes "
           "-R 7780:127.0.0.1:7780 "
           "-R 7792:127.0.0.1:7792 "
           "-R 7793:127.0.0.1:7793 "
           "-R 9003:127.0.0.1:9003 "
           f"{LINGLONG_USER}@{LINGLONG_HOST} > /tmp/asr_tunnel.log 2>&1 & "
           "echo TUNNEL_PID=$!; sleep 3; ps aux | grep 'ssh.*-R' | grep -v grep")
    out = dgx_run(cmd)
    print("=== TUNNEL STARTED ===")
    print(out[:500])
    verify = ssh("'netstat -lntp | grep -E \"(7780|7792|7793|9003)\" | head -8; echo ---ASR---; curl -s -m 3 http://127.0.0.1:7780/health'")
    print(verify[:1500])


def step4_sync_listener():
    sync("Step 4: 同步 linglong_voice_v3.py 到灵龙", 4)
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dgx_scripts", "linglong_voice_v3.py")
    if not os.path.exists(src):
        print(f"[WARN] 未找到 {src}，跳过同步")
        return
    out = dgx_run(f"put {src} /home/user/linglong_voice_v3.py")
    print(out[:300])


def step5_restart_listener():
    sync("Step 5: 重启 listener", 5)
    ssh("'pkill -9 -f linglong_voice_v3.py; sleep 2; echo killed'")
    out = ssh("'cd /home/user && nohup python3 linglong_voice_v3.py "
              "--device plughw:3,0 --speaker-device plughw:1,0 "
              "> voice_v3.log 2>&1 & sleep 3; "
              "ps aux | grep linglong_voice_v3 | grep -v grep'")
    print("=== NEW LISTENER ===")
    print(out[:500])
    time.sleep(2)
    print("=== LISTENER LOG ===")
    print(ssh("'tail -n 15 /home/user/voice_v3.log'")[:1500])


def step6_start_log_tail():
    sync("Step 6: 启动 Windows v3_log_tail.py", 6)
    check = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*v3_log_tail*' } | Select-Object -First 1 Id"],
        capture_output=True, text=True)
    if check.stdout.strip():
        print(f"v3_log_tail.py already active: {check.stdout.strip()}")
        return
    tail = os.path.join(WORKSPACE_DIR, "v3_log_tail.py")
    if os.path.exists(tail):
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Start-Process python -ArgumentList '{tail}' -WindowStyle Normal"])
    else:
        print(f"[WARN] 未找到 {tail}，跳过")


def main():
    print("=" * 60)
    print("灵龙收音 + 语音交互启动脚本")
    print("=" * 60)
    sync("Step 1: DGX 跳板 + Linglong SSH 验证", 1)
    print(ssh("'uname -a; whoami; hostname'")[:500])
    step2_check_usb()
    step3_start_tunnel()
    step4_sync_listener()
    step5_restart_listener()
    step6_start_log_tail()
    print("=" * 60)
    print("全部完成！可以喊唤醒词测试了。")
    print("=" * 60)


if __name__ == "__main__":
    main()
