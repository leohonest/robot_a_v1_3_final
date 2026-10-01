# 灵龙语音问答 SKILL (linglong-voice-qa-SKILL.md)

> 灵龙端 VAD 拾音 → DGX ASR/LLM/TTS 全链路 + Windows 端实时打印 GUI
> 版本：v1.0  2026-09-21 测试

## 一、架构概览（2026-09-24 更新设备路径）

```
┌─────────────────────────────────────────────────────────────────┐
│ 灵龙 Jetson AGX Orin (192.168.1.12, user/admin)               │
│                                                                 │
│  B107A6 mic (Card 0, plughw:0,0) ──▶ arecord + webrtcvad      │
│  USB Composite 喇 (Card 3, plughw:3,0) ◀── aplay              │
│  → /home/user/linglong_voice_v3.py                            │
└─────────────────────────────────────────────────────────────────┘
```

**⚠️ 设备路径（2026-09-24 修复）**：
- **mic 正确路径**：`plughw:0,0`（Card 0 = B107A6 USB 麦克风）
  - ❌ 旧路径 `plughw:1,0` = HDMI 0，只有 playback 没有 capture，VAD 永远收不到音频
- **speaker 正确路径**：`plughw:3,0`（Card 3 = USB Composite Device 双工喇）
  - 启动命令加 `--speaker-device plughw:3,0`
  - `play_wav()` 用 `aplay -D plughw:3,0 -q {tmp}` 替代 paplay

**ALSA 设备查询**（灵龙上电后查，不能假设备号不变）：
```bash
arecord -l  # 查 capture 设备 → Card 0 = B107A6 = plughw:0,0
aplay -l   # 查 playback 设备 → Card 3 = USB Composite = plughw:3,0
```

**v3 log 路径**（2026-09-24 确认）：
- 正确路径：`/home/user/voice_v3.log`（不是 `/tmp/v3.log`）
- 启动：`nohup python3 linglong_voice_v3.py --device plughw:0,0 --speaker-device plughw:3,0 > /home/user/voice_v3.log 2>&1 < /dev/null &`
                │                                      ▲
                │ HTTP (DGX WiFi, 直连不走 Windows 校园网)   │
                ▼                                      │
┌─────────────────────────────────────────────────────────────────┐
│ DGX (10.86.51.122)                                              │
│                                                                 │
│  :7780  Qwen3-ASR 0.6B  ──▶ wav bytes → text                     │
│  :7792  LLM Orchestrator ──▶ intent 检测 + 路由                 │
│         ├─ local  Ollama qwen3-vl:4b (普通问答)                 │
│         ├─ cloud  MiniMax-M3 + web_search (天气/汇率/股票)     │
│         └─ fallback "I cannot" (web_search 也失败时)            │
│  :9003  CosyVoice VoiceDesign (成熟男声 TTS-02)                 │
└─────────────────────────────────────────────────────────────────┘
                │
                │ SSH plink DGX→灵龙 跳板
                ▼
┌─────────────────────────────────────────────────────────────────┐
│ Windows 工作站 (192.168.4.89, 你在这里)                          │
│                                                                 │
│  v3_log_tail.py  ──▶ asr_watch_local.log (实时增量)              │
│                       │                                          │
│                       ▼                                          │
│  v3_gui.py (Tkinter)  ──▶  "灵龙 v3 ASR 实时打印" 窗口           │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## 二、关键文件路径

| 文件 | 路径 | 备注 |
|------|------|------|
| v3 listener (灵龙) | `/home/user/linglong_voice_v3.py` | 修改目标 |
| v3 log (灵龙) | `/home/user/voice_v3.log` | nohup 启动 → **不是** /tmp/v3.log |
| 备份链 | `/home/user/linglong_voice_v3.py.bak.YYYYMMDD_HHMMSS` | 改前先备份 |
| 日志订阅 (Win) | `C:\Users\DFET\.openclaw\workspace\v3_log_tail.py` | DGX→灵龙→本地 |
| 本地日志 | `C:\Users\DFET\.openclaw\workspace\asr_watch_local.log` | GUI 读这个 |
| ASR GUI 窗口 | `C:\Users\DFET\.openclaw\workspace\v3_gui.py` | Tkinter，可选 |
| Orchestrator | DGX `/data/linglong-project/scripts/llm_orchestrator.py` | :7792 |
| chassis_server | DGX `/data/linglong-project/scripts/chassis_server_v123.py` | :7781 |
| action_router | DGX `/data/linglong-project/scripts/action_router.py` | :7793 |

**Windows v3_log_tail 关键修复（2026-09-24）**：
- Windows 无 sshpass → 用 `paramiko.invoke_shell()` 走 DGX 跳板
- 路径：`/home/user/voice_v3.log`（灵龙上），不是 `/tmp/v3.log`
- 跳板逻辑：`client.connect(DGX) → invoke_shell() → shell.send("sshpass... tail -F ...")`

**v3_gui.py 创建可见窗口**：
```powershell
cmd /K title "v3_log_tail - LingLong ASR Live" && python -u v3_log_tail.py
# 用 CREATE_NEW_CONSOLE 标志让窗口可见
```

## 三、v3 listener 关键代码段

### 3.1 顶部配置（line ~48）

```python
DGX_HOST = "10.86.51.122"
ASR_URL  = f"http://{DGX_HOST}:7780/asr"
LLM_URL  = f"http://{DGX_HOST}:7792/chat"   # ← Orchestrator（不是直连 Ollama！）
TTS_URL  = f"http://{DGX_HOST}:9003/tts_base64"
# LLM_MODEL 已删除（7792 内部路由 local/cloud）
```

### 3.2 llm_call 函数（line ~154）

```python
def llm_call(query, dm=None):
    """走 DGX 7792 Orchestrator (本地 4B + 云端 MiniMax + web_search)"""
    try:
        payload = {"query": query or "你好"}
        if dm and dm.history:
            payload["history"] = [{"role": m["role"], "content": m["content"]} 
                                  for m in dm.history[-6:]]
        resp = requests.post(LLM_URL, json=payload, timeout=60)
        resp.raise_for_status()
        d = resp.json()
        text = d.get("response", "").strip()
        source = d.get("source", "?")
        intent = d.get("intent", "?")
        logger.info(f"[LLM] source={source}, intent={intent}, len={len(text)}")
        return text or "抱歉，没有拿到答案。"
    except Exception as e:
        logger.error(f"LLM 调用失败: {e}")
        return "抱歉，LLM 服务临时不可用。"
```

### 3.3 TTS 后 cooldown 防反馈循环（line ~313）

```python
_last_tts_end = 0.0
COOLDOWN_SEC = 4.0  # mic mute 时长（防 mic 拾取喇叭尾音）

def process_query(query, dm):
    # ... LLM ...
    wav = tts_call(reply)
    if wav:
        play_wav(wav)
        global _last_tts_end
        _last_tts_end = time.time()    # ← 关键
        logger.info(f"[COOLDOWN] mic skip for {COOLDOWN_SEC}s after TTS")
```

### 3.4 主循环冷却检查（line ~360）

```python
while True:
    # TTS cooldown: mic mute 防止 feedback 循环
    cooldown_left = COOLDOWN_SEC - (time.time() - _last_tts_end)
    if cooldown_left > 0:
        logger.info(f"[COOLDOWN] skip ({cooldown_left:.1f}s left)")
        time.sleep(min(0.3, cooldown_left))
        continue
    
    pcm = record_until_vad(device, max_duration_sec=args.max_duration)
    # ... ASR / wake / fallback ...
```

### 3.5 唤醒词 + fallback 分支（line ~751）

```python
matched, query = detect_wake(text)

if matched:
    logger.info(f"[WAKE] query='{query}'")
    process_query(query, dm)
    in_dialog = True
    dialog_start = now

elif in_dialog:
    logger.info(f"[DIALOG] query='{text}'")
    process_query(text, dm)
    dialog_start = now

else:
    # 方案 3：fallback - 无唤醒词也自动送 LLM
    logger.info(f"[FALLBACK] no wake, auto-process query='{text}'")
    process_query(text, dm)
```

## 四、ASR 实时打印 GUI（v3_gui.py）

### 4.1 怎么编写（120 行 Tkinter）

**位置**：`C:\Users\DFET\.openclaw\workspace\v3_gui.py`

**功能**：
- 轮询 `asr_watch_local.log`（mtime 增量读取）
- 按 tag 着色：`[ASR]` 青、`[LLM]` 绿、`[TTS]` 蓝、`[VAD]` 灰、`[WAKE]` 黄、`[NO MATCH]/Error` 红、`[DIALOG]` 紫
- 自动滚到底，限 1500 行
- 按钮：清屏、复制最新一行到剪贴板

**关键代码片段**：

```python
import tkinter as tk
from tkinter import scrolledtext
import re, os, time

LOG = r'C:\Users\DFET\.openclaw\workspace\asr_watch_local.log'

# tag 颜色（ANSI 风）
PATTERNS = [
    (re.compile(r'\[ASR\]'),                              '#00bcd4'),  # 青
    (re.compile(r'\[LLM\] reply'),                        '#43a047'),  # 绿
    (re.compile(r'\[TTS\]'),                              '#1e88e5'),  # 蓝
    (re.compile(r'\[VAD\]'),                              '#9e9e9e'),  # 灰
    (re.compile(r'\[WAKE\]'),                             '#fbc02d'),  # 黄
    (re.compile(r'\[NO MATCH\]|Error|Traceback|too short'), '#e53935'),  # 红
    (re.compile(r'\[DIALOG\]'),                           '#8e24aa'),  # 紫
]

class TailApp:
    def __init__(self, root):
        root.title('灵龙 v3 ASR 实时打印 (v3_gui.py)')
        root.geometry('900x520')

        # 顶部状态栏
        self.status = tk.Label(root, text='启动中...', anchor='w',
                               bg='#263238', fg='white', font=('Consolas', 10))
        self.status.pack(fill='x')

        # 文本区
        self.text = scrolledtext.ScrolledText(root, wrap=tk.NONE,
                                              font=('Consolas', 11),
                                              bg='#0e1116', fg='#e0e0e0')
        self.text.pack(fill='both', expand=True)

        # 注册 tag 颜色
        for pat, color in PATTERNS:
            self.text.tag_configure(color, foreground=color)

        # 按钮
        btn_frame = tk.Frame(root)
        btn_frame.pack(fill='x')
        tk.Button(btn_frame, text='清屏', command=self.clear).pack(side='left', padx=4, pady=4)
        tk.Button(btn_frame, text='复制最新一行', command=self.copy_last).pack(side='left', padx=4, pady=4)

        self._size = 0
        self._buf = ''
        self.root.after(200, self.poll)

    def poll(self):
        try:
            if os.path.exists(LOG):
                size = os.path.getsize(LOG)
                if size < self._size:
                    self._size = 0  # 文件被截断（重启 tail 时）
                if size > self._size:
                    with open(LOG, 'r', encoding='utf-8', errors='replace') as f:
                        f.seek(self._size)
                        chunk = f.read(size - self._size)
                        self._size = size
                        self._buf += chunk
                        if len(self._buf) > 200000:
                            self._buf = self._buf[-100000:]
                        while '\n' in self._buf:
                            line, self._buf = self._buf.split('\n', 1)
                            self._append_line(line)
                    self.status.config(text=f'监听中: {LOG}  大小: {size:,} bytes')
        except Exception as e:
            self._append_line(f'[GUI-ERR] {e}', color='#e53935')
        self.root.after(300, self.poll)

    def _append_line(self, line, color=None):
        if not line:
            return
        applied = color
        if applied is None:
            for pat, c in PATTERNS:
                if pat.search(line):
                    applied = c
                    break
        tag = applied if applied else 'plain'
        self.text.insert(tk.END, line + '\n', tag)
        self.text.see(tk.END)
        # 限制最大行数
        line_count = int(self.text.index('end-1c').split('.')[0])
        if line_count > 1500:
            self.text.delete('1.0', '200.0')

if __name__ == '__main__':
    root = tk.Tk()
    TailApp(root)
    root.mainloop()
```

### 4.2 怎么激活（Windows 端）

```powershell
# 方式 A：Start-Process（推荐，会弹出可见窗口）
Start-Process -FilePath 'python.exe' `
  -ArgumentList 'C:\Users\DFET\.openclaw\workspace\v3_gui.py' `
  -WorkingDirectory 'C:\Users\DFET\.openclaw\workspace' `
  -WindowStyle Normal

# 方式 B：双击 v3_gui.py（要先 cd 到 workspace，否则相对路径错）

# 验证窗口已弹出（看 MainWindowTitle）
Get-Process python | Where-Object MainWindowTitle -match 'v3 ASR'
# → 预期：Id 3140  Title "灵龙 v3 ASR 实时打印 (v3_gui.py)"
```

**窗口标题**：`灵龙 v3 ASR 实时打印 (v3_gui.py)`（用于 Get-Process 验证）

**激活顺序**（缺一不可）：
1. **先**启动 `v3_log_tail.py`（DGX→灵龙→本地日志管道）
2. **再**启动 `v3_gui.py`（GUI 读 `asr_watch_local.log`）
3. **最后**确认灵龙 v3 listener 在跑（看 `/tmp/v3.log` 有 `[VAD] listening`）

### 4.3 调试 / 杀进程

```powershell
# 查 GUI 进程（按窗口标题）
Get-Process python | Where-Object MainWindowTitle -match 'v3 ASR'

# 杀 GUI（不影响 v3_log_tail 和灵龙 v3）
Stop-Process -Id <PID> -Force

# 查所有 python 进程（看启动时间区分）
Get-Process python | Select-Object Id, StartTime

# 查日志订阅进程（无窗口，靠 StartTime 区分 - 比 GUI 早启动）
```

## 五、四大核心模块实现细节

### 5.1 VAD 实现（webrtcvad + arecord）

**库**：`webrtcvad` (Google WebRTC 语音活动检测，C 实现 Python 绑定)

**核心参数**（v3 listener line 38-44）：

```python
SAMPLE_RATE = 16000          # 16kHz（ASR 要求）
CHANNELS = 1                 # 单声道
DTYPE = 'int16'              # 16-bit PCM
VAD_FRAME_MS = 30            # WebRTC VAD 必须用 10/20/30 ms
VAD_AGGRESSIVENESS = 2       # 0=最宽松, 3=最严格 (Leo 用 2)
SILENCE_FRAMES_TO_END = 15   # 静音 450ms = 一句话结束
MIN_SPEECH_FRAMES = 5        # 至少 150ms 才算语音（过滤噪声）
MAX_DURATION_SEC = 15        # 单句最长 15 秒
```

**核心函数**（`record_until_vad`）：

```python
def record_until_vad(input_device, sample_rate=SAMPLE_RATE,
                     frame_ms=VAD_FRAME_MS,
                     silence_to_end=SILENCE_FRAMES_TO_END,
                     min_speech=MIN_SPEECH_FRAMES,
                     max_duration_sec=15):
    vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
    frame_bytes = sample_rate * frame_ms // 2  # 16-bit = 2 bytes/sample
    speech_buf = []
    voiced_count = 0
    silence_count = 0
    start = time.time()
    
    # arecord 子进程（流式）
    proc = subprocess.Popen(
        ['arecord', '-D', input_device, '-f', 'S16_LE',
         '-r', str(sample_rate), '-c', str(CHANNELS), '-q'],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        bufsize=frame_bytes * 4
    )
    
    try:
        while True:
            frame = proc.stdout.read(frame_bytes)
            if not frame:
                break
            
            is_speech = vad.is_speech(frame, sample_rate)
            
            if is_speech:
                voiced_count += 1
                silence_count = 0
                speech_buf.append(frame)
                if voiced_count == 1:
                    logger.info("[VAD] speech START")
            else:
                if voiced_count > 0:
                    silence_count += 1
                    speech_buf.append(frame)
                    if silence_count >= silence_to_end:
                        if voiced_count >= min_speech:
                            logger.info(f"[VAD] speech END ({voiced_count} frames)")
                            return b''.join(speech_buf)
                        else:
                            logger.info(f"[VAD] too short ({voiced_count} frames), ignore")
                            return None
            
            if time.time() - start > max_duration_sec:
                logger.info("[VAD] timeout, return")
                return b''.join(speech_buf) if voiced_count > 0 else None
    finally:
        proc.terminate()
```

**关键陷阱**：
- `webrtcvad` 必须 16kHz/8kHz + int16 + 单声道，其他采样率不支持
- 帧长必须是 10/20/30ms，不能 25ms（API 限制）
- `VAD_AGGRESSIVENESS=0` 会把空调声当成语音，3 太严导致漏句

**为什么用灵龙端 VAD 而不是 DGX 端**：
- 灵龙 ARM CPU 跑 webrtcvad 几乎 0 CPU（轻量级 C 库）
- DGX 流式 ASR (vllm 0.22) 在 Spark 上会卡（CUDA graph + FlashInfer）
- 折中：灵龙端 VAD → 完整 wav → DGX 一次性 ASR（伪流式）

### 5.2 ASR 实现（Qwen3-ASR 0.6B @ DGX :7780）

**服务端**（DGX）：FastAPI + Qwen3-ASR-0.6B 模型

**客户端调用**（v3 listener `asr_call`）：

```python
ASR_URL = f"http://{DGX_HOST}:7780/asr"

def asr_call(wav_bytes):
    """上传 wav → DGX ASR，返回识别文本"""
    files = {'audio': ('chunk.wav', io.BytesIO(wav_bytes), 'audio/wav')}
    data = {'language': 'zh', 'task': 'transcribe'}
    resp = requests.post(ASR_URL, files=files, data=data, timeout=30)
    resp.raise_for_status()
    
    # ⚠️ 用 resp.content.decode('utf-8') 不用 resp.text
    # resp.text 会按 ISO-8859-1 解码 → 中文 UTF-8 字节变乱码（mojibake）
    body = json.loads(resp.content.decode('utf-8'))
    text = body.get('text', '').strip()
    
    logger.info(f"[ASR] '{text}'")
    return text
```

**WAV 格式要求**：
- 16kHz 采样率
- 单声道
- 16-bit PCM（int16 little-endian）
- VAD 输出的字节流要加 WAV header：

```python
def pcm_to_wav_bytes(pcm_data, sample_rate=SAMPLE_RATE, channels=CHANNELS):
    """VAD 输出的 raw PCM bytes → 完整 WAV 字节流"""
    import wave
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return buf.getvalue()
```

**已知问题**：
- mojibake（见 5.1 坑）—— 用 `resp.content.decode('utf-8')` 修复
- 部分短句识别为空 → logger.info("[ASR] empty, ignore")
- B107A6 mic 拾取 USB 喇叭尾音 → 触发 cooldown（见三.3）

### 5.3 LLM Hybrid 实现（DGX :7792 Orchestrator）

**架构**（DGX 端 `/data/linglong-project/scripts/llm_orchestrator.py`）：

```
┌──────────────────────────────────────────────────┐
│ :7792 Orchestrator (FastAPI)                      │
│                                                   │
│  POST /chat {query, history?}                      │
│      ↓                                                    │
│  Intent Detector (规则 + LLM 分类)                   │
│      ├─ search  → web_search tool → MiniMax-M3 云端 │
│      ├─ chat    → Ollama qwen3-vl:4b 本地（普通问答）│
│      └─ 其他    → fallback "I cannot"              │
│                                                   │
│  返回: {response, source: local|cloud, intent,     │
│         elapsed_ms, timestamp}                     │
└──────────────────────────────────────────────────┘
```

**为什么用 Orchestrator 而不是直连 LLM**：

| 方案 | 优点 | 缺点 |
|------|------|------|
| 直连 Ollama :11434 | 简单 | 本地 4B 无 web 能力 → 永远"无法提供实时" |
| 直连 MiniMax 云端 | web_search 全 | 每次都花钱，慢 1-2 秒 |
| **Orchestrator :7792** | **本地快 + 云端智能自动切换** | **多一层 hop** |

**v3 listener 集成代码**（见 3.2）：

```python
LLM_URL = f"http://{DGX_HOST}:7792/chat"  # 不是 11434！

resp = requests.post(LLM_URL, json={"query": query}, timeout=60)
d = resp.json()
# d["response"]  - 最终文本
# d["source"]    - "local" / "cloud"（看是哪个模型答的）
# d["intent"]    - "search" / "chat" / "fallback"
```

**本地 vs 云端 hybrid 触发规则**（7792 内部）：

| Query 类型 | 路由 | 例子 |
|------------|------|------|
| 实时信息查询 | 云端 + web_search | "今天深圳天气"、"美元汇率"、"深圳指数" |
| 时间/日期/星期 | 云端（自带知识） | "今天几号"、"今天星期几" |
| 一般聊天 | 本地（快） | "你是谁"、"讲个笑话"、"你好" |
| 实体识别/动作请求 | 本地 | "把灯打开" |

**直连测试**：

```bash
curl -X POST http://10.86.51.122:7792/chat \
  -H "Content-Type: application/json" \
  -d '{"query":"今天深圳天气怎么样"}'

# 预期返回：
# {
#   "response": "今天深圳天气（9月21日）多云转晴，最高30度...",
#   "source": "cloud",
#   "intent": "search",
#   "elapsed_ms": 2300,
#   "timestamp": "2026-09-21T..."
# }
```

**模型配置**：
- 本地：Ollama `qwen3-vl:4b-instruct-q4_K_M` @ `http://localhost:11434`
- 云端：MiniMax-M3（anthropic API @ `https://api.minimaxi.com/anthropic`）
- API Key：环境变量 `MINIMAX_API_KEY`（包月，token 免费）

### 5.4 TTS 改成熟男声（CosyVoice VoiceDesign @ DGX :9003）

**部署**（DGX `/data/linglong-project/scripts/tts_v2_server.py`）：

- 模型：**CosyVoice VoiceDesign**（指令控制音色，不需参考音频）
- 端口：**9003**（不是 9002！）
- 端点：`POST /tts_base64 {text, language, instruct, ...}` → 返回 `{audio_base64: "..."}`
- instruct prompt 控制音色

**成熟男声配置**（默认）：

```python
TTS_URL = f"http://{DGX_HOST}:9003/tts_base64"

def tts_call(text):
    """调用 DGX TTS，返回 wav 字节流"""
    payload = {
        "text": text,
        "language": "Chinese",
        "speaker": "TTS-02",           # ← 成熟男声
        "instruct": "成熟男声，磁性，普通话标准，语速中等",
        "use_cache": True,             # 同 query 命中 TTS cache（毫秒级返回）
        "auto_cantonese": False        # ← 不要粤语
    }
    resp = requests.post(TTS_URL, json=payload, timeout=120)
    resp.raise_for_status()
    d = resp.json()
    b64 = d.get('audio_base64') or d.get('audio_b64')
    return base64.b64decode(b64) if b64 else None
```

**可用音色**（CosyVoice VoiceDesign 预设）：

| Speaker ID | 描述 | 适用 |
|-----------|------|------|
| **TTS-02** | **成熟男声（默认）** | **新闻播报、客服、专业场合** |
| TTS-01 | 年轻男声 | 活泼、社交场合 |
| TTS-03 | 温柔女声 | 助手、客服 |
| TTS-04 | 童声 | 卡通、教育 |

**切换音色**：

```python
# 改成年轻女声
payload["speaker"] = "TTS-03"
payload["instruct"] = "温柔女声，年轻，普通话标准"

# 改成活泼男声
payload["speaker"] = "TTS-01"
payload["instruct"] = "年轻男声，活泼，略带调皮"
```

**播放**（v3 listener `play_wav`）：

```python
def play_wav(wav_bytes):
    """aplay 到 USB 喇叭"""
    tmp = "/tmp/ll_reply.wav"
    with open(tmp, 'wb') as f:
        f.write(wav_bytes)
    rc = subprocess.run(f"aplay -D plughw:3,0 -q {tmp}", shell=True)
    return rc.returncode == 0
```

**关键坑**：

- **旧 9002 是粤语 TTS**（已废弃但端口还在）—— 必须确认是 9003
- `use_cache=True` 同 query 第二次毫秒返回，但 **TTS cache 用显存**（GPU memory）
- `instruct` 字段必须中文描述音色特征
- `auto_cantonese=True` 会自动把 query 翻成粤语朗读（关掉！）

**TTS 性能**：

| 项目 | 数据 |
|------|------|
| 平均响应 | 1.5 秒（首次） / 0.05 秒（缓存） |
| 单次音频 | 5-15 秒 wav（200-400 KB） |
| GPU 占用 | 2-3 GB（VoiceDesign 模型） |

---

## 十一、2026-09-24 当天补充（灵龙底盘自转事故 + action_stop_timer 修复）

### 11.1 事故时间线

| 时间 | 事件 |
|------|------|
| 09:47 | Leo 报告灵龙上电后自己原地转 |
| 09:50 | Leo 下发 chassis/stop 停了 |
| 10:10 | Leo 拍板 TCP 5秒断联检测 |
| 10:24 | TCP 5秒修复部署完成 |
| 11:11 | Leo 再次报告自转（action_stop_timer bug）|
| 11:45 | action_stop_timer 修复验证通过 |

### 11.2 新增故障排查

| 症状 | 根因 | 修复 |
|------|------|------|
| 底盘自转不停 | do_start() 收了 duration 但没用 | 函数体内加 action_stop_timer |
| "灵龙停" 不响应 | do_POST 没传 duration | do_POST 显式提取并传递 duration |
| watchdog 线程静默退出 | 函数内引用全局变量未声明 global | 加 `global var1, var2, ...` |
| USB mic 无声音 | plughw:1,0 = HDMI 0 无 capture | 改 plughw:0,0（Card 0 B107A6）|
| USB 设备拔插后 mic 挂了 | ALSA card 编号变了 | 重查 arecord -l，不能假设备号不变 |

### 11.3 action_stop_timer 完整修复链路

```
action_router.py: chassis_pulse(duration=0.873)
  → POST /chassis/start {duration: 0.873}
    → do_POST /chassis/start: body.get('duration') → do_start(..., duration=0.873)
      → do_start(): action_stop_timer = time.time() + 0.873
        → watchdog_loop(): if now >= action_stop_timer → AUTO STOP
```

**任意一步漏了 = 底盘不停**

### 5.1 Mojibake 编码 bug（GUI 显示乱码，不影响 TTS）

- **症状**：GUI / `tail /tmp/v3.log` 看到 `'������Ԫ...'` 而不是中文
- **真相**：v3 listener 用 `resp.text` 读 HTTP 响应，requests 用 ISO-8859-1 解码 → 中文 UTF-8 字节变乱码
- **影响**：仅显示，**LLM 实际收到的中文是对的，TTS 也正常播放**
- **修复**（v3 listener）：用 `resp.content.decode('utf-8')` 替代 `resp.text`
- **临时绕过**：GUI 用 `errors='replace'` 容忍

### 5.2 TTS → mic 反馈循环（最严重）

- **症状**：LLM 答完一句话，TTS 播放 → mic 拾取尾音 → ASR 又识别 → 又送 LLM → 又 TTS，**无限循环**
- **根因**：USB 喇叭 (card 3) 和 B107A6 mic (card 2) 物理距离太近
- **修复**：见 3.3 / 3.4，加 `_last_tts_end` + cooldown 4 秒

### 5.3 唤醒词强制导致漏答

- **症状**：用户说"今天深圳天气怎么样"（没说"灵龙"）→ v3 直接 `[NO MATCH] ignore`
- **修复**：见 3.5，`else` 分支改成自动送 LLM（方案 3 fallback）

### 5.4 v3 直连 Ollama 不走 Orchestrator（最坑）

- **症状**：web_search 不触发，永远"无法提供实时"
- **真相**：v3 默认 `LLM_URL=http://DGX:11434/api/generate`（本地 4B，无 web 能力）
- **修复**：见 3.1 / 3.2，改成 `http://DGX:7792/chat`（Orchestrator 路由）

### 5.5 arecord 返回 0 bytes（高频 warning）

- **症状**：log 里 `arecord ���� 0 bytes, ���� 960` 每秒几十次
- **原因**：arecord 的 stdin/stdout 管道被外部 kill 后没正确重启
- **影响**：无功能影响（VAD 正常），仅刷屏
- **解决**：忽略 / 或换 `sounddevice` 库

## 六、标准操作流程

### 6.1 首次部署（2026-09-24 更新设备路径）

```bash
# 灵龙端（$LINGLONG_USER@$LINGLONG_HOST）
sshpass -p $LINGLONG_PASSWORD ssh $LINGLONG_USER@$LINGLONG_HOST \
  "pkill -f linglong_voice_v3.py; sleep 2; \
   nohup /usr/bin/python3 -u /home/user/linglong_voice_v3.py \
   --device plughw:0,0 --speaker-device plughw:3,0 \
   > /home/user/voice_v3.log 2>&1 < /dev/null &"

# Windows 端（在你机器上）
cd C:\Users\DFET\.openclaw\workspace
python v3_log_tail.py                                           # 日志订阅（paramiko 跳板）
Start-Process cmd.exe -ArgumentList '/K title v3_log_tail - LingLong ASR Live && python -u v3_log_tail.py' -WindowStyle Normal  # GUI
```

### 6.2 修改 v3 listener（标准流程）

```python
# scripts/_patch_v3_xxx.py 模板
import sys, time, base64
sys.path.insert(0, r'C:\Users\DFET\.openclaw\workspace\scripts')
from dgx_ssh import dgx_run

# 1. 备份
ts = time.strftime('%Y%m%d_%H%M%S')
dgx_run(f'sshpass -p $LINGLONG_PASSWORD ssh $LINGLONG_USER@$LINGLONG_HOST '
        f'"cp /home/user/linglong_voice_v3.py '
        f'/home/user/linglong_voice_v3.py.bak.{ts}"')

# 2. 上传并执行补丁（用 base64 避免 shell 转义）
with open('scripts/_patch_inner.py', 'rb') as f:
    b64 = base64.b64encode(f.read()).decode()
dgx_run(f'sshpass -p $LINGLONG_PASSWORD ssh $LINGLONG_USER@$LINGLONG_HOST '
        f'"echo {b64} | base64 -d > /tmp/_patch.py && python3 /tmp/_patch.py"')

# 3. 重启
dgx_run(f'sshpass -p $LINGLONG_PASSWORD ssh $LINGLONG_USER@$LINGLONG_HOST '
        f'"pkill -f linglong_voice_v3.py; sleep 2; '
        f'setsid nohup /usr/bin/python3 -u /home/user/linglong_voice_v3.py '
        f' > /tmp/v3.log 2>&1 < /dev/null &"')
```

### 6.3 故障排查清单

| 症状 | 检查 |
|------|------|
| GUI 不刷新 | `Get-Item asr_watch_local.log` 的 LastWriteTime 是否更新？否则 v3_log_tail 死了 |
| GUI 不显示 | `Get-Process python` 看 MainWindowTitle 是否含 "v3 ASR" |
| v3 不响应 | 灵龙 `ps -ef \| grep linglong_voice_v3` |
| LLM 答"无法提供实时" | 7792 直连测试：`curl -X POST http://DGX:7792/chat -d '{"query":"今天深圳天气"}'` |
| TTS 循环播放 | 看 /tmp/v3.log 是不是有连续 `[FALLBACK] → [LLM] → [TTS] → [FALLBACK]` 循环 |
| mojibake 乱码 | 已知 bug，不影响功能，忽略 |

## 七、关键里程碑（2026-09-21）

- ✅ v3 全链路打通（mic → VAD → ASR → LLM → TTS → 喇叭）
- ✅ TTS 反馈循环修复（cooldown 4s）
- ✅ 唤醒词 fallback（无"灵龙"也能答）
- ✅ LLM 走 7792 Orchestrator（web_search 触发）
- ✅ TTS 切普通话（CosyVoice VoiceDesign @ :9003）
- ✅ ASR GUI 实时显示（v3_gui.py Tkinter）

## 八、相关 SKILL

- `skills/linglong-vla-SKILL.md` — VLA 整体（含机械臂硬件）
- `skills/linglong-audio-SKILL.md` — 音频系统（USB 喇叭/麦）
- `skills/linglong-sdk-debug-SKILL.md` — SDK 调试
- `skills/linglong-dgx-local-SKILL.md` — DGX 本地部署

## 九、文件清单（2026-09-21 当天产出）

- `C:\Users\DFET\.openclaw\workspace\v3_log_tail.py` — 日志订阅
- `C:\Users\DFET\.openclaw\workspace\v3_gui.py` — ASR GUI（120 行 Tkinter）
- `C:\Users\DFET\.openclaw\workspace\scripts\_patch_v3_*.py` — 补丁脚本集
- `C:\Users\DFET\.openclaw\workspace\asr_watch_local.log` — 本地日志

---

_最后更新: 2026-09-21 13:12_

---

## 十、2026-09-22 当天补充（Leo 现场确认）

### 10.1 MiniMax API KEY（实际值，写入 SKILL 不写入 github）

```bash
# DGX 端 .bashrc / 环境变量
export MINIMAX_API_KEY="sk-cp-${MINIMAX_API_KEY}"
```

- **用途**：云端 LLM（实时问答 / web_search / 时间 / 汇率 / 股票）
- **性质**：**包月**，token 无限
- **来源**：David 在 2026-06-20 确认
- **找 Key 流程**：DGX `grep -r MINIMAX_API_KEY ~/.bashrc /etc/environment /data/` → 找到实际环境变量值
- **不要在 SKILL 写明文 Key 进 git 仓库**（写入 SKILL 本地即可，**永远不进 git push**）

### 10.2 唤醒词 - 龙字宽泛性（Leo 9/22 拍板）

**问题**：固定只匹配 "灵龙" → 用户说 "凌龙" / "凌隆" / "零龙" ASR 识别后不命中。

**解决**：唤醒词改成 **`灵|凌|零|临|令令聆` + `龙|隆|笼|咙|咙`** 的模糊匹配。

**v3 listener 实现**：

```python
import re

# 宽泛唤醒词: 灵/凌/零/临 + 龙/隆/笼
WAKE_PATTERN = re.compile(
    r'(灵|凌|零|临|令|聆|铃|灵|陵|岭|凌|琳|临|邻|遴)'  # 前字
    r'\s*'
    r'(龙|隆|笼|咙|茏|胧|咙|笼|龙|隆|咙|龙)'  # 后字
)
WAKE_PATTERN_LOOSE = re.compile(r'灵\s*龙')

def detect_wake(text):
    # 宽泛匹配唤醒词, 返回 (matched, query_remainder)
    m = WAKE_PATTERN.search(text)
    if not m:
        m = WAKE_PATTERN_LOOSE.search(text)
    if not m:
        return False, None
    query = text[:m.start()] + text[m.end():]
    query = query.strip(' ,,.?!')
    return True, query if query else None
```

**测试匹配**：

| 用户说 | ASR 识别 | 唤醒命中 |
|--------|----------|---------|
| 灵龙今天深圳天气 | 灵龙今天深圳天气 | ✅ |
| 凌龙你好 | 凌龙你好 | ✅ |
| 零龙讲笑话 | 零龙讲笑话 | ✅ |
| 临龙帮忙 | 临龙帮忙 | ✅ |
| 小灵龙你好 | 小灵龙你好 | ✅ (灵龙子串) |
| 小零龙 | 小零龙 | ✅ |
| 玲珑（无龙字） | 玲珑 | ❌（不命中） |

### 10.3 象声词忽略（Leo 9/22 拍板）

**问题**：用户说 "嗯" / "啊" / "哎" / "哦" / "呃" → v3 当成 query 送 LLM → 浪费 token + 答非所问。

**解决**：v3 listener 主循环加 `ONOMATOPOEIA` 集合，命中直接 continue。

```python
# 象声词 / 语气词过滤（不算 query）
ONOMATOPOEIA = {
    # 单字
    '嗯', '啊', '哎', '哦', '噢', '呃', '呵', '嘿', '喂', '哈', '呀',
    '喔', '诶', '欸', '唉', '呜', '嘘',
    # 双字
    '嗯嗯', '啊啊', '哎哎', '哦哦', '呃呃', '那个', '这个', '然后',
    '好吧', '行了', '是的', '对啊', '没有',
    # 三字+
    '那个那个', '这个这个', '嗯嗯嗯',
}

def is_onomatopoeia(text):
    cleaned = text.strip().rstrip(' ,,.?!')
    if cleaned in ONOMATOPOEIA:
        return True
    if len(cleaned) <= 2:
        return True
    return False

# 主循环：
text = asr_call(wav_bytes)
if is_onomatopoeia(text):
    logger.info('[ONOMATOPOEIA] skip: ' + repr(text))
    continue
```

### 10.4 流式播报（Leo 9/22 验证 OK）

**架构**：LLM 流式 chunk -> 整句送 TTS（不是 chunk 级 TTS）

```python
# llm_orchestrator.py 流式接口
@app.post('/chat_stream')
async def chat_stream(query: str):
    headers = {'Content-Type': 'text/event-stream'}
    async def gen():
        async for chunk in call_llm_stream(query):
            yield f'data: {json.dumps({"chunk": chunk})}\n\n'
        yield 'data: [DONE]\n\n'
    return StreamingResponse(gen(), headers=headers)
```

**实测延迟**（Leo 现场确认）：
- LLM 流式推完 -> TTS 整句 0.5-1 秒出首音
- 不阻塞 LLM（流式 + 整句 TTS 是最佳折中）
- **不要做 chunk 级 TTS**（首块出来就播）-> 音质碎 + 蹦字

### 10.5 dryrun 占位（arm 动作临时态，Leo 9/22 拍板）

**背景**：`action_router.py` 当前对 arm 动作返回 `dry_run`，**不真正调用 voice_cmd_server**。

**为什么**：9/21 上传 action_router.py 时，voice_cmd_server 的 `/intent` HTTP 端点还没建。

```python
# scripts/action_router.py: dispatch()
if target == 'arm':
    if os.environ.get('ACTION_ROUTER_DRYRUN', '1') == '1':
        # dryrun 占位（默认）— 只记录意图，不发动作
        logger.info('[DRYRUN] ' + action['id'])
        return {'status': 'dry_run', 'action_id': action['id']}
    # 否则真正发到 voice_cmd_server
    return call_voice_cmd_server(action, params)
```

**启用真接**（Leo 拍板后）：

```bash
# DGX 端启动 action_router 时
export ACTION_ROUTER_DRYRUN=0
/data/linglong-project/envs/vla/bin/python -m action_router
```

### 10.6 v3 启动标准命令（Leo 9/22 确认）

```bash
# 灵龙端（$LINGLONG_USER@$LINGLONG_HOST）
sshpass -p $LINGLONG_PASSWORD ssh $LINGLONG_USER@$LINGLONG_HOST \
  "pkill -f linglong_voice_v3.py; sleep 2; \
   setsid nohup /usr/bin/python3 -u /home/user/linglong_voice_v3.py \
   > /tmp/v3.log 2>&1 < /dev/null &"
```

**关键修正**：用 setsid + nohup + disown 三连，让 v3 进程脱离父 shell。之前 ssh exec_command timeout 会杀 v3 子进程。

---
_更新_2026-09-22 16:55_
