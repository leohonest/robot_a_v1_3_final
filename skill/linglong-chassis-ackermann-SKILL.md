# 灵龙底盘（仙工 AGV）阿克曼运动左右特性 - Skill (v1.0)

**版本**: V1.0 (2026-09-09 创建)
**Owner**: Leo (老板)
**技术负责**: Walt (我)
**底盘**: 灵龙 LingLong-H 阿克曼底盘 (Seer 仙工 AGV 平台)
**IP**: 192.168.1.204 | TCP 19205 (cmd) / 19204 (state)
**SDK 调用**: `sdk.send_chassis_command(translation, rotation)`

---

## ⭐ 1. 阿克曼底盘左右转向特性（铁律）

**核心结论**（Leo 2026-09-09 10:53 物理分析 + 实测验证）：

### 1.1 前进 vs 后退的左右方向是**反的**

| 底盘运动状态 | APP 发 omega（车轮转向） | 底盘实际转弯方向 |
|------------|-------------------------|-----------------|
| **前进**（vx > 0） | omega < 0（车轮左转） | **底盘左转** ✓ |
| **前进**（vx > 0） | omega > 0（车轮右转） | **底盘右转** ✓ |
| **后退**（vx < 0） | omega < 0（车轮左转） | **底盘右转** ✗（反了）|
| **后退**（vx < 0） | omega > 0（车轮右转） | **底盘左转** ✗（反了）|

### 1.2 物理原因

阿克曼底盘（像汽车）只有**前轮转向，后轮驱动**。
- 前进时：车轮左转 → 车身左转（正常驾驶直觉）
- **后退时：方向盘左转 ≠ 车身左转**（与前进相反，像倒车方向盘逻辑）

### 1.3 SDK 调用修正（v1.23 起）

```python
# v1.23 阿克曼底盘反向修正（Leo 2026-09-09 10:53 物理分析）：
# 前进时 omega 不变；后退时 omega 必须反转
rotation_sent = -omega if vx < 0 else omega
sdk.send_chassis_command(translation=vx, rotation=rotation_sent)
```

**永远不要用** `vx > 0` 时测出的方向**直接套到** `vx < 0`！
**永远不要拍脑袋**："vx 正负都一样" —— ❌ 错的！

### 1.4 摇杆 UI 坐标系（v1.23 起已修正）

参考 RV-1 harin-supper-rocker 公式（Leo 验证过）：
```javascript
const vx   = -dy * scale;   // Y 反向：屏幕上半 → vx > 0（前进）
const omega = -dx * scale;  // X 反向：屏幕左半 → omega > 0（左转）
```

注意：**omega 是 APP 发给底盘的"车轮转向"参数**（不是底盘实际转弯方向）。
- 前进 + omega > 0 → 车轮右转 → **底盘右转** ✓
- 后退 + omega > 0 → 车轮右转 → 但**底盘左转**（SDK 已自动反转）

---

## 2. APP 操控持续运动（v1.24 变更，2026-09-09）

### 2.1 历史背景（**绝对不要再回退**）

- **v1.22**（2026-09-08）：有 `MAX_START_DURATION = 2.0` 秒限制
  - Leo 怕"APP 不松手或 bug" → 强制 2 秒后 AUTO STOP
  - 问题：**用户想一直按住摇杆走几秒钟就被打断！**
- **v1.24**（2026-09-09 11:17 移除 2 秒限制）：
  - `MAX_START_DURATION = 86400.0`（24 小时兜底，实际不会触发）
  - **APP 按多久就走多久**（松开发 stop 才停）
  - **唯一保险：心跳 watchdog**（漏 2 次心跳 / 1 秒未收到 → AUTO STOP）

### 2.2 双层 watchdog 设计（v1.22 起保持）

| Watchdog | 阈值 | 作用 | 触发后行为 |
|----------|------|------|-----------|
| **心跳 watchdog** | 漏 2 次 / 1 秒 | 真正保险（APP crash / 网络断） | AUTO STOP |
| **单次时长 watchdog** | 24 小时（兜底）| 几乎不会触发，仅防极长 bug | AUTO STOP |

心跳间隔 500ms，超时 1.0 秒 → AUTO STOP（这个是**唯一会触发的**保险）。

### 2.3 session_id 防误操作

- APP 启动时生成随机 UUID 作为 `CHASSIS_SESSION_ID`
- 每次心跳 / start / stop 都带 session_id
- 服务端检测到 session 变化（APP 重启 / 换设备）→ AUTO STOP 旧的

---

## 3. SDK 调用最佳实践

### 3.1 send_chassis_command（v1.22 起使用，v1.21 已验证能动 0.12 m/s）

```python
from linglong_h_sdk import LinglongHSdkClass

sdk = LinglongHSdkClass('192.168.1.204', chassis_tcp_on_send=True)
sdk.configure_chassis_tcp('192.168.1.204', 19205, timeout_s=0.1)
sdk.configure_chassis_state_tcp('192.168.1.204', 19204, timeout_s=5.0)

# ✅ 正确：send_chassis_command 直接发 TCP（绕过 UDP）
n_sent = sdk.send_chassis_command(translation=vx, rotation=rotation_sent)

# ❌ 不要用 set_base_vel（v1.21 测过不动，AGV 速度一直 0.000）
```

### 3.2 阿克曼 reverse rotation（v1.23 起）

```python
# APP 发的 omega 是"车轮转向"，但后退时车身方向反转
rotation_sent = -omega if vx < 0 else omega
sdk.send_chassis_command(translation=vx, rotation=rotation_sent)
```

### 3.3 调试日志（v1.21 加的，超有用）

```python
import time as _t
_t.sleep(0.05)
sp = sdk.query_chassis_speed_state()
logger.info(f"vel: vx={vx:.3f} omega={omega:.3f} sent={n_sent}B → AGV actual: {sp}")
```

发送后 50ms query 看实际速度，能立刻发现 SDK 是否生效 / 方向是否对。

---

## 4. 当前部署状态（v1.24）

| 组件 | 路径 / 值 |
|------|----------|
| 服务端 | DGX: `/data/linglong-project/scripts/chassis_server_v123.py`（v1.24）|
| 端口 | 7781 (HTTP API) |
| 进程 | PID 1042774（v1.22 的，待重启到 v1.24） |
| 日志 | `/tmp/chassis_server.log` |
| 重启命令 | 见 `skills/linglong-chassis-ackermann-SKILL.md` 第 5 节 |

### 4.1 v1.23 → v1.24 重启步骤

```bash
# DGX 上
PORT_PID=$(ss -tlnp 2>&1 | grep ':7781 ' | grep -oP 'pid=\\K[0-9]+' | head -1)
if [ -n "$PORT_PID" ]; then kill -9 $PORT_PID; fi
pkill -9 -f chassis_server 2>/dev/null
sleep 2
/data/linglong-project/envs/vla/bin/python /data/linglong-project/scripts/chassis_server_v123.py > /tmp/chassis_server.log 2>&1 &

# 验证
curl http://localhost:7781/health | python -m json.tool
# 应该看到：max_start_duration_s: 86400.0
```

---

## 5. ⛔ 错误预防（永久铁律）

### 5.1 阿克曼底盘**绝对不能**写错的代码

```python
# ❌ 错误写法 1：vx 正负都用同一个 omega
sdk.send_chassis_command(translation=vx, rotation=omega)

# ❌ 错误写法 2：以为 sign 不会影响方向
if abs(omega) > 0:
    sdk.set_base_vel(vx, omega)  # ❌ set_base_vel 不工作（v1.21 已验证）

# ❌ 错误写法 3：拍脑袋拍方向
# "我猜 vx>0 时 omega>0 是右转" → 实测发现是错的！
```

```python
# ✅ 唯一正确写法（v1.24）：
rotation_sent = -omega if vx < 0 else omega  # 阿克曼 reverse rotation
sdk.send_chassis_command(translation=vx, rotation=rotation_sent)
```

### 5.2 **绝对不要回退**的变更

- ❌ **不要加回 2 秒 MAX_START_DURATION**（Leo 已明确移除）
- ❌ **不要改回 set_base_vel**（v1.21 验证不动，改完必坏）
- ❌ **不要去掉 send_chassis_command 的 50ms query 调试日志**（超有用）
- ❌ **不要改 omega 反转逻辑**（除非底盘硬件改型）

### 5.3 改动 chassis_server 前的清单

1. ⛔ 先查这个 SKILL 文件，确认改动不违反铁律
2. ⛔ 不要碰 `MAX_START_DURATION`、`rotation_sent`、`send_chassis_command` 三个核心点
3. ⛔ 改完必须**立即**用 curl /health 验证 + Python 测一次（不要等 Leo 测）
4. ⛔ 部署脚本里加 `assert 'max_start_duration_s.*86400' in body` 之类的强校验

---

## 6. 关联文件 / SKILL

| 文件 | 内容 |
|------|------|
| `scripts/chassis_server_v123.py` | 当前底盘服务端（v1.24 待部署）|
| `skills/linglong-vla-SKILL.md` | 灵龙 VLA 训练项目（底盘在第 1 节硬件表）|
| `skills/linglong-disable-SKILL.md` | 灵龙 disable 上下使能 |
| `MEMORY.md` § ⛔ RV-1 摇杆坐标公式 | 摇杆坐标公式铁律 |
| `MEMORY.md` § v1.22 chassis_server | 部署位置（PID 1042774） |

---

## 7. 关键里程碑

| 时间 | 版本 | 事件 | 教训 |
|------|------|------|------|
| 2026-09-04 | v1.21 | 测过 `set_base_vel` 不动 | 永远用 send_chassis_command |
| 2026-09-08 | v1.22 | 加心跳 + MAX_START_DURATION=2.0 + B方案 | 2秒限制太严 |
| 2026-09-08 | v1.23 | 阿克曼底盘 reverse rotation（Leo 物理分析）| 后退时要反转 omega |
| **2026-09-09** | **v1.24** | **移除 2秒限制** | **APP 可持续运动 24h** |

---

*如需修改阿克曼底盘左右特性 / 心跳 / 单次时长 watchdog，先读 SKILL 第 5 节铁律！*
*Skill 作者: WALT | 维护者: WALT*