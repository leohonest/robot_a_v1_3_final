# Windows ASR 实时窗口 SKILL

> 适用: Windows 本地 `C:\Users\DFET\.openclaw\workspace\`
> 目的: 在 Windows 桌面跑 ASR/LLM/TTS 实时消息窗口，方便 Leo 现场看
> 创建: 2026-09-24 11:00

---

## 1. 架构

```
DGX linglong_asr_watch.py (PID 1401732)
   ↓ tail 灵龙 /home/user/voice_v3.log
   ↓ 写到 DGX /tmp/action_router.log (本地 log)
   
Windows v3_log_tail.py (PID 70532)
   ↓ paramiko + invoke_shell 跳板到 DGX → sshpass 到灵龙
   ↓ 过滤噪音（VAD START/END / FILLER / arecord 错误 / FutureWarning）
   ↓ 写到 C:\Users\DFET\.openclaw\workspace\asr_watch_local.log

Windows v3_gui.py (PID 59104)
   ↓ Tkinter 窗口，轮询 asr_watch_local.log
   ↓ 高亮 [ASR] / [LLM] / [TTS] / [WAKE] / [DIALOG]
   ↓ Leo 在工位上能看到
```

---

## 2. 三个核心文件

### 2.1 `v3_log_tail.py`（tail 灵龙 log + 过滤 + 写本地）

**关键技术**：
- 用 **paramiko**（Python SSH 库，不是 plink）直连 DGX
- 用 **invoke_shell()** 开交互 shell
- shell 里发 `sshpass -p $LINGLONG_PASSWORD ssh $LINGLONG_USER@$LINGLONG_HOST "tail -n 0 -f /home/user/voice_v3.log"`
- 收 shell stdout → 过滤噪音 → 写本地 log

**为什么不用 plink/sshpass 直连灵龙**：
- Windows **没装 sshpass**
- paramiko 直连灵龙（192.168.1.12）**超时**（校园网不通 192.168.1.x 段）
- 必须经 DGX（10.86.51.122）跳板

**噪音过滤 NOISE_RE**：
```python
NOISE_RE = re.compile(
    r'arecord \u8fd4\u56de 0 bytes'    # arecord 错误
    r'|^\d{4}-\d{2}-\d{2}.*\[VAD\] listening'  # 纯 VAD 状态行
    r'|FutureWarning'           # urllib3 FutureWarning
    r'|\[IGNORE\]'              # 没识别到 wake 的填充词
    r'|no wake keyword'         # 唤醒词检测失败的诊断行
    r'|speech START'            # VAD 检测到开始（不是结果）
    r'|speech END \(\d+ frames, \d+ bytes\)'  # VAD 检测到结束（不是结果）
    r'|\[FILLER\]'              # 填充词丢弃日志
    r'|^\s*warnings\.warn'      # Python urllib3 warning 调用行
)
```

**心跳保护**：
```python
HEARTBEAT_SEC = 30  # 30 秒没新数据 → 重连
```

---

### 2.2 `v3_gui.py`（Tkinter 高亮窗口）

```python
COLOR_ASR  = '#00bcd4'   # 青
COLOR_LLM  = '#43a047'   # 绿
COLOR_TTS  = '#1e88e5'   # 蓝
COLOR_VAD  = '#9e9e9e'   # 灰
COLOR_WAKE = '#fbc02d'   # 黄
COLOR_ERR  = '#e53935'   # 红
COLOR_DIAL = '#8e24aa'   # 紫

PATTERNS = [
    (re.compile(r'\[ASR\]'),         COLOR_ASR),
    (re.compile(r'\[LLM\] reply'),  COLOR_LLM),
    (re.compile(r'\[TTS\]'),        COLOR_TTS),
    (re.compile(r'\[WAKE\]'),       COLOR_WAKE),
    (re.compile(r'\[NO MATCH\]|Error|Traceback|too short'), COLOR_ERR),
    (re.compile(r'\[DIALOG\]'),     COLOR_DIAL),
]
```

**轮询机制**：
```python
def poll(self):
    if os.path.exists(LOG):
        size = os.path.getsize(LOG)
        if size < self._size:
            # 文件被截断（重启 tail 时）
            self._size = 0
        if size > self._size:
            with open(LOG, 'r', encoding='utf-8', errors='replace') as f:
                f.seek(self._size)
                chunk = f.read(size - self._size)
                # ... 切行 + 高亮 append
    self.root.after(300, self.poll)  # 每 300ms 轮询
```

---

### 2.3 `start_v3_gui.ps1`（启动脚本）

```powershell
$p1 = Start-Process python -ArgumentList 'C:\Users\DFET\.openclaw\workspace\v3_log_tail.py' `
  -PassThru -WindowStyle Hidden
$p2 = Start-Process python -ArgumentList 'C:\Users\DFET\.openclaw\workspace\v3_gui.py' `
  -PassThru -WindowStyle Hidden
Start-Sleep 2
Write-Host "v3_log_tail PID:" $p1.Id
Write-Host "v3_gui PID:" $p2.Id
```

**重要**：`-WindowStyle Hidden` 让两个 Python 进程**不显示窗口**——Leo 看不到 cmd 窗口（但 log 在跑）。

---

## 3. v3_log_tail.py 完整代码骨架

```python
import paramiko
import time
import sys
import os
import re

DGX_HOST = '10.86.51.122'
DGX_USER = 'kk'
DGX_PASS = '${DGX_PASSWORD}'

LINGLONG_HOST = '192.168.1.12'
LINGLONG_USER = 'user'
LINGLONG_PASS = '${LANGSHOST_PASSWORD}'
LINGLONG_LOG = '/home/user/voice_v3.log'  # (decision recorded in git log)

LOCAL_LOG = r'C:\Users\DFET\.openclaw\workspace\asr_watch_local.log'

NOISE_RE = re.compile(...)  # 见上

HEARTBEAT_SEC = 30

while True:
    client = None
    try:
        # 1. paramiko 连 DGX
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(DGX_HOST, username=DGX_USER, password=DGX_PASS, timeout=10)

        # 2. invoke_shell 开交互 shell
        shell = client.invoke_shell()
        shell.settimeout(None)
        time.sleep(0.5)
        if shell.recv_ready():
            shell.recv(8192)  # 消化初始输出

        # 3. 发 sshpass 命令
        ssh_cmd = f'sshpass -p {LINGLONG_PASS} ssh -o StrictHostKeyChecking=no {LINGLONG_USER}@{LINGLONG_HOST} "tail -n 0 -f {LINGLONG_LOG}"\n'
        shell.send(ssh_cmd)
        time.sleep(1)
        if shell.recv_ready():
            shell.recv(8192)  # 消化 sshpass 连接输出

        last_data_t = time.time()
        with open(LOCAL_LOG, 'a', encoding='utf-8') as f:
            while True:
                if time.time() - last_data_t > HEARTBEAT_SEC:
                    print(f'[WARN] no data for {HEARTBEAT_SEC}s, reconnecting...', flush=True)
                    break
                if shell.recv_ready():
                    try:
                        data = shell.recv(8192).decode('utf-8', errors='replace')
                    except Exception as e:
                        print(f'[ERR] recv failed: {e}', flush=True)
                        break
                    if not data:
                        break
                    buf += data
                    while '\n' in buf:
                        line, buf = buf.split('\n', 1)
                        if line and not NOISE_RE.search(line):
                            f.write(line + '\n')
                    f.flush()
                    last_data_t = time.time()
                else:
                    time.sleep(0.1)
        shell.close()
    except Exception as e:
        print(f'[ERR] {e}, retry in 3s', flush=True)
        time.sleep(3)
    finally:
        if client:
            try: client.close()
            except: pass
```

---

## 4. 常见坑

### 4.1 v3_log_tail 进程死了但 Windows Terminal 窗口还在
- `subprocess.Popen` 用 `CREATE_NEW_CONSOLE` 后，python 进程死掉但 cmd 窗口仍开
- **症状**：Leo 看到 Windows Terminal 9368 标题 `v3_log_tail - LingLong ASR Live` 但 log 不更新
- **检测**：`pgrep -fa v3_log_tail.py` 应该看到进程

### 4.2 Leo 看不到 cmd 窗口
- **症状**：用 `CREATE_NEW_CONSOLE` 启动 cmd 但 Leo 看不到窗口
- **原因**：cmd 启动后 python 退出，窗口立即关闭
- **解决**：用 `cmd /K`（不退出）+ `python -u v3_log_tail.py`，**DETACHED_PROCESS**

### 4.3 Leo 看不到 cmd 窗口 + 历史窗口标题残留
- **症状**：Leo 看到 `v3_log_tail - LingLong ASR Live` 但里面没进程在跑
- **原因**：昨天 Leo 手动启动的窗口（标题是 .bat 里 `title` 设的），但里面 python 早死了
- **解决**：重启 v3_log_tail.py 让 Leo 看到活的进程

---

## 5. 启动流程（Leo 看到的窗口）

1. **Leo 用 Windows Terminal 9368 手动启动 .bat**（标题来自 `_v3_log_tail_window.bat` 里的 `title v3_log_tail - LingLong ASR Live`）
2. **cmd /K** 启动 `python -u v3_log_tail.py`
3. **python 在 cmd 里 tail 灵龙 log + 过滤 + 写本地**

Leo 在 Windows 桌面**看到两个窗口**：
- **Windows Terminal 9368**：标题 `v3_log_tail - LingLong ASR Live`（滚动 v3_log_tail 自己的 print 输出）
- **Tkinter 窗口**：标题 `灵龙 v3 ASR 实时打印 (v3_gui.py)`（高亮显示 ASR/LLM/TTS 消息）

---

## 6. 备份（Leo 10:20 立的"先备份再改"铁律）

每次改 v3_log_tail.py / v3_gui.py / start_v3_gui.ps1 前：

```python
import shutil, datetime
ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
src = r"C:\Users\DFET\.openclaw\workspace\v3_log_tail.py"
bk = rf"C:\Users\DFET\.openclaw\workspace\backups\v3_log_tail.py.bak.{ts}_before_XXX"
shutil.copy2(src, bk)
```

---

## 7. Leo 16:13 警告

Leo 16:13 说"窗口没消息" — 99% 是因为 **v3_log_tail 进程死了但 Windows Terminal 窗口还在**。respawn 流程：

1. `powershell Stop-Process -Id <v3_log_tail_pid> -Force`
2. `cd C:\Users\DFET\.openclaw\workspace`
3. `cmd /K title "v3_log_tail - LingLong ASR Live" && python -u v3_log_tail.py`

---

_最后更新：2026-09-24 16:46_