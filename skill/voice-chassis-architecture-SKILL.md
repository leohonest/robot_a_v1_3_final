# Voice → Chassis 架构 SKILL

> 创建: 2026-09-23 (Leo 拍板 UDP 风格架构)
> 状态: 测试中, 灵龙下电充电

## 核心认知

### ⛔ 铁律：灵龙底盘异常 → 第一件事 POST /chassis/stop（Leo 2026-09-24 11:34 拍板）

**场景**：底盘自转不停 / 不响应 / 状态异常

**步骤**：
1. `POST http://10.86.51.122:7781/chassis/stop`
2. `GET http://10.86.51.122:7781/chassis/health` 验证 `active=false, vx=0, omega=0`
3. 然后才开始排查 log / 改代码 / SSH

**理由**：灵龙失控 = 物理设备在动 = 现场安全风险；stop 毫秒级生效永远不亏。

### voice 路径 vs APP 路径 是不同消息机制

| | voice 路径 | APP 路径 |
|---|---|---|
| 触发 | ASR 识别 wake word + 意图 | 用户按住按钮 |
| 消息机制 | **UDP 风格** — 发完就扔 | **TCP 长连接** — 持续保持 |
| 持续时间 | 指令自带 duration_s | 用户按多久就多久 |
| 是否需要 heartbeat | **不需要** (chassis_server 自己 timer) | **需要** (网络断了要保活) |
| watchdog 用途 | 按 voice_deadline 触发 AUTO STOP | heartbeat 超时触发 AUTO STOP |
| 错误处理 | 没收到就重发 | watchdog AUTO STOP |

**关键设计**: voice 路径是 **fire-and-forget** (UDP), APP 路径是 **keep-alive** (TCP+heartbeat).

## 架构组件

### 1. listener (灵龙 192.168.1.12)
- 监听麦克风 (plughw:3,0 B107A6)
- ASR (DGX :7780)
- wake word 检测
- 调 `requests.post(:7793/action, json={query})`

### 2. action_router (DGX :7793)
- 路由 + 意图识别
- `chassis_pulse(action, params)`:
  ```python
  # UDP 风格: 只发 start, 不后台线程, 不 heartbeat
  requests.post(:7781/chassis/start, json={
    'vx': vx, 'omega': omega, 'session_id': session_id, 'duration': duration_s
  })
  return {'status': 'ok', ...}  # 立即返回
  ```

### 3. chassis_server (DGX :7781)
- 接受 start 带 `duration` 参数
- 设置 `voice_deadline = time.time() + duration`
- watchdog 优先级:
  1. `voice_deadline` 到期 → AUTO STOP (voice 路径)
  2. heartbeat 超时 (1.0s) → AUTO STOP (APP 路径)

## 关键修复

### 1. action_stop_timer 静默 bug（2026-09-24 致命事故 ⛔）

**症状**：灵龙上电后自己原地转，Leo "灵龙停"/"灵龙停止" 不响应，持续 130 秒。

**根因（多重静默 bug 链）**：
1. action_router.py 9/23 17:30 改成 UDP 风格，正确发了 `duration` 参数（5度 = 0.873 秒）
2. chassis_server_v123.py `do_start()` 函数签名有 `duration=None` 参数
3. **但函数体内完全没用 duration**！`action_stop_timer` 从未赋值
4. `do_POST /chassis/start` **也没把 duration 传给 do_start**（默认 None → 静默丢参）
5. 结果：vel_loop 50ms 持续发 vel，无 timer，130 秒不停

**修复（4 步）**：

```python
# Step 1: do_start() 函数体内必须实际处理 duration
def do_start(vx, omega, session_id, duration=None):
    global action_stop_timer, voice_deadline
    action_stop_timer = 0.0
    voice_deadline = 0.0
    if duration:
        action_stop_timer = time.time() + duration
        voice_deadline = action_stop_timer

# Step 2: do_stop() 清掉 timer（防 race condition）
def do_stop():
    global action_stop_timer, voice_deadline
    action_stop_timer = 0.0
    voice_deadline = 0.0

# Step 3: do_POST /chassis/start 必须显式传 duration
def do_POST_chassis_start(handler):
    body = json.loads(handler.body)
    duration = body.get('duration')  # ← 必须提取！
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/json')
    handler.end_headers()
    # 显式传参给 do_start！
    result = do_start(vx, omega, session_id, duration=duration)
    handler.wfile.write(json.dumps(result).encode())

# Step 4: watchdog_loop 顶部必须声明所有全局变量！
def watchdog_loop():
    global last_heartbeat_time, action_stop_timer, voice_deadline, last_tcp_success_time
    #                                        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    #   ← Python 坑：未声明 global → 函数体内一旦读写该全局变量 → 整个 watchdog 线程 crash 退出
    while True:
        time.sleep(WATCHDOG_CHECK_INTERVAL)
        with state_lock:
            if not active: continue
            now = time.time()
            # 优先级 1: action_stop_timer（voice 路径，最优先）
            if action_stop_timer > 0 and now >= action_stop_timer:
                action_stop_timer = 0.0
                _internal_stop_locked('action_stop_timer_expired')
                continue
            # 优先级 2: voice_deadline（UDP 超时）
            if voice_deadline > 0 and now >= voice_deadline:
                voice_deadline = 0.0
                _internal_stop_locked('voice_deadline_expired')
                continue
            # 优先级 3: TCP 5秒断联（最高优先于 heartbeat）
            if last_tcp_success_time > 0:
                tcp_age = now - last_tcp_success_time
                if tcp_age > TCP_DISCONNECT_TIMEOUT:
                    last_tcp_success_time = 0.0
                    _internal_stop_locked('tcp_disconnect_timeout')
                    continue
            # 优先级 4: heartbeat 超时（APP 路径）
            if last_heartbeat_time > 0:
                hb_age = now - last_heartbeat_time
                if hb_age > HEARTBEAT_TIMEOUT:
                    _internal_stop_locked('heartbeat_timeout')
```

**测试验证**：
```
t=0.63s: active=True, omega=-0.1（转中）
t=1.18s: active=False, omega=0.0（停了）
log: action stop timer EXPIRED → AUTO STOP
AUTO STOP reason=action_stop_timer_expired ✅
```

**永久教训**：
- 函数签名有参数 ≠ 函数体内用了参数（静默 bug，最危险）
- `do_POST` 必须显式 `body.get('duration')` 并传给 handler
- Python 全局变量在函数体内使用前必须先 `global` 声明

### 2. 四层 watchdog 架构（2026-09-24 最终版）

| 层级 | 触发条件 | 用途 | 来源 |
|------|---------|------|------|
| **Layer 1**: action_stop_timer | `now >= action_stop_timer` | voice 路径，带 duration 的指令 | 2026-09-24 新增 |
| **Layer 2**: voice_deadline | `now >= voice_deadline` | UDP 风格超时兜底 | 2026-09-23 UDP 架构 |
| **Layer 3**: tcp_disconnect_timeout | `tcp_age > 5.0s` | TCP 断联 5 秒强制停（最高优先于 heartbeat）| 2026-09-24 新增 |
| **Layer 4**: heartbeat_timeout | `hb_age > 1.0s` | APP 路径，TCP 长连接保活 | 历史设计 |

**优先级**：Layer 1 > Layer 2 > Layer 3 > Layer 4

### 3. logger.error 双调用 bug

```python
# 错误
logger.error("[TTS] failure: cannot generate")(f"[TTS] failure: cannot generate")
# 正确
logger.error("[TTS] failure: cannot generate")
```

### 4. FILLER 过滤 (listener.py)

```python
FILLER_WORDS = set('嗯啊哎哦呃')
def is_filler_only(text):
    cleaned = re.sub(r'[，。、！?？\. ,。？！、]+', '', text).strip()
    if not cleaned: return True
    for ch in cleaned:
        if ch not in FILLER_WORDS:
            return False
    return True
```

### 5. v3_log_tail.py 过滤

```python
NOISE_RE = re.compile(
    r'arecord 返?回? 0 bytes'    # arecord 错误
    r'|FutureWarning'                    # urllib3 warning
    r'|\[IGNORE\]'                       # 没识别到 wake
    r'|no wake keyword'
    r'|speech START'
    r'|speech END.*frames'
    r'|\[FILLER\]'                       # 填充词 dropped
    r'|^\s*warnings\.warn'
)
```
```python
# 错误
logger.error("[TTS] failure: cannot generate")(f"[TTS] failure: cannot generate")
# 正确
logger.error("[TTS] failure: cannot generate")
```

### 2. chassis_server_v123.py watchdog
```python
def do_start(vx, omega, session_id, duration=None):
    global voice_deadline
    if duration:
        voice_deadline = time.time() + duration
    else:
        voice_deadline = 0.0

def watchdog_loop():
    while True:
        time.sleep(WATCHDOG_CHECK_INTERVAL)
        with state_lock:
            if not active: continue
            now = time.time()
            if voice_deadline > 0 and now >= voice_deadline:
                voice_deadline = 0.0
                _internal_stop_locked('voice_duration_complete')
                continue
            if last_heartbeat_time > 0:
                hb_age = now - last_heartbeat_time
                if hb_age > HEARTBEAT_TIMEOUT:
                    _internal_stop_locked('heartbeat_timeout')
```

### 3. FILLER 过滤 (listener.py)
```python
FILLER_WORDS = set('嗯啊哎哦呃')
def is_filler_only(text):
    cleaned = re.sub(r'[，。、！?？\. ,。？！、]+', '', text).strip()
    if not cleaned: return True
    for ch in cleaned:
        if ch not in FILLER_WORDS:
            return False
    return True

# process_query:
if is_filler_only(text):
    logger.info(f"[FILLER] '{text}' dropped")
    continue
```

### 4. v3_log_tail.py 过滤
```python
NOISE_RE = re.compile(
    r'arecord \u8fd4\u56de 0 bytes'    # arecord 错误
    r'|FutureWarning'                    # urllib3 warning
    r'|\[IGNORE\]'                       # 没识别到 wake
    r'|no wake keyword'
    r'|speech START'
    r'|speech END.*frames'
    r'|\[FILLER\]'                       # 填充词 dropped
    r'^\s*warnings\.warn'
)
```

## 调试步骤

### voice 路径问题排查
1. `grep -E "ASR|WAKE|ACTION" /home/user/ll_v3.log` (listener 是否识别)
2. `grep "action_id\|duration_s\|session_id" /tmp/action_router.log` (action_router 是否路由)
3. `grep "START\|STOP\|voice_duration\|heartbeat" /tmp/chassis_server.log` (chassis_server 是否收到)

### 常见问题

#### 1. 底盘只转 1-2° 就停
- **症状**: listener matched, action matched, 但底盘转 1° 后停
- **根因**: watchdog AUTO STOP (heartbeat 超时或 session 变化)
- **修复**: 用 UDP 架构 + voice_deadline + 去掉 session_changed AUTO STOP

#### 2. TCP 连接超时
- **症状**: `set_vel failed: timed out`
- **根因**: 底盘 (192.168.1.204:19205) TCP 连不上
- **可能**: 灵龙下电, 网络中断, 防火墙

#### 3. v3_gui / v3_log_tail 突然消失
- **症状**: 窗口没了, 消息没刷新
- **根因**: python 进程被 kill (kill -9 误杀)
- **修复**: 重启两个进程

## 文件位置
- chassis_server: `/data/linglong-project/scripts/chassis_server_v123.py`
- action_router: `/data/linglong-project/scripts/action_router.py`
- listener: `/home/user/linglong_voice_v3.py`
- v3_log_tail: `C:\Users\DFET\.openclaw\workspace\v3_log_tail.py`
- v3_gui: `C:\Users\DFET\.openclaw\workspace\v3_gui.py`

## 监控命令
```bash
# DGX SSH
ssh kk@10.86.51.51 "tail -f /tmp/chassis_server.log"
ssh kk@10.86.51.51 "tail -f /tmp/action_router.log"
ssh kk@10.86.51.51 "ps aux | grep -E 'chassis|action' | grep -v grep"

# 灵龙 SSH
ssh $LINGLONG_USER@$LINGLONG_HOST "tail -f /home/user/ll_v3.log"
```

## 待 Leo 拍板 TODO
1. chassis_server_v123.py 加 TCP 重连机制 (避免 vel_loop set_vel failed)
2. action_router 是否需要本地缓存 TTS "收到" 音频
3. listener.py 性能优化
