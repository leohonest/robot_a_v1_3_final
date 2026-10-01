# 灵龙动作路由 SKILL (linglong-action-router-SKILL.md)

> 关键词模糊匹配 → 直调 DGX 服务端（不复用 SDK 客户端）
> 版本：v0.3  2026-09-24（新增 UDP 风格 chassis_pulse + 四层 watchdog）

## 警告：chassis_pulse 已改成 UDP 风格（2026-09-23 17:30 Leo 拍板）

**旧设计（APP 路径，错误用于 voice）**：
- `POST /chassis/start` → 后台线程每 500ms heartbeat → `POST /chassis/stop`
- 问题：voice 路径不需要 heartbeat，watchdog 会因 heartbeat 超时 AUTO STOP

**新设计（UDP 风格，voice 路径正确方式）**：
```python
def chassis_pulse(action, params):
    """UDP 风格：发 start + duration，立即返回（无后台线程/无 heartbeat）"""
    resp = requests.post(
        f'http://{CHASSIS_HOST}:{CHASSIS_PORT}/chassis/start',
        json={
            'vx': vx, 'omega': omega,
            'session_id': f'voice_{int(time.time()*1000)}',
            'duration': duration_s   # ← 关键：duration 传到 chassis_server
        },
        timeout=5
    )
    return {'status': 'ok', 'action_id': action_id, 'duration_s': duration_s}
    # ↑ 立即返回，不等 duration 结束，不后台线程，不 heartbeat
```

**⚠️ 绝对禁止**：在 voice 路径里模仿 APP 用 start + heartbeat + stop（Leo 17:30 原话）

## 一、设计目标（Leo 2026-09-21 16:07 拍板）

> "关于去控制底盘、控制上身电机关节的这些部署，**服务器端不就在 DGX 上面吗**？识别出来以后，把命令编辑成**从手机 APP 客户端发过来的那个一样的命令**，然后**扔给那个 APP 操控服务器端吧**。"

**核心思路：复用现有 APP 服务端，不重新发明轮子**

```
┌─────────────────────────────────────────────────────────┐
│ v3 listener (灵龙 mic → ASR → query)                     │
│     │                                                    │
│     ▼  POST /action  {query: "..."}                      │
│ ┌──────────────────────────────────┐                    │
│ │ action_router.py (DGX :7793)      │ ← 新建             │
│ │  • 关键词模糊匹配                  │                    │
│ │  • 17 个动作字典                   │                    │
│ │  • 命中 → 转发 HTTP 给下游服务端  │                    │
│ │  • miss →  fallback 到 7792 LLM   │                    │
│ └──────────────────────────────────┘                    │
│     │ 命中                                                  │
│     ├──→ voice_cmd_server :7778  (上身动作)              │
│     ├──→ chassis_server :7781    (底盘矢量运动)          │
│     └──→ AGV 19206 (3051 nav)    (站点↔站点导航)        │
└─────────────────────────────────────────────────────────┘
```

## 二、动作字典（v0.2 正式版）

### 2.1 上身动作（7 个 — voice_cmd_server :7778 处理）

| ID | 触发词 | 命令 | 实现 |
|----|--------|------|------|
| `enable` | 上使能/启动/上电/使能/enable | `robot_enable_up()` | voice_cmd_server 已支持 |
| `disable` | 下使能/关闭/断电/disable | `robot_enable_down()` | voice_cmd_server 已支持 |
| `home` | 归位/复位/回原/home/reset/回初始 | L-shape home | voice_cmd_server |
| `grip_L_open` | 松左手/左手松/open_left/左手张开 | `gripper_cap(L, 0.0)` | voice_cmd_server |
| `grip_L_close` | 握左手/左手握/close_left/左手抓住 | `gripper_cap(L, 1.0)` | voice_cmd_server |
| `grip_R_open` | 松右手/右手松/open_right/右手张开 | `gripper_cap(R, 0.0)` | voice_cmd_server |
| `grip_R_close` | 握右手/右手握/close_right/右手抓住 | `gripper_cap(R, 1.0)` | voice_cmd_server |

### 2.2 底盘矢量运动（5 个 — chassis_server :7781 处理，Leo 2026-09-22 06:47）

**核心原理**：矢量距离（米/度）÷ 速度（0.1 m/s）= 运动时间

**实现机制**（模拟 APP 遥杆）：
1. `POST /chassis/start {vx, omega, session_id}` → 启动持续运动
2. 起后台线程每 500ms 发一次 `POST /chassis/heartbeat {session_id}`
3. 等 `duration_s`（计算得出）
4. `POST /chassis/stop {session_id}` → 停止

| ID | 触发词 | 速度 | 默认值 |
|----|--------|------|--------|
| `chassis_fwd` | 前进[0.3米]/往前走/直走/向前 | 0.1 m/s | 0.3m → 3s |
| `chassis_back` | 后退[0.2米]/倒退/向后 | 0.1 m/s | 0.2m → 2s |
| `chassis_left` | 左转[15度]/左拐 | 0.1 rad/s ≈ 5.73°/s | 15° → 2.6s |
| `chassis_right` | 右转[15度]/右拐 | 0.1 rad/s ≈ 5.73°/s | 15° → 2.6s |
| `chassis_stop` | 停/停止/刹车/stop/halt | — | 发 stop |

**参数提取正则**：
```
distance: r'(?:前进|后退)\s*(\d+(?:\.\d+)?)\s*(?:米|m|公尺)?'
angle:    r'(?:左转|右转)\s*(\d+(?:\.\d+)?)\s*(?:度|°|degrees?)?'
```

### 2.3 自主导航（AGV 3051，Leo 2026-09-22 06:34）

**触发格式**：
- 单独目的地：`去门口` / `回工位1` / `回到工位1`
- 站点到站点：`从工位1去门口` / `从门口回到工位1`

**站点映射**（Leo 2026-09-22 06:34）：
- 工位1 → LM3
- 工位2 → LM4
- 门口 → LM5

**实现**：直接 TCP 到 AGV `:19206`，发 3051 路径导航 JSON（带 task_id）

## 三、文件位置

- **本 SKILL**: `skills/linglong-action-router-SKILL.md`
- **动作路由源码**: `scripts/action_router.py` (474 行，DGX 上)
- **测试脚本**: `scripts/_test_action.py`

## 四、部署记录

- 2026-09-22 07:00 上传到 DGX `/data/linglong-project/scripts/action_router.py`

## 五、待办

- [ ] 把 action_router 接入 orchestrator（query 先过 action_router，miss 才走 7792 LLM）
- [ ] voice_cmd_server `/intent` HTTP 端点（接上身 7 动作）
- [ ] 现场实测：前进/后退/左转/右转/停（Leo 在场）
- [ ] 现场实测：去门口/回工位1（AGV 3051 nav）


---

## 六、Leo 2026-09-22 16:00 诊断：arm 动作没生效根因

### 6.1 症状
- 发送 "上使能" / "下使能" -> action_router 返回 dry_run 状态
- 灵龙硬件**完全不动**

### 6.2 根因
看 scripts/action_router.py: dispatch() 函数：

```python
if target == 'arm':
    # 灵龙手臂动作（占位，明天接 voice_cmd_server）
    return {
        'status': 'dry_run',
        'note': 'arm action -> voice_cmd_server :7778（待接）',
        'action_id': action['id'],
        'params': params,
    }
```

**arm 动作硬编码返回 dry_run**，没有真正调用 voice_cmd_server :7778 的 `/intent` 端点。

### 6.3 修复路径（待 Leo 拍板）

1. **Leo 拍板** ACTION_ROUTER_DRYRUN 环境变量为 0
2. **实现** `call_voice_cmd_server()` 函数（POST 到 :7778/intent）
3. **实现** `voice_cmd_server.py` 的 `/intent` HTTP 端点（目前只接 HTTP /cmd 走 TCP）
4. **实测**：Leo 在场发上使能 -> 机器人 enable 真生效

### 6.4 当前测试只验证

- OK 底盘矢量运动（前进 0.3m -> 真到 0.3m）— 已通
- OK 3051 路径导航（去门口 -> AGV 真到 LM5）— Leo 9/22 早上验证
- FAIL 上肢 7 个动作（dry_run 占位）— 待接 voice_cmd_server

### 6.5 Leo 设计意图（9/22 16:00）

> 服务器端就在 DGX，识别出来以后，把命令编辑成从手机 APP 客户端发过来的那个一样命令，然后扔给那个 APP 操控服务器端。

**实现架构**：

```
action_router.py:arm -> HTTP /intent {action, args} -> voice_cmd_server.py
                       -> 转回原来的 /cmd 流程
                       -> TCP 发到 chassis_control / arm_control
```

---

## 七、Leo 2026-09-22 17:00 任务：扩展 VLA 动作到所有上肢 + AGV

### 7.1 当前只有 enable + disable 两个 VLA 操作实现

**mobile APP 操控快捷键**：

- OK 上使能 / 下使能（enable/disable）

**没实现的快捷键**（用户实际能用）：

| 短按 | 长按 | Leo 设计目标 |
|------|------|----------|
| 归位 | home | OK 已实现 |
| 抓手 | grip | OK 已实现（松握） |
| **抬手** | raise | FAIL 待实现 |
| **摆头** | head move | FAIL 待实现 |
| **前倾** | lean forward | FAIL 待实现 |
| **侧倾** | side lean | FAIL 待实现 |
| 前进 | forward | OK 已实现（chassis_fwd） |
| 后退 | back | OK 已实现（chassis_back） |
| 左转 | turn left | OK 已实现（chassis_left） |
| 右转 | turn right | OK 已实现（chassis_right） |
| 去X | go to X | OK 已实现（3051 nav） |

### 7.2 口语映射（Leo 9/22 设计）

**核心思路**：把 APP 屏幕上的所有按钮按按钮 -> 翻译成口语命令 -> 让 voice_cmd_server 听懂。

| APP 按钮 | 口语命令 | 实现 |
|---------|---------|------|
| 上使能 | 上使能 / 启动 / 上电 | OK done |
| 下使能 | 下使能 / 关闭 / 断电 | OK done |
| 归位 | 归位 / 回家 / 复位 | OK done |
| 松左手 | 松开左手 / 左手松 | OK done |
| 握左手 | 握左手 / 左手抓 | OK done |
| 松右手 | 松开右手 / 右手松 | OK done |
| 握右手 | 握右手 / 右手抓 | OK done |
| 前进 0.3m | 前进0.3米 / 往前走 30 公分 | OK done |
| 后退 0.2m | 后退0.2米 / 往后退 20 公分 | OK done |
| 左转 45度 | 左转45度 / 向左转 90 度 | OK done |
| 右转 45度 | 右转45度 / 向右转 90 度 | OK done |
| 停 | 停 / 刹车 / 停止 | OK done |
| 走 0.3m 通透 | 通透 0.3 米 / 走过去 30 公分 | FAIL 待 Leo 解释语义 |

### 7.3 Leo 9/22 17:00 明确要求

> 目前只映射了上使能和下使能两个 VLA 操作，要把其他所有的上肢动作和 AGV 的动作前进，后退，左转，右转都实现，带矢量数据，距离 0.3 米，角度 45 度，通透都要实现。

**矢量默认值**：
- 距离：**0.3 米**（默认前进距离）
- 角度：**45 度**（默认转弯角度）

### 7.4 文件待办

- [x] 把默认前进距离 0.3m（OK 已经是）
- [x] 把默认转弯角度 15度 改成 45度（DGX 已部署 DEFAULT_ANGLE_DEG=45.0）
- [ ] scripts/vla_action_mapping.md — 完整口语映射表

---
_更新_2026-09-22 17:05_
