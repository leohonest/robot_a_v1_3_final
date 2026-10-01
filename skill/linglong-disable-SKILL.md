# 灵龙 disable 操作 SKILL (v1.0)

**版本**: V1.0 (2026-09-05 完成 disable bug 调试总结)
**作者**: Walt
**目的**: 记录灵龙 disable 操作的正确做法 + 今天发现的严重 bug，防止下次再踩坑

**核心教训（先看）**：
- ⛔ **任何 disable 实现都必须用 SDK 标准接口 `robot_enable_down()`**，不能直接调底层 `_send_robot_mode_for()`
- ⛔ **`_notify_control_switch` 全局中断是 disable 真生效的关键**——绕过它 = disable 不工作
- ⚠️ **disable 有固有副作用**：10 秒后 watchdog 切回 teleop+断电（这是臂控制器安全设计，不可避免）

---

## 1. 灵龙 disable 的 3 个真相（2026-09-05 验证）

### 1.1 SDK 必须走标准接口

✅ **正确（disable v5，今天 Leo 测试 OK）**：
```python
mode_mgr = RobotModeManager('192.168.1.28', port=4141)
mode_mgr.robot_operation_mode()    # 1秒（含 _notify_control_switch 中断）
time.sleep(2)
mode_mgr.robot_enable_down()       # 5秒（含 _notify_control_switch + 4秒 disable=1 + 1秒 disable=0）
time.sleep(2)
mode_mgr.close()
print('DISABLED v5 (10s)')
```

⛔ **错误（disable v4，我 9月5日 14:11 写错的，Leo APP 触发后不生效）**：
```python
msg = RobotModeMessage(enable=0, disable=1, retract_mode=0, inference_teleop_mode=0)
mode_mgr._send_robot_mode_for(msg, 30.0)   # 绕过 robot_enable_down() = 绕过 _notify_control_switch
mode_mgr.close()
```

**为什么不工作**：
- `_notify_control_switch("robot_enable_down")` 触发全局中断 → 通知臂控制器"控制权切换"
- **没有这个中断，臂控制器认为只是普通的 mode 包变化，不会进入 disabled 状态**
- 30秒持续 disable=1 后突然停止 → watchdog 切回 teleop+断电

### 1.2 disable 的固有副作用（不可消除）

**Leo 15:00 听到"遥操模式 + 断电"的原因**：

| 时间 | 事件 |
|------|------|
| 14:58:30 | disable v5 启动（disable=1 持续 10 秒）|
| 14:58:30-40 | disable 真的释放扭力（Leo 物理验证 + state 显示手臂掉了）|
| 14:58:40 | v5 退出 → disable=1 流停止 |
| ~14:58:50 | watchdog 启动："无 mode 包 = 控制链路断开 = 安全切回" |
| 15:00 | Leo 听到"遥操模式" + 看到断电 |

**根本原因**：disable.py 退出 → UDP 流停止 → 臂控制器 watchdog（安全设计）切回 idle/teleop → 断电。

**这是臂控制器内部行为，不是 v5 的 bug**。

**真正修复需要**（如 Leo 要做持久 disable）：
- 方案 A：disable daemon 长跑（Leo 需要手动管理生命周期）
- 方案 B：找 SDK 单次 disable API（之前查过没找到 `release_torque` / `set_zero_torque`）
- 方案 C：接受 10 秒限制（如果 Leo 测试时 10 秒够用）

### 1.3 disable 有效时间 = UDP 流持续时间

- **Leo 9月2日 dashboard 直接命令** = Python 里直接调 `robot_enable_down()` → 5 秒后 disable.py 退出 → watchdog 切回（但 Leo 当时只测了"放下"，没观察到后续）
- **Leo 9月5日 APP 下使能**（修复前）= server 跑 v4 disable.py 30 秒 → watchdog 切回
- **Leo 9月5日 14:58 测试**（修复后）= 我跑 v5 → 10 秒 disable 真生效 → 之后 watchdog 切回

---

## 2. voice_cmd_server 中文识别 bug（2026-09-05 修复）

### 2.1 Bug 现象

**Leo APP 触发"下使能"按钮 → server 错误识别为 enable → 跑 enable.py 而不是 disable.py**

### 2.2 根因

**INTENT_RULES 顺序 + 子串匹配**：

```python
INTENT_RULES = [
    {'intent': 'enable', 'patterns': ['使能', ...]},   # ← 第一个
    {'intent': 'home',   'patterns': [...]},
    {'intent': 'disable', 'patterns': ['下使能', ...]}, # ← 第三个
]

def recognize_intent(text):
    for rule in INTENT_RULES:
        for pattern in rule['patterns']:
            if pattern in text:    # ← 子串匹配
                return rule
```

**"下使能" 包含 "使能" → 第一个匹配命中 enable 规则 → 永远跑 enable.py！**

### 2.3 修复方案

**把 disable 规则移到第一个**：

```python
INTENT_RULES = [
    {'intent': 'disable', 'patterns': ['下使能', '断电', 'disable'], ...},  # 第一个
    {'intent': 'enable',  'patterns': ['使能', '上电', 'enable'], ...},       # 第二个
    ...
]
```

修复后：
- "下使能" → disable patterns 第一个命中 → disable ✓
- "使能" → 不在 disable patterns → enable patterns 命中 → enable ✓
- "上使能" → 不在 disable patterns → enable patterns 命中 → enable ✓

### 2.4 修复位置

- 文件：`/data/linglong-project/scripts/voice_cmd/voice_cmd_server.py`
- 备份：`voice_cmd_server.py.bak.before_intent_reorder.20260905_151019`
- 修复脚本：用 Python `del lines[i]; lines.insert(j, line)` 调整

---

## 3. 9月2日 vs 9月4日 disable.py 时间线（避免混淆）

| 时间 | 文件 | 来源 | 设计 |
|------|------|------|------|
| 9月2日 12:52 | enable.py / home.py / cap.py / state.py | Walt 部署 | 标准接口 |
| **没有 disable.py** | - | - | - |
| **9月4日 14:19** | **disable.py（370 bytes，最早版本）** | 原版 | `robot_operation_mode + robot_enable_down` |
| 9月5日 13:35 | disable.py.bak.20260905_133500（749 bytes）| 中间版本 | 错误的"持续发包" |
| 9月5日 14:11 | disable.py v4（749 bytes，**我写错的**）| 我改的 | `_send_robot_mode_for(disable=1, 30s)` |
| **9月5日 14:55** | **disable.py v5（1211 bytes，当前生产）**| Leo 设计 + 我部署 | `robot_operation_mode + robot_enable_down` + 10 秒 |

**关键事实**：
- **最早的 disable.py 是 9月4日 14:19**——**不是 9月2日**
- Leo 9月2日 dashboard 下使能一定用的是**直接 SDK 调用**（不是脚本）——脚本当时还不存在
- 原版 disable.py（9月4日）的设计就是对的——v4 是我改坏的

---

## 4. 完整 disable 工作流（生产推荐）

```python
"""disable.py v5 - 标准 SDK 接口 + 10 秒总时长 (2026-09-05 Leo 设计)"""
import sys, time
sys.path.insert(0, '/data/linglong-project/sdk')
from linglong_h_sdk import RobotModeManager

ARM_IP = '192.168.1.28'

mode_mgr = RobotModeManager(ARM_IP, port=4141)

# 阶段 1: 切到 operation 模式 (1秒)
mode_mgr.robot_operation_mode()

# 阶段 2: 等控制器反应 (2秒)
time.sleep(2)

# 阶段 3: 触发下使能 (5秒: 4秒 disable=1 + 1秒 disable=0 + 全局中断)
mode_mgr.robot_enable_down()

# 阶段 4: 让 disable 状态稳定 (2秒)
time.sleep(2)

mode_mgr.close()
print('DISABLED v5 (10s)')
```

**总时长 10 秒，分四个阶段**：
- 0-1秒：operation_mode（含中断）
- 1-3秒：sleep（控制器反应 buffer）
- 3-8秒：enable_down（含中断 + 4s disable=1 + 1s disable=0）
- 8-10秒：sleep（disable 状态稳定 buffer）

---

## 5. 验证 disable 真生效的标志

✅ **state 显示手臂掉了**（z 从 0.65 掉到 0.25）：
```
L_EE = (-0.012, 0.252, 0.252)  ← z 从 0.65 掉到 0.25
R_EE = (-0.022, -0.244, 0.244)  ← z 从 0.65 掉到 0.24
waist_z = 0.750  ← 从 0.80 掉到 0.75
```

✅ **Leo 物理掰动测试**：能轻松掰动手臂（电机无扭力）

✅ **Leo APP 触发"下使能"被 server 识别为 disable**（不是 enable）

---

## 6. 踩坑清单（永久记住）

| # | 错误 | 真相 | 教训 |
|---|------|------|------|
| 1 | 直接调 `_send_robot_mode_for(msg, 30.0)` 做 disable | 必须用 `robot_enable_down()` 标准接口 | **任何 SDK 调用都必须走 SDK 的标准接口** |
| 2 | 绕过 `_notify_control_switch` 中断 | 中断通知臂控制器"控制权切换"，不通知 = 不进入 disabled | **永远不要绕过全局中断** |
| 3 | 持续发包 disable=1 30 秒突然停止 | 触发 watchdog 切回 teleop+断电 | **disable 是有副作用的，需要 Leo 接受 10 秒限制或用 daemon** |
| 4 | server INTENT_RULES 顺序：enable → home → disable | "下使能"被"使能"抢匹配 | **disable 必须排在 enable 之前** |
| 5 | Leo 9月2日 dashboard 下使能 vs Leo 9月5日 APP 下使能 | 9月2日 dashboard 用直接 SDK 调用（脚本还没 disable.py） | **9月2日没有 disable.py 脚本，最早是 9月4日 14:19** |
| 6 | server 用 v4 disable.py（绕过 SDK）| 必须用 v5（标准 SDK 接口）| **v4 disable.py 已被 v5 替换，备份在 .bak.v4_broken.20260905_145400** |
| 7 | test 期间被 Leo 反问"为什么 dashboard 一下就放下来" | 因为 dashboard 直接用 `robot_enable_down()` 触发中断，APP→server→v4 绕过中断 | **Leo 反问经常是发现真相的钥匙，要认真对待** |

---

## 7. 文件索引

### 当前生产文件
- `/data/linglong-project/scripts/linglong/disable.py` —— **v5**（1211 bytes）
- `/data/linglong-project/scripts/voice_cmd/voice_cmd_server.py` —— INTENT_RULES 顺序已修复

### 备份文件
- `disable.py.bak.20260904_141949` —— 9月4日原版（370 bytes）—— 等同 v5 设计
- `disable.py.bak.before_v4_restore.20260905_141125` —— v4 之前（一字未改）
- `disable.py.bak.20260905_133500` —— v4 中间版（749 bytes）—— **错的**
- `disable.py.bak.v4_broken.20260905_145400` —— **v4 我写错的版本（749 bytes）—— 保留作证据**
- `voice_cmd_server.py.bak.before_intent_reorder.20260905_151019` —— INTENT_RULES 修复前

### SKILL 文件
- `skills/linglong-sdk-debug-SKILL.md` —— 通用 SDK 调试（含 disable 工作流）
- `skills/linglong-vla-SKILL.md` —— VLA 训练主 SKILL

### 调试日志
- `memory/2026-09-05.md` —— 今天的调试全过程

---

## 8. 启动 server 的正确方式（2026-09-05 验证）

```bash
bash -c 'cd /data/linglong-project && nohup /data/linglong-project/envs/vla/bin/python /data/linglong-project/scripts/voice_cmd/voice_cmd_server.py --tcp-port 7777 --http-port 7778 > /tmp/voice_cmd_server.log 2>&1 & disown; sleep 1'
```

**关键**：
- `bash -c '...'` 包装整个命令
- `nohup` + `> log 2>&1` 让进程脱离 shell
- `& disown` 让 background job 不被 bash 关联
- `; sleep 1` 给启动时间再返回

**不要用**：
- 单纯 `nohup ... &` —— paramiko 通道会卡住
- `setsid ...` —— 也会卡
- `(...) &` subshell —— 也会卡

---

## 9. 调试心得（来自今天的复盘）

**Leo 14:38 反问是今天最重要的转折点**：

> "没道理，因为在没有app和HTML时，通过dashboard命令进行操作，一下就放下来了"

这句话揭穿了 v4 的"持续发包"思路——直接证明 dashboard 工作 = 原版 `robot_enable_down()` 工作 = `_notify_control_switch` 中断是关键。

**教训**：
- **认真对待 Leo 的反问**——Leo 经常凭直觉发现真相
- **不要急于推进自己假设的方案**——多问 Leo 之前怎么做的
- **脚本可能改坏功能**——v4 是我"改进"反而改坏了，恢复原版才对

---

**SKILL 结束** | 版本 V1.0 | 2026-09-05 15:25
