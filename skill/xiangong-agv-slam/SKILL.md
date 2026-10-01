---
name: xiangong-agv-slam
description: 仙工 Robokit AGV 底盘建图、扫图、保存地图、加载地图、站点导航、调速的完整流程。包括 slam_type 1/2/3/4 区别、6100/6101/1025/2025/2022/4011 命令格式、二进制协议头结构、resultmap 字段触发、AGV 真实 IP 192.168.1.204 + 6 个端口（19204 state / 19205 cmd / 19206 nav / 19207 cfg / 19301 push / 19210 other）、3051 不带 source_id 用 self-position、激光雷达 block 解决（虚拟激光）、路径级 max_speed 调速（0.1 m/s 推荐）。激活场景：用户提到仙工 AGV / Robokit / 底盘建图 / SLAM 扫图 / smap 地图 / 1025 / 6100 / 2025 / 3051 / 站点导航 / 调速 / 激光 block。
---

# 仙工 Robokit AGV 底盘建图和加载地图 SKILL

## 设备信息（永久）

| 项目 | 值 |
|------|-----|
| **AGV IP** | **192.168.1.204** |
| 设备型号 | 仙工 Robokit 系列 AGV 底盘 |
| 通信协议 | TCP Request/Response 自定义二进制协议 |
| 调试电脑网段 | 192.168.1.X（X≠204），子网 255.255.255.0 |
| 官方上位机 | Roboshop Pro |
| 文档位置 | `D:\灵龙\仙工Robokit AGV底盘扫图调测手册.doc` |

## 6 个 TCP 端口（永久）

| 端口 | 类别 | 关键命令 |
|------|------|---------|
| **19204** | state（机器人状态） | 1025 / 1004 / 1050 / 1020 / 1021 / 1300 |
| **19205** | cmd（机器人控制） | 2025 / 2022 / 2002 / 2003 / 2004 / 2010 / 3051 / 3066 |
| **19206** | nav（机器人导航） | 3051 / 3066 / 3055 / 3056 / 3058 / 3001 / 3002 / 3003 |
| **19207** | cfg（机器人配置） | 4010 / 4011 / 4005 / 4006 |
| **19301** | push（机器人推送） | 主动推送数据 |
| **19210** | other（其他） | 6100 / 6101 / 6030 / 6031 / 6201 |

⚠️ **6100/6101 是 SLAM 扫图，在 19210 端口（不是 19205！）**
⚠️ **3051/3066 路径导航在 19206 端口（不是 19205！）**

## 二进制协议头结构（永久）

每个报文 = **16 字节定长头 + JSON 数据区**

| 字段 | 类型 | 长度 | 说明 |
|------|------|------|------|
| m_sync | uint8 | 1B | 同步头（**固定 0x5A**） |
| m_version | uint8 | 1B | 协议版本（通常 0x01） |
| m_number | uint16 | 2B | 序号（大端序），匹配请求/响应 |
| m_length | uint32 | 4B | JSON 数据区长度（大端序） |
| m_type | uint16 | 2B | 报文类型编号（大端序） |
| m_reserved | uint8[6] | 6B | 保留（填充 0x00） |

**请求/响应示例**（3051 路径导航）：
```
请求:  5A 01 00 01 00 00 00 1C 07 D2 00 00 00 00 00 00  +  JSON{"x":10.0,"y":3.0,"angle":0}
响应:  5A 01 00 01 00 00 00 1C 07 D2 00 00 00 00 00 00  +  JSON{"ret_code":0,...}
```

## SLAM 扫图完整流程

### 步骤 1：开始扫图 6100 (19210 端口)

**官方推荐格式**（Leo 16:14 指定）：
```json
{
  "slam_type": 2,
  "real_time": true,
  "screen_width": 1280,
  "screen_height": 720
}
```

**slam_type 取值**：
| 值 | 含义 |
|----|------|
| **1** | 2D 离线扫图（无 real_time） |
| **2** | **2D 实时扫图（real_time=true 必填）** |
| 3 | 3D 离线 |
| 4 | 3D 实时 |

**响应**（16100 robot_other_slam_res）：
```json
{"create_on":"...","ret_code":0,"slam_type":2}
```
- ret_code=0 = 成功
- ret_code≠0 = 失败

### 步骤 2：手动遥控 AGV 转一圈

⚠️ **必须 Leo 手操遥控**，让 AGV 走遍办公室各个角落，雷达才能扫描完整环境。

### 步骤 3：停止扫图 6101 (19210 端口)

**JSON 数据区：空 `{}`**

**响应**（16101 robot_other_endslam_res）：
```json
{"create_on":"...","real_time":true,"ret_code":0,"slam_type":2}
```

### 步骤 4：查询扫图状态 + 拿 resultmap 1025 (19204 端口)

**必须传 `return_resultmap:true` 才会返回 resultmap！**

```json
{"return_resultmap": true}
```

**响应**（11025 robot_status_slam_res）：
| 字段 | 类型 | 描述 |
|------|------|------|
| **slam_status** | number | 0=没扫图 / 1=离线扫图 / 2=实时扫图 / 3=3D离线 / 4=3D实时 |
| ret_code | number | API 错误码 |
| create_on | string | 时间戳 |
| err_msg | string | 错误信息 |
| **resultmap** | string | 构建完成的地图（**base64 编码字符串**） |

⚠️ **停止建图后 resultmap 不会立即返回**，需要**轮询直到非空**！

### 步骤 5：保存 resultmap 为 .smap 文件

resultmap 是 base64 字符串，存为 `<自定义文件名>.smap`：

```python
import json
with open('resultmap.smap.txt', 'w') as f:
    f.write(rm)  # rm 是 resultmap 字段
```

⚠️ **扩展名必须是 .smap**（文件名随便，扩展名 .smap 即可）

### 步骤 6：上传并加载地图 2025 (19205 端口)

**JSON 数据区**：直接传保存的 .smap JSON 字典（不是 base64 字符串）

**响应**（robot_control_upload_and_loadmap_res）：
```json
{"create_on":"...","ret_code":0}
```
- ret_code=0 = 成功（地图上传 + 加载到 current_map）

⚠️ **2025 是"上传 + 加载"一步完成**，不需要再发 2022 加载。

### 步骤 7：查询地图列表 1300 (19204 端口)

```json
{}
```

**响应**：返回所有地图（含 MD5 / 大小 / 储存时间）

### 步骤 8：查询机器人位置 1004 (19204 端口)

```json
{}
```

**响应**：
| 字段 | 描述 |
|------|------|
| x, y | 位置（米） |
| angle | 航向（弧度） |
| loc_state | 定位状态（0=没定位 / 1=正在定位 / 2=已定位） |
| confidence | 置信度（0-1） |
| similarity | 相似度（0-1） |
| current_station | 当前站点名 |

## 路径导航（站点导航）

⚠️ **3051/3066 都需要地图里有站点（LM1/LM2）**，需要 Roboshop Pro 软件标记。

### 3051 路径导航（19206 端口）

JSON 数据区（**用站点名，不是坐标！**）：
```json
{
  "id": "LM2",
  "source_id": "LM1",
  "task_id": "12345678"
}
```

### 3066 指定路径导航（19206 端口）

JSON 数据区（多点序列）：
```json
{
  "move_task_list": [
    {"id": "LM2", "source_id": "LM1", "task_id": "12344321"},
    {"id": "AP1", "source_id": "LM2", "task_id": "12344322", "operation": "JackHeight", "jack_height": 0.2}
  ]
}
```

⚠️ **source_id 和 id 必须有直接相连的线路**（用 Roboshop Pro 贝塞尔曲线画）
⚠️ **task_id 不能重复**

### 完整流程（Roboshop Pro + API）

1. **建图完成**，smap 保存到控制器
2. **Roboshop Pro 打开地图**，编辑添加 LM1/LM2 站点
3. **保存地图**，推送回 SRC 底盘
4. API 调用 **6104 LoadMap** 加载带站点的地图
5. API 调用 **6100 StartSlam slam_type=0** 进入纯定位模式
6. 下发 **6106 导航任务** 执行 LM1 → LM2

## 常见错误码

| ret_code | 含义 | 解决方案 |
|----------|------|---------|
| 0 | 成功 | - |
| 40001 | 参数缺失 | 检查 JSON 字段名 |
| 40050 | need map header | 4010 加载地图 JSON 不全 |
| 52801 | "Doesn't support key: X" | JSON 字段名 AGV 不识别 |
| 60002 | error data region | JSON 格式错误 |

## 永久脚本（已验证可用）

| 脚本 | 功能 |
|------|------|
| `scripts/test_6100_v3.py` | 6100 完整参数触发扫图 |
| `scripts/test_6101.py` | 6101 停止扫图 |
| `scripts/save_smap.py` | 1025 轮询 + 保存 .smap |
| `scripts/test_2025_v2.py` | 2025 上传并加载地图 |
| `scripts/test_1004.py` | 1004 查询位置 |
| `scripts/check_nav.py` | 1004 + 1020 + 1050 综合查询 |
| `scripts/test_3051_lm1_to_lm2.py` | LM1 → LM2 基础站点导航（首次成功） |
| `scripts/test_3051_lm3_to_lm2.py` | LM3 → LM2（踩激光 block + 标准朝向坑） |
| `scripts/test_3051_no_source.py` | 3051 不带 source_id（用 self-position 起点） |
| `scripts/test_3051_lm4_to_lm2.py` | LM4 → LM2（成功但速度太快） |
| `scripts/test_3051_speed.py` | 3051 三种速度参数测试（speed/max_speed/velocity） |

## 永久教训（2026-09-12 9-12 AGV 调试教训）

1. ⛔ **6100/6101 端口 = 19210**（other），**不是 19205**（cmd）！
2. ⛔ **1025 必须传 `return_resultmap:true` 才会返回 resultmap**
3. ⛔ **resultmap 是 base64 字符串**，存成 .smap 后才能用 2025 上传
4. ⛔ **3051/3066 需要地图里有 LM 站点**，纯坐标不能导航（要 Roboshop Pro 标记）
5. ⛔ **AGV IP = 192.168.1.204**（不是 192.168.4.250！Walt 多次幻觉！）
6. ⚠️ **停止扫图后 resultmap 不会立即返回**，要轮询（最长几分钟）
7. ⚠️ **Leo 16:14 指定 6100 完整参数**：slam_type=2, real_time=true, screen_width=1280, screen_height=720
8. ⛔ **AGV 激光雷达敏感，靠近障碍会自动 block**（2026-09-14 LM3 踩坑）
9. ⛔ **3051 nav 强制 AGV 转回 source_id 的"标准朝向"**（2026-09-14 重大坑）
10. ⛔ **重新上传同一张地图不会改 AGV 朝向**——AGV 朝向是运行时状态，地图是静态数据
11. ⛔ **AGV 默认速度太快**（约 0.5-1.0 m/s），需要调速（2026-09-14 教训）
12. ⛔ **AGV 总是 block 的真凶 = 地图噪声 + 底盘安全行为**（2026-09-14 研发人员确认）：扫图没扫干净 + AGV 检测到障碍试推不过去就停车
13. 📌 **永久解法优先级**：Roboshop Pro 修补地图 > 虚拟激光 > 重新扫图（治本但费时）

## 站点导航实战（2026-09-14 自主导航踩坑全记录）

### ✅ LM1 → LM2 自主导航成功

最简单的两站点导航，ret_code=0，AGV 顺利完成。**证明基础 3051 nav 工作正常**。

### ❌ LM3 → LM2 失败：激光雷达 block

**坑 1：LM3 太靠近障碍**（地图里 LM3 周围 0.3m 内有墙/柱子/杂物）
- AGV 走到 LM2 门口最后 0.13m 时**激光雷达检测到周围物体 → 触发 block**
- AGV **"一顿一顿"地尝试移动但每次都被阻止**，最终停在 LM2 门口 0.13m 处
- **真凶**：AGV 自带的激光雷达（不是我们写的代码）

**坑 2：3051 强制 AGV 转回 LM3 标准朝向**
- Leo 手动把 AGV 转了 120° 顺时针（面朝 LM2）
- 发了 3051 后，AGV **第一步 = 逆时针转 120° 转回 LM3 在地图里定义的标准朝向**
- 之后才开始走路径到 LM2
- **真凶**：3051 算法把 source_id 的"标准朝向"作为路径规划起点

**坑 3：重新上传同一张地图不解决问题**
- Leo 重传 office1.smap 给 AGV
- AGV 还是会转 120° 回 LM3 朝向
- **因为 LM3 在地图里的标准朝向没改**，重传只刷新磁盘数据不刷新"标准朝向"

### ✅ 3051 不带 source_id（重要技巧）

如果不想让 AGV 转回 source_id 的标准朝向，**3051 JSON 不传 source_id**：

```json
{
  "id": "LM2",
  "task_id": "curpos_to_lm2_xxx"
}
```

**AGV 会用当前 self-position 作为起点**（不强制对齐 source_id 朝向）。

✅ **验证过**：ret_code=0，AGV 接受命令。

⚠️ **注意**：不带 source_id 后，AGV 从当前位置到 LM2 自己规划路径，**不保证路径经过 LM1 也不保证避开 block**。

### ✅ 重新编辑地图 + 加 LM4 站点

**解决方案**：
1. Roboshop Pro 打开 office1.smap
2. **加新站点 LM4**（位置比 LM3 宽敞，离障碍 0.5m+）
3. 把 AGV 物理移到 LM4 位置
4. 保存地图 → 重新上传（2025）

**结果**：LM4 → LM2 自主导航 ret_code=0，AGV 顺利从 LM4 走到 LM2。

### ⚠️ 调速：AGV 默认速度太快

LM4→LM2 自主导航成功了，但**速度太快（感觉约 0.5-1.0 m/s）**——危险。

**3 种调速方法**（推荐度排序）：

#### 🥇 推荐：路径级 max_speed（最稳）

**Roboshop Pro** 里 LM4→LM2 的 path 上右键 → 属性 → `max_speed` = 0.1 m/s

**优点**：
- 一次设置，所有走这段 path 的任务都生效
- 不会因为 3051 参数被忽略而失败
- Leo 可以给不同 path 设不同速度（走廊快、门口慢）

#### 🥈 备选：3051 per-task 参数

JSON 里加以下任一参数（都接受 ret_code=0 但**不确认哪个真生效**，需实测）：
```json
{"source_id": "LM4", "id": "LM2", "speed": 0.1, "task_id": "..."}
{"source_id": "LM4", "id": "LM2", "max_speed": 0.1, "task_id": "..."}
{"source_id": "LM4", "id": "LM2", "velocity": 0.1, "task_id": "..."}
```

⚠️ AGV 接受参数 ≠ 真的减速——Leo 上电后实测才知道。

#### 🥉 全局配置：4010 / 4011

cmd port 上 4010 / 4011 两个 config 类型命令，**可能是设全局最大速度**。
**未实测**，文档没明确。

### ⛔ 解决激光 block：虚拟激光

Roboshop Pro → "定位信息" 面板 → 点"**虚拟激光**"按钮（变绿 = 启用）

**原理**：强制覆盖激光雷达 block 检测，让 AGV 继续走完最后那段被 block 的距离。

⚠️ **现场安全**：启用虚拟激光前确认真没障碍（人/物），否则 AGV 会撞。

### 📌 研发人员权威解释（2026-09-14 13:34 Leo 转述仙工研发）

> **根因：地图质量 + AGV 安全行为叠加**

**机制**：
1. **扫图时有些区域没扫干净** → 地图里有"灰色地带"（不是真障碍但 AGV 误以为有）
2. **底盘行动区域受限**（AGV 安全设计）→ 检测到前方"障碍" → 试推几次 → 还过不去 → **判定为故障 → 停车**
3. **结果**：AGV 在地图噪声区域"一顿一顿"然后停下，**不是代码问题，是地图+安全策略**

**为什么总是 block LM2/LM3 周围**：
- LM2/LM3 这些站点附近 0.3-0.5m 范围扫图时 AGV 没走全
- 地图里这块区域有"未定义"或"灰色"标记
- AGV 到了就以为是墙

**永久解法**（按推荐度排）：

| 解法 | 实施 | 优点 | 缺点 |
|------|------|------|------|
| 🥇 **Roboshop Pro 修补地图** | 用地图编辑工具把灰色地带涂成 free space → 保存 → 2025 上传 | 一次解决，永久不 block | Leo 手动标比较费时 |
| 🥈 **虚拟激光** | 定位信息面板启用按钮 | 快速，立即生效 | 每次都要开 + 现场确认无障碍 |
| 🥉 **重新扫图（治本）** | 6100 + Leo 慢慢遥控走全每个角落 + 6101 + 1025 | 彻底干净 | 30-60 分钟 |

### 💡 终极解决 LM3 太靠近障碍

如果 LM3 一直 block：
- **首选**：Roboshop Pro 把 LM3 拖到宽敞位置（离障碍 0.5m+）→ 保存新地图 → 重新上传
- **次选**：每次走 LM3 路径前手动启用虚拟激光

## 永久脚本（2026-09-14 新增）

| 脚本 | 功能 |
|------|------|
| `scripts/test_3051_lm1_to_lm2.py` | LM1 → LM2 基础站点导航 |
| `scripts/test_3051_lm3_to_lm2.py` | LM3 → LM2（踩 block 坑） |
| `scripts/test_3051_no_source.py` | 3051 不带 source_id（用 self-position） |
| `scripts/test_3051_lm4_to_lm2.py` | LM4 → LM2（成功但速度太快） |
| `scripts/test_3051_speed.py` | 3051 三种速度参数测试（speed/max_speed/velocity） |

## ⛔ 30 秒同步铁律（2026-09-14 教训）

**AGV 测试中容易犯的错**：
- 连续发多个测试任务没问 Leo 能不能发 → AGV 在乱跑
- **AGV 现在就在动！先发 cancel 命令！** — 我之前 13:29 连续发了 3 个 LM1→LM2 测试任务，AGV 实际停在 LM2，结果是"从 LM2 跑到 LM1 再跑回 LM2 跑三遍"

**正确做法**：
- 改 AGV 状态前**必须先问 Leo**（特别是连发测试）
- 测试单个参数前**只发 1 个命令**，等 Leo 反馈后再发下一个
- AGV 异常时**立即通知 Leo**，不要默默继续发命令
- Leo 已经下电 → **安全第一，下次上电再实测**

## 推荐安全速度（Leo 2026-09-13:33 拍板）

- **0.1 m/s** ≈ 老人慢走速度
- 室内 AGV 强烈推荐
- 5 米距离约 50 秒（可能嫌慢，先跑 0.1 m/s 确认生效再调高）

## 文档路径（永久）

- **D:\灵龙\仙工Robokit AGV底盘扫图调测手册.doc** ← **核心文档，含 6100/6101/1025/3066 完整字段**
- `D:\灵龙\仙工AGV底盘TCP协议.docx` ← API 列表（不含字段定义）
- `scripts/slam_doc.txt` / `scripts/slam_doc2.txt` ← 文档已提取为 txt

## 完整代码示例

```python
import socket, struct, json

def send_agv(ip, port, msg_type, json_data, number=1, timeout=5):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect((ip, port))
    body = json.dumps(json_data).encode('utf-8')
    header = struct.pack('>BBHIH', 0x5A, 0x01, number, len(body), msg_type) + b'\x00' * 6
    packet = header + body
    sock.sendall(packet)
    resp = b''
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk: break
            resp += chunk
            if len(resp) >= 16:
                r_len = struct.unpack('>I', resp[4:8])[0]
                if len(resp) >= 16 + r_len: break
    except socket.timeout: pass
    sock.close()
    if len(resp) >= 16:
        r_len = struct.unpack('>I', resp[4:8])[0]
        if r_len > 0 and len(resp) >= 16 + r_len:
            body_resp = resp[16:16+r_len]
            try: return json.loads(body_resp)
            except: return {'raw': body_resp.decode('utf-8', errors='replace')}
    return {}

# 1. 开始扫图
send_agv('192.168.1.204', 19210, 6100, {
    'slam_type': 2, 'real_time': True,
    'screen_width': 1280, 'screen_height': 720
})
# (Leo 手操遥控 AGV 转一圈)

# 2. 停止扫图
send_agv('192.168.1.204', 19210, 6101, {})

# 3. 轮询 1025 拿 resultmap
for i in range(60):
    r = send_agv('192.168.1.204', 19204, 1025, {'return_resultmap': True}, number=i+1)
    if r.get('resultmap'):
        with open('office1.smap.txt', 'w') as f:
            f.write(r['resultmap'])
        break
    time.sleep(2)

# 4. 上传并加载地图
with open('office1.smap.txt') as f:
    rm = f.read()
# rm 是 base64 字符串，需要先 base64-decode 成 JSON
import base64
map_json = json.loads(base64.b64decode(rm))
send_agv('192.168.1.204', 19205, 2025, map_json)
```

---

_最后更新：2026-09-14 13:34 - Leo 第二天自主导航成功 + 总结所有坑（激光 block / 标准朝向 / 调速 / 30 秒同步教训）_


---

## ⭐ 2026-09-15 多站点导航 + 朝向对齐（Leo 第二阶段自主导航）

### 1301 - 获取所有站点（站名 + 坐标 + 朝向）

**重要发现**：LM station 列表不在 .smap 地图文件里，**单独存在 AGV 运行时配置**。

```
命令: 1301 (0x0515) 端口: 19204 (state)
JSON: {}

响应:
{
  "stations": [
    {"id": "LM5", "x": 3.241, "y": -1.498, "r": 0.0, "spin": false, "type": "LocationMark"},
    ...
  ]
}
```

| 字段 | 含义 |
|------|------|
| id | 站名 |
| x, y | 站点坐标（米） |
| r | **标准朝向**（弧度）= path 终点期望朝向 |
| spin | 是否允许原地旋转 |
| type | "LocationMark"（一般都用这个）|

### 多段路径自动衔接（2026-09-15 验证）

**重大发现**：AGV 的路径规划层（MoveFactory）会自动找 **多段最短路径**，**不需要手动分段**。

测试案例：LM5 → LM3（无直接路径），AGV 自动规划：**LM5 → LM4 → LM1 → LM3**

```
命令: 3051 (source_id="LM5", id="LM3")
返回: ret_code=0
task_status: 2 (运行)
unfinished_path: ["LM4", "LM1", "LM3"]  ← 自动规划的多段路径！
```

**任务执行机制**：
1. AGV 走完一段 → 自动更新 finished_path
2. 自动追踪下一段
3. 全部完成 → task_status=4

### 1303 - 路径规划查询（需要 target_id 字段）

```
命令: 1303 (0x0517) 端口: 19204 (state)
JSON: {"source_id": "LM5", "target_id": "LM3"}  ← 注意是 target_id 不是 id
返回: 路径规划详情
```

### 朝向对齐（解决到达后没完全重合问题）

**根本原因**：AGV 到达后的朝向 = **路径规划终点朝向**（target_angle），**可能不等于站点 marker 的标准朝向（r 字段）**。

**实测数据**（LM5 测试）：
| 项目 | 朝向 |
|------|------|
| LM5.r（标准朝向）| **0.0°**（朝右）|
| AGV 实际到达朝向 | -80.13°（朝下）|
| 差距 | **80.13°** |

**Leo 的处理流程**：

1. 读取 LM5 标准朝向（1301）→ stations[LM5].r
2. 读取 AGV 当前朝向（1004）→ pos.angle
3. 计算旋转差：
   ```python
   import math
   diff = lm5_angle - agv_angle
   diff_norm = math.atan2(math.sin(diff), math.cos(diff))
   # diff_norm = 1.3986 弧度 = 80.13°
   # 正值 = 顺时针转
   ```
4. 永久方案：Roboshop Pro 里把 LM5 的标准朝向改成 AGV 实际到达角度（-80.13°），下次 3051 自动对齐
5. 临时方案：发旋转命令强制对齐

### 永久教训

1. ⛔ **3051 到达后朝向 ≠ 站点标准朝向**——SKILL.md 之前只警告了出发时对齐 source_id，现在确认到达时也只对齐路径终点朝向，不对齐 target 标准朝向
2. ✅ **3051 支持自动多段路径规划**——不需要手动分段，AGV 会自己找最短路径
3. ✅ **1301 是查询所有站点的标准命令**——包括标准朝向
4. ⛔ **站点 marker 视觉朝向 ≠ API 返回的 r 字段**——视觉上 LM5 朝下但 r=0°（朝右），不要混淆
5. ⛔ **手动控制障碍物检测关闭 ≠ 路径导航障碍检测关闭**——手动关掉后 3051/3066 仍可能停障
6. 💡 **LM5 朝向要匹配路径终点朝向**——否则到达后会出现0.06°-80° 的朝向差

### 多段路径测试记录（2026-09-15）

| 路径 | 段数 | 用时 | 位置差 | 朝向差 |
|------|------|------|--------|--------|
| LM4 → LM5 | 1 | ~25秒 | <10mm | 0.06° |
| LM5 → LM4 | 1 | ~30秒 | <5mm | 0.03° |
| LM5 → LM3 | 3 (LM5→LM4→LM1→LM3) | ~2:30 | <5mm | 0.02° |
| LM3 → LM5 | 3 (LM3→LM1→LM4→LM5) | ~2:21 | <8mm | 0.01° |
| LM4 ↔ LM2（原始位置）| ❌ | 卡0.44m | - | - |

**关键**：LM5 移到 (4.099, -3.867) 后，朝向差异从 80° 缩小到 0.02°——LM5 朝向要匹配路径终点朝向。

### 朝向对齐脚本（永久）

```python
import socket, struct, json, math

AGV_IP = '192.168.1.204'

def send_agv(ip, port, msg_type, json_data, number=1, timeout=10):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect((ip, port))
    body = json.dumps(json_data).encode('utf-8')
    header = struct.pack('>BBHIH', 0x5A, 0x01, number, len(body), msg_type) + b' ' * 6
    packet = header + body
    sock.sendall(packet)
    resp = b''
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk: break
            resp += chunk
            if len(resp) >= 16:
                r_len = struct.unpack('>I', resp[4:8])[0]
                if len(resp) >= 16 + r_len: break
    except socket.timeout: pass
    sock.close()
    if len(resp) >= 16:
        r_len = struct.unpack('>I', resp[4:8])[0]
        if r_len > 0 and len(resp) >= 16 + r_len:
            body_resp = resp[16:16+r_len]
            try:
                return json.loads(body_resp)
            except Exception:
                return {'raw': body_resp.decode('utf-8', errors='replace')}
    return {}

# 1. 读取 LM 标准朝向
stations = send_agv(AGV_IP, 19204, 1301, {}).get('stations', [])
lm5 = next((s for s in stations if s['id'] == 'LM5'), None)
lm5_angle = lm5['r']

# 2. 读取 AGV 当前朝向
pos = send_agv(AGV_IP, 19204, 1004, {})
agv_angle = pos['angle']

# 3. 计算旋转差
diff = lm5_angle - agv_angle
diff_norm = math.atan2(math.sin(diff), math.cos(diff))
print(f'LM5.r = {math.degrees(lm5_angle):.2f}°')
print(f'AGV.angle = {math.degrees(agv_angle):.2f}°')
print(f'需要旋转 = {math.degrees(diff_norm):.2f}° (顺 if diff_norm > 0 else 逆时针)')
```

---

### ⭐ 地图噪声去除脚本（2026-09-15）

**场景**：Roboshop Pro 里手动编辑地图 + 脚本批量去噪。

**脚本位置**：`scripts/agv_remove_noise.py`

**原理**：
- `.smap` 文件本质是 JSON（含 `header`, `normalPosList`, `rssiPosList`）
- `normalPosList` 是29,410+ 个可通行 waypoint
- 用**空间网格索引**做连通域分析（neighbor_dist=0.15m）
- 删除小于 MIN_CLUSTER_SIZE (10) 的小簇 = 噪声

**用法**：
```bash
python scripts/agv_remove_noise.py
# 自动备份 → 去噪 → 保存到 scripts/slam_map_denoised.smap
# 输出对比图 → output/agv_map_denoise.png
```

**实测**：29,410 → 29,007 点（删除396 个噪声点，分布在121 个小簇）

---

_最后更新：2026-09-15 12:14 - Leo 多段导航 + 1301 朝向查询 + 旋转对齐流程_


---

## ⭐ 2026-09-15 障碍检测参数调优（Leo 第二阶段自主导航必读）

### MoveFactory 障碍相关参数（Leo 永久修改）

Leo 在 Roboshop Pro → 移动参数配置 → MoveFactory 插件 → 把 **碰撞距离缩短**，否则 AGV 走不过去。

**实测修改记录（2026-09-14 → 09-15）**：

| 参数 | 默认 | Leo 修改后 | 含义 |
|------|------|------------|------|
| **ObsStopDist**（碰撞预测距离 / 障碍停车距离）| 1.0 m | **0.2 m** | 检测到障碍后多少米停车 |
| **ObsExpansion**（碰撞预测机器人扩展宽度 / 障碍扩张宽度）| 0.1 m | 0.1 m | 障碍物周围扩张范围 |

**修改位置**：Roboshop Pro → 移动参数配置 → MoveFactory 插件 → 搜索 ObsStopDist / ObsExpansion

**为什么 ObsStopDist 必须从 1.0 改到 0.2**：
- 默认 1.0m = AGV 离障碍 1.0m 就停车
- LM2/LM3/LM4 周围地图有"灰色地带"（扫图没扫干净），AGV 误以为有障碍
- 如果保持 1.0m 停车距离，AGV 会**离目标点还有 0.5-1m 就 block**
- 改成 0.2m 后：AGV 走得**更接近**实际障碍才停，留出更多路径规划空间

### 永久教程

⚠️ **AGV 障碍敏感度过高的根本原因**：

> **地图噪声（未扫干净） + 底盘安全策略叠加**（仙工研发人员 2026-09-14 确认）
>
> 1. 扫图时有些区域没扫干净 → 地图里有"灰色地带"（不是真障碍但 AGV 误以为有）
> 2. 底盘行动区域受限 → 检测到前方"障碍" → 试推几次 → 还过不去 → 判定为故障 → 停车
> 3. 结果：AGV 在地图噪声区域"一顿一顿"然后停下

### 三个治本方案对比

| 方案 | 操作 | 优点 | 缺点 |
|------|------|------|------|
| 🥇 **调小 ObsStopDist**（Leo 选用）| Roboshop Pro 改参数 0.2 | 永久生效，全局生效 | 接近障碍时仍可能停 |
| 🥈 **Roboshop Pro 修补地图** | 灰色地带涂白 → 2025 上传 | 一次解决，永久不 block | Leo 手动标费时 |
| 🥉 **重新扫图（治本）** | 6100 + Leo 慢慢遥控走全 | 彻底干净 | 30-60 分钟 |

### 虚拟激光（强制覆盖障碍检测）

**Roboshop Pro** → 定位信息面板 → "**虚拟激光**"按钮（变绿 = 启用）

⚠️ **现场安全**：启用前确认真没障碍（人/物），否则 AGV 会撞

### 跟 SDK 永久铁律结合

Leo 在 SOUL.md 立的铁律"不改 SDK 源码"——调 AGV 障碍参数属于 **Roboshop Pro UI** 层（不算 SDK 源码），**永久允许**：

✅ **允许**：
- 改 MoveFactory 参数（ObsStopDist / ObsExpansion）
- 改 Roboshop Pro 地图编辑
- 改站点标准朝向（1301 + 站点属性）

❌ **不允许**：
- 改仙工 SDK 源码（.cpp/.hpp/.so）
- 改 driver_pvt ROS2 消息
- 绕过 SDK 直接发 UDP 自定义包


---

## 🗺️ 站点别名映射规则（永久 - 2026-09-17 12:02 Leo 指定）

### 物理位置 ↔ AGV 站点映射

| 物理位置（Leo 用语） | AGV 站点 ID |
|---------------------|------------|
| **门口** | **LM5** |
| **工位一** | **LM3** |

### 使用规则
1. Leo 说"门口" → 我自动理解为 `id: 'LM5'`
2. Leo 说"工位一" → 我自动理解为 `id: 'LM3'`
3. Leo 说"从门口回到工位一" → 我自动理解为 `3051 source_id: LM5, id: LM3`
4. Leo 说"去门口" → 我自动理解为 `3051 id: LM5, source_id: 当前站点`
5. 新增物理位置 → Leo 告知后立即补到这张表

### 强制流程（铁律）
1. **收到任何导航请求，先查这张表翻译**
2. **翻译后再用 3051 协议发命令**
3. **不要假设 Leo 的口语化表达就是 LM 编号**

### 3051 关键参数（永久）
- 端口：19206
- 字段：`source_id`, `id`, `task_id`
- **`task_id` 必须是字符串**（不是整数！整数会触发 52801 错误："task_id value is missing or wrong"）
- 实际成功的格式：`{"source_id": "LM3", "id": "LM5", "task_id": "walt_lm3_to_lm5_20260917"}`

### 实测路径（2026-09-17 验证）
- **LM3 → LM5**：直接到达（单段，~12秒）
- **LM5 → LM3**：多段 LM5 → LM4 → LM1 → LM3（~75秒）
- 总路径耗时与中转站点数成正比

### Leo 12:02 强化指令
> "把你的 memory 再强化一下，让你干活的时候，你要先查一下本地 SKILL"

**铁律：接到任何任务前，必须先扫一遍 `skills/<相关领域>/SKILL.md`**：
- AGV/SLAM/导航任务 → 查 `skills/xiangong-agv-slam/SKILL.md`
- 灵龙 SDK/VLA → 查 `skills/linglong-vla-SKILL.md` 或 `skills/linglong-sdk-debug-SKILL.md`
- 灵龙 disable → 查 `skills/linglong-disable-SKILL.md`
- 豆粕预测 → 查 `skills/soybean-prediction/SKILL.md`
- DGX 本地部署 → 查 `skills/linglong-dgx-local-SKILL.md`
- 灵龙底盘/麦克纳姆 → 查 `skills/linglong-chassis-ackermann-SKILL.md`

**违反此铁律 = Leo 立即抓到 → 必须马上补救 + 写 memory**

---

_最后更新：2026-09-15 12:17 - Leo 补充碰撞距离参数修改（ObsStopDist 1.0 → 0.2）_
