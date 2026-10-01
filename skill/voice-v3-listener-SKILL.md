# Voice V3 Listener SKILL（最新版 · 2026-09-29 更新）

> 适用: 灵龙 Jetson AGX Orin `/home/user/linglong_voice_v3.py`
> 目的: 部署灵龙端 VAD + ASR listener（完整流程，含 SSH 反向隧道）
> 创建: 2026-09-24 11:00
> 最后更新: 2026-09-29 10:10（**重大更新：DGX_HOST 改成 127.0.0.1 + 反向隧道**）

---

## 1. 完整链路（**更新**）

```
灵龙 mic → VAD (webrtcvad) → arecord → 127.0.0.1:7780 (SSH -R 隧道)
                                                ↓
                                  DGX :7780 Qwen3-ASR 0.6B (实际)
                                                ↓
                                              text → 127.0.0.1:7792 (SSH -R 隧道)
                                                ↓
                                  DGX :7792 LLM Orchestrator
                                                ↓
                                              text → 127.0.0.1:7793 (SSH -R 隧道)
                                                ↓
                                  DGX :7793 action_router → 命中执行 / miss → LLM
                                                ↓
                                              text → 127.0.0.1:9003 (SSH -R 隧道)
                                                ↓
                                  DGX :9003 TTS (Qwen3-TTS-VoiceDesign)
                                                ↓
                                       plughw:1,0 (喇叭 Card 1 USB Composite)
```

**关键变化**：因为灵龙 192.168.1.x 不通 DGX 10.86.x，**全部通过 DGX→灵龙 SSH -R 反向隧道走 127.0.0.1**。

---

## 2. 每日启动流程（6 步 · 必读）

### Step 1：SSH 跳板确认
- Windows → DGX (10.86.51.122, kk/${DGX_PASSWORD}) → Linglong (192.168.1.12, user/admin)
- 用 `scripts/dgx_ssh.py:dgx_run()` 统一入口

### Step 2：检查 USB 设备（**每次启动必查**）
```bash
sshpass -p $LINGLONG_PASSWORD ssh $LINGLONG_USER@$LINGLONG_HOST 'arecord -l; aplay -l'
```
**灵龙当前固定设备表**（2026-09-29 验证）：
| Card | 设备 | 功能 | 路径 |
|------|------|------|------|
| 1 | USB Composite Device | 双工喇叭 | plughw:1,0 |
| 2 | NVIDIA Jetson AGX Orin APE | 内部 | 不使用 |
| 3 | B107A6 (BATSound USB Mic) | **麦克风** | plughw:3,0 |
| 0 | NVIDIA HDA (HDMI 0-3) | 备用 | **未用** |

**启动命令**：
```bash
nohup python3 /home/user/linglong_voice_v3.py \
  --device plughw:3,0 \
  --speaker-device plughw:1,0 \
  > /home/user/voice_v3.log 2>&1 &
```

### Step 3：建立 SSH 反向隧道（DGX → 灵龙）⭐⭐⭐ 关键
**根因**：灵龙 192.168.1.x 不通 DGX 10.86.x，必须从 DGX 主动 SSH 过去并开 -R 反向隧道。

**隧道启动脚本**（`scripts/start_asr_tunnel.sh`，存在 DGX 上）：
```bash
#!/bin/bash
# 杀旧隧道
ps aux | grep 'ssh.*-R.*$LINGLONG_USER@$LINGLONG_HOST' | grep -v grep | awk '{print $2}' | xargs -r kill -9 2>/dev/null

# 启新隧道（4 个端口）
nohup sshpass -p $LINGLONG_PASSWORD ssh -N \
  -o StrictHostKeyChecking=no \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -o ExitOnForwardFailure=yes \
  -R 7780:127.0.0.1:7780 \
  -R 7792:127.0.0.1:7792 \
  -R 7793:127.0.0.1:7793 \
  -R 9003:127.0.0.1:9003 \
  $LINGLONG_USER@$LINGLONG_HOST > /tmp/asr_tunnel.log 2>&1 &

echo "TUNNEL_PID=$!"
sleep 3
ps aux | grep 'ssh.*-R.*192.168.1.12' | grep -v grep
```

**验证隧道**（从 DGX 查灵龙 127.0.0.1）：
```bash
ssh $LINGLONG_USER@$LINGLONG_HOST 'netstat -lntp | grep -E "(7780|7792|7793|9003)"'
# 必须看到 4 个 LISTEN

ssh $LINGLONG_USER@$LINGLONG_HOST 'curl -s http://127.0.0.1:7780/health'
# 必须返回 {"status":"ok","service":"qwen3-asr"...}
```

### Step 4：修改 linglong_voice_v3.py 的 DGX_HOST
```bash
# 1. 备份（必须！）
ssh $LINGLONG_USER@$LINGLONG_HOST 'cp /home/user/linglong_voice_v3.py /home/user/linglong_voice_v3.py.bak.YYYYMMDD_HHMMSS'

# 2. 修改（10.86.51.122 → 127.0.0.1）
ssh $LINGLONG_USER@$LINGLONG_HOST 'sed -i "s|DGX_HOST = \"10.86.51.122\"|DGX_HOST = \"127.0.0.1\"|" /home/user/linglong_voice_v3.py'

# 3. 验证
ssh $LINGLONG_USER@$LINGLONG_HOST 'grep DGX_HOST /home/user/linglong_voice_v3.py'
# 必须看到: DGX_HOST = "127.0.0.1"
```

### Step 5：杀掉旧 listener + 启动新的
```bash
ssh $LINGLONG_USER@$LINGLONG_HOST 'pkill -9 -f linglong_voice_v3.py; sleep 2'
ssh $LINGLONG_USER@$LINGLONG_HOST 'cd /home/user && nohup python3 linglong_voice_v3.py \
  --device plughw:3,0 \
  --speaker-device plughw:1,0 \
  > voice_v3.log 2>&1 &'

# 验证 PID
ssh $LINGLONG_USER@$LINGLONG_HOST 'ps aux | grep linglong_voice_v3 | grep -v grep'
```

**预期日志**：
```
[VAD] listening... (device=plughw:3,0, frame=30ms, silence_to_end=50frames)
```
- silence_to_end 50 frames (1500ms, 9/28 Leo 调过)

### Step 6：启动 Windows 端 v3_log_tail.py 实时转播
```powershell
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = "python"
$psi.Arguments = "C:\Users\DFET\.openclaw\workspace\v3_log_tail.py"
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $false
$psi.WindowStyle = "Normal"
$proc = [System.Diagnostics.Process]::Start($psi)
```

---

## 3. v3.py 关键代码

### 当前配置（2026-09-29）
```python
# /home/user/linglong_voice_v3.py
DGX_HOST = "127.0.0.1"  # 经 SSH -R 隧道（不是 10.86.51.122）
ASR_URL = f"http://{DGX_HOST}:7780/asr"
LLM_URL = f"http://{DGX_HOST}:7792/chat"
ACTION_URL = f"http://{DGX_HOST}:7793/action"
TTS_URL = f"http://{DGX_HOST}:9003/tts_base64"

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = 'int16'

COOLDOWN_SEC = 4.0
VAD_FRAME_MS = 30
SILENCE_TO_END = 50  # 9/28 调成 50 (1500ms)
VAD_AGGRESSIVENESS = 1
MIN_SPEECH_FRAMES = 3
```

### 启动参数
```bash
python3 linglong_voice_v3.py \
  --device plughw:3,0 \
  --speaker-device plughw:1,0
```

### play_wav() 用 aplay -D
```python
SPEAKER_DEVICE = None  # 由 main() 设置

def play_wav(wav_bytes):
    tmp = "/tmp/ll_reply.wav"
    with open(tmp, 'wb') as f:
        f.write(wav_bytes)
    spk = SPEAKER_DEVICE
    rc = subprocess.run(f"aplay -D {spk} -q {tmp}", shell=True)
    return rc.returncode == 0
```

---

## 4. Wake Word + FILLER（9/28 优化）

```python
WAKE_PATTERNS = [
    r'你好[,.，。、\s]*[灵龙]',
    r'^[灵龙]',
    r'[灵龙][,，]?\s*在吗',
    r'[灵龙][,，]?\s*你',
]
ASR_CORRECTIONS = [
    ('玲珑', '灵龙'), ('临龙', '灵龙'), ('林龙', '灵龙'),
    ('凌龙', '灵龙'), ('陵龙', '灵龙'), ('龄龙', '灵龙'),
    ('隆龙', '灵龙'), ('柠檬', '灵龙'),
]

FILLER_ONLY = {'嗯', '啊', '哎', '哦', '呃', '嗯。', '啊。'}
```

**注意**：Qwen3-ASR 经常把"灵龙"识别成"玲珑"，correction 在 v3 listener 里做。

---

## 5. 故障排查

### 5.1 ASR "Network is unreachable"
**根因**：DGX_HOST 用了 10.86.51.122（直连），但灵龙不通 DGX
**解决**：走 SSH -R 隧道，DGX_HOST 改 127.0.0.1（见 Step 3-4）

### 5.2 VAD 一直 "too short"
- 检查 arecord 是否真在收：`arecord -D plughw:3,0 -d 2 -f S16_LE -r 16000 /tmp/test.wav`
- 检查 mic 是否被 mute：`alsamixer`
- 改 `--device` 到正确卡号（每次启动必查 arecord -l）

### 5.3 ASR 返回 "嗯。"
- mic 在收 TTS 尾音（COOLDOWN 不够）
- 加 `COOLDOWN_SEC`（4 秒够）

### 5.4 action_router 全部 fallback 45 度
- 检查 ASR 输出是否是阿拉伯数字（不是中文）
- 看 `chinese_to_arabic` 是否正确转换（action_router.py 处理）

### 5.5 隧道断了 (DGX ASR 健康检查失败)
- 隧道进程被 kill：检查 `/tmp/asr_tunnel.log`
- 重新跑 Step 3 启动脚本

---

## 6. 完整启动脚本（明天直接跑）

```python
# scripts/start_linglong_voice.py
import sys
sys.path.insert(0, r'C:\Users\DFET\.openclaw\workspace\scripts')
from dgx_ssh import dgx_run
import time

def start():
    # Step 2: 检查 USB 设备
    out = dgx_run("sshpass -p $LINGLONG_PASSWORD ssh $LINGLONG_USER@$LINGLONG_HOST 'arecord -l; aplay -l'")
    print('=== USB DEVICES ===\n', out[0][:2000])
    
    # Step 3: 启动 SSH 反向隧道
    tunnel_script = """
ps aux | grep 'ssh.*-R.*$LINGLONG_USER@$LINGLONG_HOST' | grep -v grep | awk '{print $2}' | xargs -r kill -9 2>/dev/null
nohup sshpass -p $LINGLONG_PASSWORD ssh -N -o StrictHostKeyChecking=no -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes -R 7780:127.0.0.1:7780 -R 7792:127.0.0.1:7792 -R 7793:127.0.0.1:7793 -R 9003:127.0.0.1:9003 $LINGLONG_USER@$LINGLONG_HOST > /tmp/asr_tunnel.log 2>&1 &
sleep 3
echo TUNNEL_DONE
"""
    dgx_run(tunnel_script)
    
    # Step 4: 修改 DGX_HOST（备份 + sed）
    ts = time.strftime("%Y%m%d_%H%M%S")
    dgx_run(f"sshpass -p $LINGLONG_PASSWORD ssh $LINGLONG_USER@$LINGLONG_HOST 'cp /home/user/linglong_voice_v3.py /home/user/linglong_voice_v3.py.bak.{ts}'")
    dgx_run("sshpass -p $LINGLONG_PASSWORD ssh $LINGLONG_USER@$LINGLONG_HOST 'sed -i \"s|DGX_HOST = \\\"10.86.51.122\\\"|DGX_HOST = \\\"127.0.0.1\\\"|\" /home/user/linglong_voice_v3.py'")
    
    # Step 5: 杀旧 listener + 启新
    dgx_run("sshpass -p $LINGLONG_PASSWORD ssh $LINGLONG_USER@$LINGLONG_HOST 'pkill -9 -f linglong_voice_v3.py; sleep 2'")
    dgx_run("sshpass -p $LINGLONG_PASSWORD ssh $LINGLONG_USER@$LINGLONG_HOST 'cd /home/user && nohup python3 linglong_voice_v3.py --device plughw:3,0 --speaker-device plughw:1,0 > voice_v3.log 2>&1 &'")
    
    # Step 6: 验证
    time.sleep(3)
    out = dgx_run("sshpass -p $LINGLONG_PASSWORD ssh $LINGLONG_USER@$LINGLONG_HOST 'tail -n 10 /home/user/voice_v3.log'")
    print('=== LISTENER LOG ===\n', out[0][:1000])

if __name__ == '__main__':
    start()
```

---

## 7. 历史变化记录

- **2026-09-29 10:10** — **DGX_HOST 改成 127.0.0.1 + 反向隧道 4 端口**（彻底解决 Network unreachable）
- **2026-09-28 13:03** — silence 900ms→1500ms（VAD 优化）
- **2026-09-28 12:50** — filler silent（不显示 [ASR]/[FILLER]）
- **2026-09-24 11:00** — 初始版本，B107A6 mic + USB Composite speaker
- **2026-09-24 16:46** — 增加 4 象限 rotation 测试

---

_最后更新：2026-09-29 10:10 (Leo 强烈要求记下来)_