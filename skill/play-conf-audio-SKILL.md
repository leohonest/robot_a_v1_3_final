# 灵龙预录音频 Conf 机制 SKILL

> 适用：灵龙 listener (linglong_voice_v3.py) + DGX action_router
> 目的：WAKE 触发时立即在灵龙侧异步播放"收到"中文男声预录音频
> 创建：2026-09-28（Leo 多次拍板设计）

---

## 1. 设计原则（Leo 12:09 简化架构）

**关键约束**：
- DGX **不放音**（DGX 只发 conf 消息内容）
- listener **直连** DGX :7793 action_router_server（不走 orchestrator）
- "收到"音频**只**由 listener 在 WAKE 时触发，与 TTS **完全独立并行**
- 播放用 `subprocess.Popen` **异步**（不阻塞 listener 主循环）

**链路**：
```
ASR → detect_wake (含灵龙/龙/珑) 
 ├─ IGNORE → 不处理
 └─ WAKE → 异步 play_conf_audio（独立流）
              ↓ (并行)
           process_query
              ├─ action_check (POST :7793)
              │    ├─ 命中 → TTS 状态文本
              │    └─ 未命中 → llm_call (POST :7792) → TTS 回复
```

---

## 2. 预录 WAV 文件

**位置（双副本）**：
- **灵龙**：`/home/user/sounds/shoudao.wav`（57644 bytes, 24kHz mono, MD5 c4e47d6167897c140ca3864a5b57b201）
- **DGX**：`/data/linglong-project/sounds/shoudao.wav`（57644 bytes, 同步副本）

**生成流程**（Leo 11:41 方案 2）：
1. TTS 生成中文男声"收到"（instruct="中年男性，声音沉稳，简洁肯定"）
2. POST `http://10.86.51.122:9003/tts_base64` 拿 base64
3. decode → 57644 bytes WAV
4. SCP 到灵龙 + DGX（md5 校验一致）

**优先用 audio_b64（最新），fallback 到本地 wav 文件**

---

## 3. Listener 端实现

### 3.1 异步播放 helper

```python
def play_conf_audio(audio_b64: str = None):
    """
    Leo 2026-09-28 12:09: 异步播放"收到"音频（不阻塞调用方）。
    用 subprocess.Popen 让 aplay 后台跑，WAKE 触发后立即返回。
    """
    wav_path = '/home/user/sounds/shoudao.wav'
    if audio_b64:
        try:
            conf_wav = base64.b64decode(audio_b64)
            tmp = '/tmp/_ll_conf_play.wav'
            with open(tmp, 'wb') as f:
                f.write(conf_wav)
            subprocess.Popen(f"aplay -D {SPEAKER_DEVICE} -q {tmp}", shell=True)
            logger.info(f"[CONF] async playing {len(conf_wav)} bytes audio_b64")
            return
        except Exception as e:
            logger.error(f"[CONF] audio_b64 decode failed: {e}, fallback to local file")
    try:
        subprocess.Popen(f"aplay -D {SPEAKER_DEVICE} -q {wav_path}", shell=True)
        logger.info(f"[CONF] async playing local {wav_path}")
    except Exception as e:
        logger.error(f"[CONF] fallback play failed: {e}")
```

**关键**：`subprocess.Popen`（不 `run`）——`run` 是同步阻塞，会卡住 listener 主循环。

### 3.2 main loop WAKE 块

```python
if matched:
    # === Leo 12:09: WAKE 触发立即异步放"收到"，与 TTS 完全独立 ===
    play_conf_audio()
    logger.info(f"[WAKE] query='{query}' (joined buffer: {asr_buffer})")
    process_query(query, dm)
```

**放音和 process_query 完全独立**：
- play_conf_audio() 立即返回（Popen 不等）
- process_query 跑 action_check → TTS 状态 / LLM 路径
- 两条线**并行**

---

## 4. DGX action_router 端 audio_b64 注入

### 4.1 action_router.py 修改

**say_shoudao 函数**：从读预录 wav（替代原来的 TTS 调用）

```python
def say_shoudao() -> Optional[str]:
    wav_path = '/data/linglong-project/sounds/shoudao.wav'
    try:
        with open(wav_path, 'rb') as f:
            return base64.b64encode(f.read()).decode('ascii')
    except Exception as e:
        logger.warning(f'load shoudao.wav failed: {e}')
    return None
```

**所有 dispatch 路径加 audio_b64**：
- `chassis_pulse` return：已有 audio_b64（行 366）
- `chassis_stop` return：加 audio_b64（行 374 改动）
- `arm dispatch` ok/error：加 audio_b64（行 518 改动）
- `agv_3051_navigate` ok：加 audio_b64（行 553 改动）

**Leo 12:00 重要决定**：**orchestrator 不需要改**——listener 直连 7793 不走 orchestrator，所以 orchestrator.py 改动**回退**。

### 4.2 listener 直连 7793

```python
def action_check(query: str) -> dict:
    ACTION_URL = f"http://{DGX_HOST}:7793/action"
    try:
        resp = requests.post(ACTION_URL, json={"query": query}, timeout=5)
        resp.raise_for_status()
        d = resp.json()
        matched = d.get("matched", False)
        result = d.get("result", {})
        audio_b64 = result.get("audio_b64") if matched else None
        action_id = d.get("action_id") if matched else None
        params = d.get("params", {})
        return {
            "matched": matched,
            "audio_b64": audio_b64,
            "action_id": action_id,
            "params": params,
            "result": result,
        }
    except Exception as e:
        return {"matched": False, "audio_b64": None, "action_id": None, "params": {}, "result": {}}
```

---

## 5. 升级触发条件（什么时候 listener 重启）

**必须重启 listener**：
- 改了 listener v3 代码（play_conf_audio / WAKE 块 / process_query / filler 逻辑等）
- 改了 ALSA USB 设备参数（plughw:N,M）
- 改了 ASR/LLM/TTS URL

**不需要重启 listener**（只读）：
- 改了 DGX action_router.py（listener 不直接读，但走 7793 时拿 audio_b64）—— 但**仍要重启 action_router_server 加载新代码**
- 改了 DGX orchestrator.py（listener 不走 orchestrator 时）

---

## 6. 故障排查

### 6.1 放 conf 没声音
1. 看 listener log 有没有 `[CONF] async playing ... bytes`
2. 看 aplay 进程：`ssh $LINGLONG_USER@$LINGLONG_HOST ps aux | grep aplay`
3. 测 aplay 直接：`ssh $LINGLONG_USER@$LINGLONG_HOST aplay -D plughw:0,0 /home/user/sounds/shoudao.wav`
4. 检查 wav 文件 md5：灵龙端 `md5sum /home/user/sounds/shoudao.wav`（应该是 c4e47d...）

### 6.2 audio_b64 太大导致 POST 超时
- 57644 bytes → base64 = 76860 字符 + JSON 包 ≈ 80KB
- listener → DGX :7793 POST 默认 5s 超时够用
- 但 listener → :7792 如果走 orchestrator 会慢（避免走 orchestrator）

### 6.3 conf 音频和 TTS 撞车
- conf 是独立 Popen，不等播放完
- TTS 也是独立 Popen（play_wav 用 subprocess.run 同步 vs 用 Popen 异步待确认）
- **冲突解决**：listener 用 COOLDOWN_SEC = 4.0，conf 音频时长约 0.5-1s，TTS 文本通常 1-3s
- mic mute 期间 conf 已经被 mic 录到 → 自激循环风险
- **缓解**：conf 音量小 + TTS 音量正常 + 用 COOLDOWN 让 mic mute

---

_最后更新：2026-09-28 13:11_