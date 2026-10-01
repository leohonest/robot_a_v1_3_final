# 灵龙 SDK 调试 SKILL (v1.1)

**版本**: V1.1 (2026-09-02 12:42 完成今日调试总结)
**作者**: Walt
**目的**: 记录灵龙 SDK 调通过程的关键教训，避免下次再踩坑

**今日调试范围**：
- ✅ Enable + Autonomous Mode
- ✅ reset_to_init (L-shape 归位)
- ✅ set_cap + send() 灵巧手控制
- ✅ robot_enable_down (安全下使能)
- ✅ 重新上电后二次使能

---

## 1. 灵龙网络架构（必须先搞清楚！）

灵龙系统是 **多 IP** 架构，**不是** 单 IP：

| 设备 | IP | 端口 | 作用 |
|------|-----|------|------|
| **手臂控制器** | **192.168.1.28** | UDP 3334 (cmd) / 3333 (state) / 4141 (mode) / 8080 (status) | **SDK 直接对接的设备** |
| 数采主控 (Jetson) | 192.168.1.12 | SSH 22, ROS topics | udp_bridge 跑在这里，转发 ROS <-> 手臂 |
| 底盘 (chassis) | 192.168.1.204 | TCP 19204/19205/19206 | 底盘控制 |

**最容易踩的坑**：默认 `LinglongHSdkClass(ip='192.168.1.28')` 是对的，但 `grip_client.py` 老版本写的是 `192.168.1.12`，那是 Jetson，**不是手臂控制器**。

**配置来源**：
- `~/.openclaw/workspace/skills/linglong-vla-SKILL.md`（V3）
- 灵龙 `/home/user/gateway/config/network.toml`：
  ```toml
  [arm_udp]
  ip = "192.168.1.28"
  status_port = 8080
  control_port = 3334
  mode_port = 4141
  status_broadcast_port = 4142
  ```
- 灵龙 `/home/user/app_v1.4.5_20260807/app/udp_bridge/install/udp_bridge/share/udp_bridge/config/robot_config.yaml`：
  ```yaml
  robot:
    ip: "192.168.1.28"
    port: 3333
  ```

---

## 2. SDK 初始化三步曲

```python
from linglong_h_sdk import LinglongHSdkClass, RobotModeManager

ARM_IP = '192.168.1.28'

# 1. 上使能 (mode port 4141)
mode_mgr = RobotModeManager(ARM_IP, port=4141)
mode_mgr.robot_enable_up()       # 4秒 enable=1 + 1秒 enable=0
mode_mgr.robot_autonomous_mode() # 切自主模式
mode_mgr.close()

# 2. 主 SDK (cmd port 3336, state port 3333)
sdk = LinglongHSdkClass(
    ARM_IP,
    port=3336,           # 控制端口
    state_port=3333,     # 状态端口
    auto_state_thread=True,  # 50Hz 后台收状态
)

# 3. 移动到 L 型 home
sdk.reset_to_init(send_time=3.0, mode='eef', interp_start='ctrl')
```

**L 型 home 位置**（`reset_to_init` 的目标）：
- L_EE = (+0.30, +0.25, +0.65)
- R_EE = (+0.30, -0.25, +0.65)
- 腰部 z = 0.80m

---

## 3. 调试链路选择（重要！）

**不要从 Windows Walt 跑 SDK 命令**：理由如下
- Windows → DGX 是校园网，有被拦截风险
- Windows → 灵龙的 UDP 包容易被网络设备拦截

**应该用哪种链路？**

| 链路 | 速度 | 安全性 | 推荐度 |
|------|------|--------|--------|
| Windows → DGX SSH → 灵龙 | 慢 | 低 | ❌ |
| **DGX → 灵龙 (同 WiFi 192.168.1.x)** | 快 | 高 | ✅ 推荐 |
| **灵龙本地 → SDK (回环)** | 最快 | 最高 | ✅ 推荐 |

**最佳实践**：
1. **Python 脚本写到 DGX 的 `/tmp/`**（paramiko SFTP 上传）
2. DGX 上 `python3 /tmp/script.py` 直接执行
3. 或上传到灵龙 `/home/user/` 本地跑（更稳）

---

## 4. 诊断流程（按顺序）

### 4.1 第一步：确认 IP 正确
```bash
# DGX 上
ping -c 2 192.168.1.28     # 必须通
nc -zu -w 2 192.168.1.28 3334  # 控制端口
nc -zu -w 2 192.168.1.28 4141  # mode 端口
```

### 4.2 第二步：确认 SDK 路径
- DGX: `/data/linglong-project/sdk/linglong_h_sdk/`（Python 3.12）
- 灵龙: `/home/user/sdk/linglong_h_sdk/`（Python 3.10）

### 4.3 第三步：用 GripClient 试 enable
```python
from grip_client import GripClient
import time
c = GripClient(robot_ip='192.168.1.28')
c.enable()  # robot_enable_up + robot_autonomous_mode
time.sleep(2)
c.get_state()  # 检查 cap_L/R 和 arm 关节
c.close()
```

### 4.4 第四步：验证 reset_to_init
```python
ok = sdk.reset_to_init(send_time=3.0, mode='eef', interp_start='ctrl')
state = sdk.fetch_robot_state(timeout=2.0)
print(state.epos_h[0,:3])  # 应该接近 [0.30, 0.25, 0.65]
```

### 4.5 第五步：交叉验证 (灵龙 ROS)
```bash
source /opt/ros/humble/setup.bash
source /home/user/app_v1.4.5_20260807/app/udp_bridge/install/setup.bash
ros2 topic echo /end_pos --once
```
应该看到 `ee_pose_l ≈ [+0.30, +0.25, +0.65]`

---

## 5. 踩坑清单（永久记住）

| # | 错误 | 真相 | 教训 |
|---|------|------|------|
| 1 | `grip_client.py` 用 `192.168.1.12:8080` | 手臂控制器在 `192.168.1.28`，端口是 3334/3333/4141 | **查 network.toml，别猜 IP** |
| 2 | `maniSdkClass.fetch_robot_state()` 全 0 | 状态收不到 → SDK 连的不是手臂控制器 | **先 verify 状态，再 send 命令** |
| 3 | `RobotModeManager` 在 DGX 跑，没 enable_up | 没用 GripClient，直接调用底层方法 | **用高层封装 GripClient** |
| 4 | `LinglongHSdkClass` 没 `robot_enable_up` | enable_up 在 `RobotModeManager`，不在主 SDK | **enable 和 send 是两个独立类** |
| 5 | `reset_to_init` 后位置不变 | SDK 命令发到了 Jetson (192.168.1.12)，不是手臂 | **IP 错了就一切白搭** |
| 6 | `ManiInterpStartSource` ImportError | DGX SDK 有，**灵龙本地 SDK 没有** | **跨平台时检查 import** |
| 7 | cap_rate 语义搞反 | 0.0=松开, 1.0=**握住**（不是反的！） | **一对一验证: 问 Leo 当前值，发命令，看物理反应** |

---

## 6. 验证 L 型成功的标志

✅ `state.epos_h[0,:3]` ≈ `[0.30, 0.25, 0.65]`
✅ `state.epos_h[1,:3]` ≈ `[0.30, -0.25, 0.65]`
✅ `state.epos_waist[2]` ≈ `0.80`
✅ `state.q[0]` 和 `state.q[1]` 关节角度跟上 L 型
✅ `/driver_pvt` topic 中 q == q_exp（跟踪误差 ≈ 0）

---

## 7. 常用脚本

| 脚本 | 用途 |
|------|------|
| `scripts/diag_linglong_comm.py` | ping + 端口扫描（只读） |
| `scripts/check_linglong_via_paramiko.py` | DGX → 灵龙 SSH 隧道 + 状态检查 |
| `scripts/grip_client.py` | 高层 SDK 封装（DGX 端） |
| **新增**: `scripts/linglong_arm_enable_reset.py` | DGX 上 enable + reset_to_init 一把梭 |
| **新增**: `scripts/linglong_arm_verify_lshape.py` | 验证 L 型位置 |

---

## 8. 相关文件索引

- 详细 SKILL：`skills/linglong-vla-SKILL.md`（V3, 8-13 创建）
- MEMORY 索引：`MEMORY.md`（找"灵龙"段）
- 今日调试日志：`memory/2026-09-02.md`
- 脚本：`scripts/linglong_*.py`

---

## 6.1 ⛔ cap_rate 语义（重大修正 2026-09-02 12:22）

**WRONG（之前理解）：**
- `cap=0.0` = 握紧
- `cap=1.0` = 张开

**RIGHT（Leo 一对一纠正后）：**
- `cap=1.0` = **持续握住**（保持命令）
- `cap=0.0` = **松开**（释放命令）

**为什么搞反了**：
- `grip_client.py` 注释写的是反的
- 没亲自物理验证就信了文档

**教训**：
- **永远一对一验证**：问 Leo 当前值 → 发命令 → 看物理反应
- 不要凭文档推断
- 类似的语义字段（速度、力矩）也要先验证

**修正后正确调用**：
```python
sdk.set_cap(1.0, 1.0)  # 握住（不是张开！）
sdk.send()              # 必须 send
```

---

## 9. ⭐ 完整 enable → disable 工作流（生产推荐）

```python
import sys, time
sys.path.insert(0, '/data/linglong-project/sdk')
from linglong_h_sdk import LinglongHSdkClass, RobotModeManager

ARM_IP = '192.168.1.28'

# === ENABLE ===
mode_mgr = RobotModeManager(ARM_IP, port=4141)
mode_mgr.robot_enable_up()
time.sleep(1)
mode_mgr.robot_autonomous_mode()
time.sleep(1)
mode_mgr.close()

sdk = LinglongHSdkClass(ARM_IP, port=3336, state_port=3333, auto_state_thread=True)
time.sleep(1)

# L-shape 归位
sdk.reset_to_init(send_time=3.0, mode='eef', interp_start='ctrl')
time.sleep(4)

# 验证
state = sdk.fetch_robot_state(timeout=2.0)
assert abs(state.epos_h[0,0] - 0.30) < 0.05, "L_EE.x not home"
assert abs(state.epos_h[1,0] - 0.30) < 0.05, "R_EE.x not home"
assert abs(float(state.epos_waist[2]) - 0.80) < 0.05, "waist z not home"

# 灵巧手控制（握紧 = 1.0, 松开 = 0.0）
sdk.set_cap(1.0, 1.0)  # 握紧
sdk.send()

# === DISABLE ===
mode_mgr = RobotModeManager(ARM_IP, port=4141)
mode_mgr.robot_operation_mode()
time.sleep(2)
mode_mgr.robot_enable_down()

sdk.close()
mode_mgr.close()
```

---

**SKILL 结束** | 版本 V1.1 | 2026-09-02 12:42