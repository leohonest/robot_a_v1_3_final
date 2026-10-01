"""DGX -> Windows 本地 tail: 订阅灵龙 voice_v3.log (v3 listener 真实输出)
加心跳 + 自动重连（防 SSH 静默死锁）
+ 过滤噪音（arecord 0 bytes / VAD 纯状态行）

Leo 2026-09-24 10:56: 走 DGX → invoke_shell → sshpass → 灵龙
- Windows 上没 sshpass, 必须经 DGX
- paramiko 直接连灵龙超时（校园网不通 192.168.1.x）
- 用 invoke_shell + sshpass 命令组合
"""
import paramiko
import time
import sys
import os
import re

# 配置
DGX_HOST = os.environ.get("DGX_HOST", "10.86.51.122")
DGX_USER = os.environ.get("DGX_USER", "kk")
DGX_PASS = os.environ.get("DGX_PASSWORD", "")

LINGLONG_HOST = os.environ.get("LINGLONG_HOST", "192.168.1.12")
LINGLONG_USER = os.environ.get("LINGLONG_USER", "user")
LINGLONG_PASS = os.environ.get("LINGLONG_PASSWORD", "")
LINGLONG_LOG = os.environ.get("LINGLONG_LOG", "/home/user/voice_v3.log")

LOCAL_LOG = r'C:\Users\DFET\.openclaw\workspace\asr_watch_local.log'

# 心跳配置
HEARTBEAT_SEC = 30    # 30 秒没新数据就重连
RETRY_INTERVAL = 3    # 重连失败时等多久

# 噪音过滤：以下行跳过，不写本地 log
# (decision recorded in git log)
# [IGNORE] no wake keyword：噪音后续日志，全部跳过
NOISE_RE = re.compile(
    r'arecord \u8fd4\u56de 0 bytes'    # arecord 错误
    r'|^\d{4}-\d{2}-\d{2}.*\[VAD\] listening'  # 纯 VAD 状态行
    r'|FutureWarning'           # urllib3 FutureWarning
    r'|^\s*warnings\.warn'      # Python urllib3 warning 调用行
    r'|speech START'            # VAD 检测到开始（不是结果）
    r'|speech END \(\d+ frames, \d+ bytes\)'  # VAD 检测到结束（不是结果）
    r'|\[IGNORE\]'              # Leo 12:52：没唤醒词的全部 IGNORE 日志
    r'|no wake keyword'         # Leo 12:52：唤醒词检测失败的诊断
)

print(f'starting tail: {LINGLONG_USER}@{LINGLONG_HOST}:{LINGLONG_LOG} -> {LOCAL_LOG}', flush=True)

# 清旧 log（避免混内容）
try:
    os.remove(LOCAL_LOG)
except OSError:
    pass

while True:
    client = None
    try:
        # 1. paramiko 连 DGX
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(DGX_HOST, username=DGX_USER, password=DGX_PASS, timeout=10)
        print(f'[OK] connected to DGX {DGX_HOST}', flush=True)

        # 2. invoke_shell 开交互 shell, 在 shell 里 sshpass 到灵龙
        shell = client.invoke_shell()
        shell.settimeout(None)
        # 等待 shell prompt（DGX bash prompt）
        time.sleep(0.5)
        # 消化初始输出
        if shell.recv_ready():
            shell.recv(8192)
        # 3. 发 sshpass 命令
        ssh_cmd = f'sshpass -p {LINGLONG_PASS} ssh -o StrictHostKeyChecking=no {LINGLONG_USER}@{LINGLONG_HOST} "tail -n 0 -f {LINGLONG_LOG}"\n'
        shell.send(ssh_cmd)
        print(f'[OK] DGX -> Linglong sshpass tail launched', flush=True)
        time.sleep(1)
        # 消化 sshpass 连接输出
        if shell.recv_ready():
            shell.recv(8192)

        last_data_t = time.time()

        with open(LOCAL_LOG, 'a', encoding='utf-8') as f:
            buf = ''
            while True:
                # 心跳检查
                if time.time() - last_data_t > HEARTBEAT_SEC:
                    print(f'[WARN] no data for {HEARTBEAT_SEC}s, reconnecting...', flush=True)
                    break
                if shell.recv_ready():
                    try:
                        data = shell.recv(8192).decode('utf-8', errors='replace')
                    except Exception as e:
                        print(f'[ERR] recv failed: {e}, reconnecting', flush=True)
                        break
                    if not data:
                        print('[WARN] recv returned empty, reconnecting', flush=True)
                        break
                    buf += data
                    # 切行
                    while '\n' in buf:
                        line, buf = buf.split('\n', 1)
                        if line and not NOISE_RE.search(line):
                            f.write(line + '\n')
                            print(line, flush=True)  # 同时输出到 cmd 窗口
                    f.flush()
                    last_data_t = time.time()
                else:
                    time.sleep(0.1)

        # 退出 sshpass
        try:
            shell.send('\x03\n')  # Ctrl+C
            time.sleep(0.3)
        except Exception:
            pass
        shell.close()
    except Exception as e:
        print(f'[ERR] {e}, retry in {RETRY_INTERVAL}s', flush=True)
        time.sleep(RETRY_INTERVAL)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass