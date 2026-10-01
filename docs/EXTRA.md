# 灵龙国庆版本 - 补充文档 (2026-09-29)

## 1. AGV 完整站点坐标（来自记忆）

### 仙工 AMB-150 AGV (192.168.1.204)

| 站点 ID | 中文名 | 9/12 slam_demo_2d 坐标 | 9/29 重定位后实测 |
|--------|-------|---------------------|------------------|
| LM1 | 工位3 / 工位三 | 未实测 | 未实测 |
| LM3 | 工位1 / 工位一 | (-6.141, 8.283) | (-0.16, 2.03) ✅ |
| LM4 | 工位2 / 工位二 | (-8.515, 8.670) | 未实测 |
| LM5 | 门口 | (-0.15, 9.18) | 未实测 |

**注意**：不同地图坐标系不同！实测坐标跟当前加载的 smap 一致才有效。

### 9/12 完整 landmark
- 门口: (-0.15, 9.18) facing -17.7 度 (面朝门外)
- 工位1 (Walt): (-6.141, 8.283) facing -21.5 度
- 工位2: (-8.515, 8.670) facing 171.4 度
- 工位1 → 工位2: 1.55m
- 工位2 → 门口: 4.35m

### AGV 仙工 SLAM 地图 (D:\灵龙\灵龙嵌入式和APP的架构设计文档和代码\)
- AGV_System_Architecture.docx (61KB)
- AGV_System_Architecture.docx.bak (817KB)
- AGV_System_Architecture_2026-09-08.zip (1.7MB) ← 包含 smap_helper.py + 9 个单元测试
- 仙工 AGV 底盘 TCP 协议.docx
- 仙工 Robokit AGV 底盘扫图调测手册.doc

## 2. Robokit AGV TCP 端口

| 端口 | 协议 | 命令 |
|------|------|------|
| 19204 | state | 1005 速度 / 1007 电池 / 1004 位置 |
| 19205 | cmd | 2010 底盘运动 / 2000/2002/2003/2004/2010/2022/2024/2025/2026 |
| 19206 | nav | **3051 路径导航** / 3066 |
| 19207 | cfg | 4005 lock / 4006 unlock / 4100/4151 motor clear fault |
| 19301 | push | 主动推送 |

## 3. RoboShop Pro 操作手册（关键）

⚠️ **必须完全关闭 RoboShop Pro**：
- RoboShop Pro 占着控制权时
- 我发 6201/3051/2010 等命令 → ret_code=0 但**不真正执行**
- 2010 直接返回 ret_code=40020 + "control is preempted..."
- Leo 必须**完全关闭 RoboShop Pro**或点「释放控制权」按钮

## 4. AGV 重定位步骤

### 上电后必须做
1. Leo 遥控 APP 把灵龙推到 landmark 附近（门口/工位1/工位2）
2. AGV 自动识别 landmark，修正坐标
3. 检查 `current_station` 字段非空 + confidence > 0.5

### 验证重定位
```bash
# TCP 1004 查位置
python -c "
import socket, struct, json
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(5)
s.connect(('192.168.1.204', 19204))
header = struct.pack('>BBHIH', 0x5A, 0x01, 1, 0, 1004) + b'\x00' * 6
s.send(header)
data = s.recv(1024)
print(json.loads(data[16:].decode('utf-8', errors='replace')))
"
```

## 5. Cron Jobs (2026-09-29 当前)

| Job ID | 名字 | 频率 | 状态 |
|--------|------|------|------|
| 40d85814 | SoybeanAM_Prediction_7AM | 0 7 * * 1-5 | ok (cron error 1) |
| 6eae9f1b | Session_Rename_Daily_3AM_group | 0 3 * * * | error (timeout) |
| db9988c2 | Session_Rename_Daily_3AM_direct | 0 3 * * * | ok |
| 42d22482 | Session_Briefing_Daily_5AM_group | 0 5 * * * | ok (delivery mode changed) |
| ad940cf0 | Session_Backup_Daily_6AM | 0 5 * * * | ok |
| 45c79079 | SoybeanPM_Prediction_1230 | 30 12 * * 1-5 | disabled |
| dba91777 | SoybeanEVE_Prediction_1800 | 0 18 * * 1-5 | disabled |

## 6. Linglong Monitor

- 路径：/home/user/linglong-monitor/
- systemd service: linglong-monitor.service (enabled + active)
- 数据：data/episode_NNN_*/
- 日志：logs/events.log
- 重启：SIGTERM (user 权限)

## 7. 防火墙端口清单（需要开放的）

| 端口 | 协议 | 方向 |
|------|------|------|
| 22 | TCP | Windows → DGX / DGX → Linglong |
| 7780 | TCP | Linglong → DGX (ASR) |
| 7792 | TCP | Linglong → DGX (LLM) |
| 7793 | TCP | Linglong → DGX (Action) |
| 7781 | TCP | Windows → DGX (Chassis) |
| 9003 | TCP | Linglong → DGX (TTS) |
| 19204 | TCP | DGX → AGV (state) |
| 19205 | TCP | DGX → AGV (cmd) |
| 19206 | TCP | DGX → AGV (nav) |

## 8. 仙工 AMB-150 重启流程

1. 关闭 RoboShop Pro
2. 拔 AGV 电源 5 秒
3. 重启 AGV（底盘二维码黄灯亮）
4. SSH 重启 chassis_server_v123.py + action_router
5. 检查 chassis_server 健康 `/health`

## 9. RoboShop Pro 卸载命令（如需要）

Windows:
```
sc delete RoboShopPro
```

## 10. 已知 Bug / 踩坑

1. **灵龙 → DGX 网络不通**：必须 SSH -R 隧道
2. **AGV 上电后必须重定位**（similiarity=0 时导航失败）
3. **chassis_server bug**：用户 disable 后 10 秒 watchdog 切回 teleop
4. **RoboShop Pro 占控制权**：ret_code=0 但不执行
5. **MemMagicOS WebView fetch 不可靠**：必须用 Java MediaRecorder 原生插件
6. **JAVA_HOME 必须设**：否则 APK 编译失败
7. **tts_v2_server.py 启动慢**（首次要加载 Qwen3-TTS 1.7B 模型 ~30秒）
8. **disable.py v4/v5**：必须走 SDK 标准接口，不能直接发 UDP
9. **chassis_server MAX_START_DURATION=24h**：发 start 必须带 duration 字段
10. **action_router chinese_to_arabic**：五百/十五/五百度都要正确转换

## 11. Skill 列表

详见 skills/ 目录：
- voice-v3-listener-SKILL.md ⭐ 核心
- android-magic-os-bypass-SKILL.md
- chassis-action-stop-timer-SKILL.md
- linglong-chassis-ackermann-SKILL.md
- linglong-chassis-safety-SKILL.md
- linglong-dgx-ssh-SKILL.md
- linglong-disable-SKILL.md
- linglong-sdk-debug-SKILL.md
- linglong-voice-qa-SKILL.md
- linglong-vla-SKILL.md
- orchestrator-module-reload-SKILL.md
- xiangong-agv-slam/

## 12. 紧急联系

- Leo: 飞书 ou_0c664a6efe1cc371b66359cd2131115b
- DGX SSH: kk@10.86.51.122 / ${DGX_PASSWORD}
- 灵龙 SSH: $LINGLONG_USER@$LINGLONG_HOST / admin
- AGV IP: 192.168.1.204

---

_补充文档 · Walt 2026-09-29 12:03_
