# 灵龙底盘安全 SKILL

> 适用: 所有灵龙底盘相关的诊断 + 排查 + 操作
> 目的: 防止 Leo 立的"差点撞桌子"事故重演
> 创建: 2026-09-24

---

## 1. Leo 立的 4 条铁律（写进 SOUL.md）

### 1.1 Leo 11:34 — "底盘异常先停再排查"

**铁律**：任何灵龙底盘异常（自转不停 / 不响应 / 状态异常）→ 第一件事 `POST /chassis/stop`，验证 active=False 后才开始排查。

**实施步骤**（每次底盘异常必须执行）：
1. `POST http://10.86.51.122:7781/chassis/stop` （`scripts/chassis_stop.py` 或 curl/python requests）
2. `GET /chassis/health` 验证 `active=false, last_vel={vx:0, omega:0}`
3. **然后**才开始排查 log / 改代码 / SSH 查 DGX
4. 排查过程中每发现新问题，**先确认 stop 没失效**再继续

```python
import requests, json
r = requests.post('http://10.86.51.122:7781/chassis/stop',
                  json={'session_id': 'walt_safety_stop'},
                  timeout=30)
h = requests.get('http://10.86.51.122:7781/health', timeout=5).json()
# active=False, last_vel={vx:0, omega:0} = 安全
```

---

### 1.2 Leo 12:29 — "任何执行前先问"（差点撞桌子 #1）

**铁律**：任何可能让灵龙实际运动的执行命令（v3 语音链路 / 直接 HTTP start / chassis_pulse 任何形式）前，必须先问 Leo 确认以下 4 件事：

1. **动作类型**：旋转 vs 直线
2. **空间是否够**：直线前进前必须问"工位空间够吗？走多远？"
3. **duration 时长**
4. **是否真的需要执行**：能不能用 vel_loop 测试代码逻辑而不动真车？

**绝对禁止**：
- ❌ 不问 Leo 直接发 start
- ❌ 用 "verifying xxx"借口直接执行动作
- ❌ 在 Leo 还没看到事故描述之前重复同样的执行测试

**例外**：Leo 明确说"可以发 start" / Leo 在现场看着，才执行。

---

### 1.3 Leo 15:56 — "运动分级许可"（直线/旋转分级）

| 风险等级 | 动作类型 | 许可要求 |
|---------|---------|---------|
| 🔴 高风险 | **直线运动**（前进/后退） | **必须 Leo 明确同意才能发** |
| 🟡 中风险 | **原地旋转**（左转/右转） | 最好告诉 Leo 一声 |
| 🟢 低风险 | 上身动作 / 底盘 STOP | 通常不需要 |

**绝对禁令**：
- ❌ 任何 `vx != 0` 的底盘运动
- ❌ 任何带 `vx` 或 `omega` 的 chassis_server.start
- ❌ 任何 chassis_pulse 包含前进/后退路径的调用（chassis_fwd / chassis_back）
- ❌ "为了测试链路完整性"借口自动执行

**例外**：Leo 明确说 + Leo 在现场看。

**部署完成后回复模板**（强制使用）：
```
Leo，[任务名]部署完成：
- [修复内容]
- [建议的测试场景]
- 推荐先测：旋转 N 度 / 前进 0 米（看到默认参数）
- 避免：前进/后退（工位空间小）
需要 Leo 同意我才发底盘命令。
```

---

### 1.4 Leo 15:59 — "灵龙上身运动也要同意"

**铁律**：灵龙**上身运动**（arm 关节、灵巧手 grip、SDK 调用）也要征得 Leo 同意才能做。

**含义**：
- ❌ 不问 Leo 直接调 voice_cmd_server :7778
- ❌ 不问 Leo 直接发 robot_enable_up / robot_enable_down / set_end_pose / gripper_cap
- 跟底盘运动一样的许可流程

---

## 2. Leo 16:03 飞书 channel 双向断（已诊断）

**症状**：WALT → Leo 飞书 ✅ 通；Leo → WALT ❌ 通

**诊断证据**（Leo 16:10 log）：
- `sessionKey=agent:main:feishu:direct:ou_0c664a6efe1cc371b66359cd2131115b` 反复出现
- `isError=true error=terminated rawError=terminated`（15:57:54）
- MEMORY.md 69999 chars > 20000 限制 warning

**原因**：agent run 立即被 terminate，回复不出来。

**Leo 16:17 自启动 gateway 重启**（待 Leo 验证双向通）。

---

## 3. 9/24 4 次底盘事故教训

| 时间 | 事故 | 根因 | 修复 |
|------|------|------|------|
| 11:11 | 灵龙上电后自己转（130 秒不停） | do_start 收 duration 但不用 | 加 action_stop_timer + watchdog 跳过 voice 检查 |
| 12:03 | reasoning 验证链路发 vx=0.1，差点撞桌子（Leo 急停） | 我以为"只读"实际是"动作" | Leo 立铁律"先问" |
| 12:29 | Leo: "差点撞桌子！" → 立刻停 | 我违反"先停再排查"原则 | 立铁律 |
| 12:54 | 中文数字 parser 部署后 Leo 实测触发 9 次前进 | 我没预判 Leo 会在工位测底盘 | 立铁律"运动分级许可" |

---

## 4. 诊断流程（每次都要先停）

按 Leo 11:34 铁律：

```
1. POST /chassis/stop → 验证 active=false
2. GET /chassis/health → 看 last_vel + tcp_age + heartbeat_age
3. 看 chassis_server.log: /tmp/chassis_server.log
   tail -50 + grep -E 'START|EXPIRED|STOP|AUTO STOP'
4. 看 v3 listener log: ssh 到灵龙 /home/user/voice_v3.log
   tail -100 + grep -E 'ASR|WAKE|ACTION'
5. 看 action_router log: /tmp/action_router.log
6. 看 orchestrator log: /tmp/orch.log
7. 看完 log → 才能改代码
```

---

## 5. action_stop_timer 设计（核心修复）

按 Leo 11:29 拍板：

```
chassis_server watchdog_loop 检查顺序：
1. TCP 断联 5 秒 → AUTO STOP（防灵龙关机期间 vel 堆积）
2. action_stop_timer → AUTO STOP（action_router 路径）
3. voice_deadline → AUTO STOP（voice 路径）
4. heartbeat 1 秒 → AUTO STOP（仅 APP 摇杆路径，voice 路径跳过）
```

`if action_stop_timer == 0 and voice_deadline == 0: 检查 heartbeat` — **voice 路径不能被 heartbeat 抢先停**。

---

## 6. Leo 12:51 拍板的中文数字修复

action_router.py 加 `chinese_to_arabic(query)` 函数，在 `fuzzy_match()` 前调用。支持到百位+小数点：

```python
# 测试通过
chinese_to_arabic('九百九十九点九') == '999.9'  # True
chinese_to_arabic('左转五十度') == '左转50度'   # True
chinese_to_arabic('前进零点三米') == '前进0.3米'  # True
```

---

## 7. 备份铁律（Leo 10:20 立）

任何灵龙相关代码改动前，**三地备份**：
- **DGX**：cp 到 `/data/linglong-project/scripts/*.bak.YYYYMMDD_HHMMSS_before_XXX`
- **灵龙主板**：cp 到 `/home/user/*.bak.YYYYMMDD_HHMMSS_before_XXX`（如果有改动）
- **Windows 本地**：cp 到 `C:\Users\DFET\.openclaw\workspace\backups\*.bak.YYYYMMDD_HHMMSS_before_XXX`

---

_最后更新：2026-09-24 16:46_