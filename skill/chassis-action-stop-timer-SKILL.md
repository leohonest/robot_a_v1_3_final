# Chassis Action-Stop-Timer SKILL

> **目的**：记录 Leo 2026-09-24 拍板的底盘 action_stop_timer 修复方案
> 适用：DGX `/data/linglong-project/scripts/chassis_server_v123.py`
> 路径: DGX chassis_server.py（HTTP 7781）+ DGX action_router.py（HTTP 7793）
> 创建: 2026-09-24 (Leo 12:13 + 12:29 + 12:51 拍板）

---

## 1. 核心原则（Leo 9/22 17:30 UDP 架构）

底盘物理逻辑 = **手机 APP 虚拟摇杆 / 数采员手柄模式**：
- 持续发送心跳 = 持续动作
- 松手 = 1 秒无心跳 → 停
- voice 路径（action_router）**不发心跳**——单次 start 带 duration，chassis_server 自己 timer 到点停

**两套机制并存**：
- APP 路径：无 voice_deadline / action_stop_timer → heartbeat 1 秒 watchdog 生效
- voice 路径：有 voice_deadline / action_stop_timer → heartbeat 检查**跳过**

---

## 2. Leo 12:29 立的 4 条铁律（写进 SOUL.md）

按底盘运动风险等级分级：

| 风险等级 | 动作类型 | 许可要求 |
|---------|---------|---------|
| 🔴 高风险 | **直线运动**（前进/后退） | **必须 Leo 明确同意才能发** |
| 🟡 中风险 | **原地旋转**（左转/右转） | 最好告诉 Leo 一声 |
| 🟢 低风险 | 上身动作 / 底盘 STOP | 通常不需要 |

**绝对禁止**：
- ❌ 任何 `vx != 0` 的底盘运动
- ❌ 任何带 `vx` 或 `omega` 的 chassis_server.start
- ❌ 任何 chassis_pulse 包含前进/后退路径的调用（chassis_fwd / chassis_back）
- ❌ "为了测试链路完整性"借口自动执行

**例外**（Leo 明确说才生效）：
- "可以发 start" → 可以执行
- Leo 在现场看着 → 可以执行

---

## 3. Leo 11:34 立的铁律（"灵龙底盘异常先停再排查"）

任何灵龙底盘异常（自转不停 / 不响应 / 状态异常）→ **第一件事 `POST /chassis/stop`**，验证 `active=False` 后才开始排查。

工具：
```python
import requests, json
r = requests.post('http://10.86.51.122:7781/chassis/stop',
                  json={'session_id': 'walt_safety_stop'},
                  timeout=30)
h = requests.get('http://10.86.51.122:7781/health', timeout=5).json()
# active=False, last_vel={vx:0, omega:0} = 安全
```

---

## 4. chassis_server watchdog 四层优先级（修复后）

```python
# watchdog_loop 检查顺序（修复后 2026-09-24 11:29 + 12:13）

# 优先级 1（最高）：TCP 断联 5 秒 → AUTO STOP
if last_tcp_success_time > 0:
    tcp_age = now - last_tcp_success_time
    if tcp_age > TCP_DISCONNECT_TIMEOUT:  # 5.0 秒
        _internal_stop_locked('tcp_disconnect_5s')
        continue

# 优先级 2：action_router 路径停止定时器（Leo 11:29 拍板）
if action_stop_timer > 0 and now >= action_stop_timer:
    _internal_stop_locked('action_stop_timer_expired')
    continue

# 优先级 3：voice_deadline（Leo 9/22 17:30）
if voice_deadline > 0 and now >= voice_deadline:
    _internal_stop_locked('voice_duration_complete')
    continue

# 优先级 4（最低）：heartbeat 1 秒超时（**仅 APP 摇杆路径**）
# Leo 12:13 关键修复：voice 路径有 timer → 跳过 heartbeat 检查
if action_stop_timer == 0 and voice_deadline == 0:
    if last_heartbeat_time > 0:
        hb_age = now - last_heartbeat_time
        if hb_age > HEARTBEAT_TIMEOUT:  # 1.0 秒
            _internal_stop_locked('heartbeat_timeout')
            continue
```

**关键**：`if action_stop_timer == 0 and voice_deadline == 0:` 跳过 heartbeat 检查——voice 路径有 timer，**不能被 heartbeat 1 秒抢先停**。

---

## 5. chassis_server 修复要点（3 个 bug 修复链）

### 5.1 bug 1：do_start() 收参数但没用
**症状**：action_router 发 `duration=0.873`，chassis_server 收到但函数体内完全没用。

**修复**：
```python
def do_start(vx, omega, session_id, duration=None):
    # ...
    global action_stop_timer
    if duration is not None and duration > 0:
        action_stop_timer = time.time() + duration
        logger.info(f'ACTION STOP TIMER set: deadline={action_stop_timer:.3f} (now+{duration:.3f}s)')
    else:
        action_stop_timer = 0.0  # APP 路径不设定时器
    # ...
```

### 5.2 bug 2：do_POST /chassis/start 没把 duration 传给 do_start
**修复**：
```python
elif self.path == '/chassis/start':
    # ...
    vx = float(req.get('vx', 0.0))
    omega = float(req.get('omega', 0.0))
    # (decision recorded in git log)
    duration = req.get('duration', None)
    if duration is not None:
        duration = float(duration)
    result = do_start(vx, omega, sid, duration=duration)  # 传 duration
    self._send_json(result)
```

### 5.3 bug 3：watchdog_loop 函数体没声明 global → 整个线程 crash
**症状**：`UnboundLocalError: cannot access local variable 'action_stop_timer' where it is not associated with a value` → watchdog 线程立即死掉 → timer 永远不触发。

**修复**：
```python
def watchdog_loop():
    # (decision recorded in git log)
    global last_heartbeat_time, action_stop_timer, voice_deadline, last_tcp_success_time
    # ...
```

---

## 6. do_stop() 归零逻辑（防 race condition）

```python
def do_stop(session_id=None):
    global voice_deadline, action_stop_timer
    voice_deadline = 0.0
    # (decision recorded in git log)
    # 防止 timer 还没到, stop 已来, 之后 timer 误触发
    action_stop_timer = 0.0
    # ...
```

---

## 7. duration 计算公式（DGX action_router.py）

```python
# (decision recorded in git log)
LINEAR_VEL = 0.1   # m/s（前进/后退）
ANGULAR_VEL = 0.1  # rad/s（左/右转）≈ 5.73°/s

# 角度时长（duration_s）
duration_s = angle_deg / (ANGULAR_VEL * 180 / 3.14159)  # = angle / 5.73
# 例：5° → 0.873s, 30° → 5.236s, 45° → 7.854s, 90° → 15.708s, 500° → 87.27s

# 距离时长
duration_s = distance_m / LINEAR_VEL
# 例：0.3m → 3s, 1m → 10s
```

---

## 8. 中文数字解析（Leo 12:51 拍板）

**背景**：Qwen3-ASR 0.6B 默认输出中文数字（"五"），action_router regex `\d+` 不匹配。

**实现**：在 `fuzzy_match()` 前调 `chinese_to_arabic(query)`：

```python
_CN_SINGLE = {'零': '0', '一': '1', '二': '2', '三': '3', '四': '4',
              '五': '5', '六': '6', '七': '7', '八': '8', '九': '9', '两': '2'}

def chinese_to_arabic(query: str) -> str:
    # 支持到百位+小数点：九百九十九点九 → 999.9
    # 1) 百位复合段：[零-九]百[...]
    # 2) 十位段：(X)?十Y[.点Z]
    # 3) 小数段：X点Y
    # 4) 单词字：X
    # ...
```

**测试通过**（test_cn_parser2.py）：九→9, 三十→30, 九十→90, 一百→100, 九百→900, 一百二十三→123, 一百零五→105, 九百九十九→999, 零点三→0.3, 九百九十九点九→999.9 ✅ ALL PASS

---

## 9. 部署流程（每次改 chassis_server_v123.py）

按 Leo 立的"先备份再改"原则（**绝对必读**）：

1. **DGX 备份**：
   ```bash
   cp /data/linglong-project/scripts/chassis_server_v123.py \
      /data/linglong-project/scripts/chassis_server_v123.py.bak.YYYYMMDD_HHMMSS_before_XXX
   ```

2. **Windows 备份**：本地文件同步备份到 `backups/`

3. **本地修改 → 推 DGX**（用 `scripts/dgx_ssh.py:dgx_run()` 走 plink）：
   - base64 编码 → 写 `/tmp/` → md5sum 验证 → mv 到生产路径

4. **kill 旧进程 → 启新进程**（必须 Leo 同意才动）：
   ```bash
   pkill -9 -f 'chassis_server_v123.py.*--port=7781'
   cd /data/linglong-project && setsid nohup /data/linglong-project/envs/vla/bin/python \
     /data/linglong-project/scripts/chassis_server_v123.py --port=7781 \
     > /tmp/chassis_server.log 2>&1 < /dev/null &
   ```

5. **验证**：`/health` + 测试 start 触发 timer → 看 log

**警告**：每次部署完**不要自动执行测试**！按 Leo 12:29 铁律，**只告诉 Leo 修了什么 + 建议测试场景（旋转+几度，不前进/后退）**。

---

## 10. 验证命令

```python
import requests, json, time
# Test 1: voice 路径（带 duration）
duration = 0.873  # 5度 = 0.873s
r = requests.post('http://10.86.51.122:7781/chassis/start', json={
    'vx': 0.0, 'omega': 0.1, 'session_id': f'test_{int(time.time())}', 'duration': duration
}, timeout=5)
print(r.json())

# Test 2: APP 路径（无 duration → 1秒 heartbeat 超时停）
r = requests.post('http://10.86.51.122:7781/chassis/start', json={
    'vx': 0.0, 'omega': 0.1, 'session_id': f'app_test_{int(time.time())}'
    # NOTE: 不传 duration
}, timeout=5)

# Test 3: 中文数字 → action_router HTTP
r = requests.post('http://10.86.51.122:7793/action', json={'query': '左转九百九十九点九度'}, timeout=5)
print(r.json()['params'])  # {'angle_deg': 999.9, 'duration_s': 174.5}
```

---

## 11. 历史教训（写进 SKILL 的根本原因）

| 时间 | 事故 | 修复 |
|------|------|------|
| 9/24 11:11 | 灵龙上电后自己转（130 秒不停）|action_router 发 duration 但 do_start 没用 |
| 9/24 12:03 | reasoning `verification` 发 vx=0.1 验证链路，差点撞桌子（Leo 急停）| Leo 12:29 立铁律 |
| 9/24 12:13 | heartbeat 1 秒抢先停（duration=7.85s 应该停，但被 heartbeat 抢先 1 秒）| watchdog_loop 跳过 voice 路径 heartbeat 检查 |
| 9/24 12:29 | Leo: "差点撞桌子！" → 立刻停 | Leo 立铁律："任何执行前先问" |
| 9/24 12:36 | Leo 现场实测发现所有角度都停 8 秒 | 中文数字 → 阿拉伯数字（chinese_to_arabic） |
| 9/24 12:54 | 部署完中文数字 parser 后，Leo 在工位上实测触发了 9 次前进 | Leo 15:56 立铁律："直线运动必须 Leo 同意" |

---

_最后更新：2026-09-24 16:46_