# 灵龙-H VLA 训练项目 - Skill (v3)

**版本**: V3 (2026-08-13 重大更新: 灵龙数采主控接入 + SDK 完整提取 + OpenLoong 对照分析 + DGX WiFi 排查)
**Owner**: Leo (老板)
**技术负责**: Walt (我)
**机器人**: LINGLONG-H (linglong-h-006, 上海灵龙机器人有限公司)
**训练机**: NVIDIA DGX Spark (spark-bafa, 10.86.51.122) - **GPU 8-12 恢复, 但当前跟灵龙办公网 192.168.4.x 不通**
**目标**: 灵龙 + LinkerHand 灵巧手 → 自主识别杯子并执行"A 点抓取 → 放到 B 点 → 叠杯子"

---

## 1. 硬件清单 (V3 大更新)

| 设备 | 型号 | IP / 位置 | 备注 |
|------|------|----------|------|
| ⭐ **机器人本体(数采主控=算力域)** | **Jetson AGX Orin 64GB** | **192.168.4.236 (办公网 4-509 WiFi) / 192.168.1.12 (eth0 内部网)** | ⭐ **数采主控就是算力域**! Jetson AGX Orin 64GB (61Gi RAM) + Ubuntu 22.04.5 + ROS Humble + PREEMPT_RT 实时内核 |
| 灵龙控制机 (SDK 监听) | (跟 Jetson 同机) | 192.168.1.30 / 192.168.4.236 | **SDK UDP 3336/3333/4141 在这台机器** |
| 灵龙底盘 (TCP) | linglong chassis | 192.168.1.204 | TCP 19205 (cmd) / 19204 (state) / 19206 (nav) |
| 灵龙本地 WiFi | linglong-h-006 / qwertyuiop | **跟办公网 4-509 物理位置不同** | 5G 模块 (Quectel RM500U-CNV) 也可用 |
| 灵巧手(右) | LinkerHand O6 | 通过 CAN 转 USB | **1-DOF 模式**(开/合 + 单一力度) |
| 头部相机 | **Intel Realsense D457** | 灵龙头部 | 1280×720 30Hz JPEG (`/dev/video-rs-color-0`, `/dev/video-rs-depth-0`) |
| 左腕相机 | **森云 390C** | 灵龙左腕 | 640×480 30Hz JPEG |
| 右腕相机 | **森云 390C** | 灵龙右腕 | 640×480 30Hz JPEG |
| 数采数据目录 | (灵龙本地) | `/home/user/sampler_data/workspace/<日期>/temp/` | ROSBAG-LINGLOONG-V1 格式, 含 `__loongdata_metadata.json` |
| **DGX Spark (训练机)** | spark-bafa | **10.86.51.122** (Leo 私网, ARM64) | GB10 Grace Blackwell Superchip, 128GB 内存, **当前 DGX 找不到灵龙办公网 WiFi (物理隔离)** |

---

## 2. ⭐ 坐标系核心结论 (2026-07-28 反推验证, 不变)

**坐标系 = VR 坐标系(egocentric,以操作员为中心)**

| 属性 | 值 |
|------|-----|
| 原点 | VR 视野中心 (操作员头部,物理高度 150 cm) |
| +X | 机器人躯干中线前方 |
| +Y | 机器人左手边 |
| **+Z** | **向下为正** (关键! 非 ROS REP-103 标准) |
| 单位 | 米(位置)/ 弧度(RPY) |

### 2.1 关键坐标点 (SDK reset_to_init 默认)

| 点 | x | y | z | 物理高度 |
|---|----|----|----|---------|
| L_EE(左手末端) | +0.30 | +0.25 | +0.65 | 85 cm |
| R_EE(右手末端) | +0.30 | -0.25 | +0.65 | 85 cm |
| 左腕(肩关节中心) | 0 | +0.35 | 0 | 150 cm |
| 右腕(肩关节中心) | 0 | -0.35 | 0 | 150 cm |
| 腰部 EE 期望 | 0 | 0 | +0.80 | 70 cm |

### 2.2 物理参数 ↔ 坐标系换算

```python
def physical_to_coord(phys_height_cm):
    z = (150 - phys_height_cm) / 100
    return z
```

### 2.3 ⭐ V3 新增: 坐标系一致是因为 "VR 标定流程"

Leo 2026-07-28 解释:
- 每次采集前按 VR 手柄**扳机键 2 秒**触发重新标定
- 数采窗口重新对齐到 VR 视野中心
- 同一人 + 每次标定 → 17 个 episode home 完全一致

---

## 3. 数据采集 SOP (v3.4)

### 3.1 数采员姿态
```python
凳子高度(cm) ≈ 150 - 身高 × 0.45
# 数采员坐下后眼睛高度必须在 148-152 cm 之间
```

### 3.2 标定(每次采集前必做)
1. 数采员戴 VR 头显,正视前方
2. 红贴纸贴**机器人头部正中** (~150 cm)
3. **扳机键 2 秒** 听到提示音后松
4. 验证: 头部标记偏离 VR 视野中心 <3 cm

### 3.3 数据完整性检查 (每个 .tar 上传前必须验证!)
```bash
tar -tf episode.tar 2>&1 | tail -5
# 末尾应是两个连续的 512B 零块 (空行)
# 如果报 "Truncated input file (needed X bytes, only Y available)" → 截断包, 丢弃
```

### 3.4 ⭐ V3 新增: 数采启动流程 (产品手册 4.3)

1. 长按**底盘电源开关 3-5 秒**, 听到"开机"提示
2. 等待底盘自检 (灯光从白→绿)
3. 按下机器人**背部电源开关** (启动运控+算力)
4. 按下机器人**背部使能开关** (关节模组上电)
5. VR 头显连灵龙 WiFi (`linglong-h-006`), 打开 `RPC_unity` APP
6. 选择 `Local` 模式 → 输入 IP `192.168.1.12` (默认) → connect

### 3.5 数采界面 (产品手册 5.4)
- Web 界面: `http://dev-local.openloong.org.cn/` 或 `http://192.168.4.236:10799/`
- 倒计时: 1~3 秒
- 实时帧率: 30 fps (head/left_hand/right_hand)
- 数据统计: 时长 / 大小 / 总帧数 / 帧率
- 最低帧率监控: "当前最低帧率关节: driver_pvt", "当前最低帧率相机: left_hand"

---

## 4. ⭐⭐⭐ 灵龙 SDK 完整提取 (V3 核心新增)

### 4.1 SDK 路径 (V3 已确认)

```
/home/user/sdk/                            ← 灵龙数采主控
├── linglong_h_sdk/                        ← Python SDK (源码)
│   ├── sdk_base.py                         ← ⭐ maniSdkSensDataClass + maniSdkCtrlDataClass + RobotModeMessage
│   ├── sdk_extend.py                       ← ⭐ LinglongHSdkClass (主类) + 50Hz 插值循环
│   ├── sdk_trajectory.py                   ← 轨迹任务加载/回放
│   └── config/                              ← 任务配置 (eY4/joint/wide demo)
├── linglong_h_sdk_cpp/                    ← C++ SDK (源码)
│   ├── include/linglong_h_sdk_cpp/
│   └── build/CMakeFiles/...               ← 已编译
└── cpp_lib/                                ← 编译产物 liblinglong_h_sdk_cpp.so + include
```

### 4.2 ⭐⭐⭐ 两个私有消息格式 (ctrl + sens)

#### `maniSdkSensDataClass` — 状态包 (机器人 → SDK, UDP 3333)
```
FMT = "< 116f i"     ← 小端, 116 floats + 1 int
SIZE = 468 bytes
```

| # | 字段 | 维度 | 含义 |
|---|---|---|---|
| 1 | `q_exp` | 2×7 | 双臂期望关节角 |
| 2 | `q` | 2×7 | 双臂实际关节角 |
| 3 | `dq` | 2×7 | 双臂关节角速度 |
| 4 | `epos_h` | 2×7 | 双臂末端实际位姿 (xyz,rpy,+1保留) |
| 5 | `epos_exp` | 2×7 | 双臂末端期望位姿 |
| 6 | `epos_waist` | 7 | 腰部末端实际位姿 |
| 7 | `epos_exp_waist` | 7 | 腰部末端期望位姿 |
| 8 | `tau` | 2×7 | 双臂关节力矩 |
| 9 | `cap_rate_exp` | 2 | 左右夹爪期望开合 |
| 10 | `cap_rate` | 2 | 左右夹爪实际开合 |
| 11 | `waist` | 4 | 腰部4关节实际角 |
| 12 | `waist_exp` | 4 | 腰部4关节期望角 |
| 13 | `head` | 2 | 头部2关节实际角 |
| 14 | `head_exp` | 2 | 头部2关节期望角 |
| 15 | `base_vel` | 2 | 底盘实际速度 [vx, w] |
| 16 | `save_data` | int | 保留 |

校验: `14+14+14+14+14 +7+7 + 14 + 2+2 + 4+4 + 2+2 + 2 = 116 ✓`

#### `maniSdkCtrlDataClass` — 控制指令 (SDK → 机器人, UDP 3336)
```
FMT_NO_CRC = "c 3f 3f 7f f3f 3f 7f f 3f 3f 4f 3f 2f f f f f"
FMT_WITH_CRC = FMT_NO_CRC + "H"  ← 末尾 2 字节 CRC16
SIZE = 191 bytes
```

| # | 字段 | 维度 | 含义 |
|---|---|---|---|
| 1 | `mode` | 1 byte | **0=关节模式 / 1=末端模式** |
| 2-4 | `arm_pos_exp_l` | 3 | 左臂末端位置 [x,y,z] |
| 5-7 | `arm_att_exp_l` | 3 | 左臂末端姿态 [r,p,y] |
| 8-14 | `arm_q_exp_l` | 7 | 左臂7关节角 |
| 15 | `cap_l` | 1 | 左夹爪开合 |
| 16-18 | `arm_pos_exp_r` | 3 | 右臂末端位置 |
| 19-21 | `arm_att_exp_r` | 3 | 右臂末端姿态 |
| 22-28 | `arm_q_exp_r` | 7 | 右臂7关节角 |
| 29 | `cap_r` | 1 | 右夹爪开合 |
| 30-32 | `waist_pos_exp` | 3 | 腰部末端位置 |
| 33-35 | `waist_att_exp` | 3 | 腰部末端姿态 |
| 36-39 | `waist_q_exp` | 4 | 腰部4关节角 |
| 40-42 | `head_att_exp` | 3 | 头部姿态 |
| 43-44 | `head_q_exp` | 2 | 头部2关节角 |
| 45 | `car_translation_exp` | 1 | 底盘期望线速度 |
| 46 | `car_rotation_exp` | 1 | 底盘期望角速度 |
| 47-48 | `car_*_status` | 2 | 底盘实际 (回填) |
| 49 | **CRC16** | uint16 | 校验 |

#### `RobotModeMessage` — 模式切换 (UDP 4141)
```
FMT = "< 4i"  SIZE = 16 bytes
```
- `enable` / `disable` / `retract_mode` / `inference_teleop_mode`

### 4.3 ⭐ SDK 核心 API (LinglongHSdkClass)

```python
from linglong_h_sdk import LinglongHSdkClass

sdk = LinglongHSdkClass(
    robot_ip="192.168.4.236",  # 或 192.168.1.30
    auto_state_thread=True,     # 后台线程收 sens (50Hz)
)

# 模式切换
sdk.robot_enable_up()             # 上使能
sdk.robot_autonomous_mode()       # 进入自主模式
sdk.robot_operation_mode()        # 切回遥操模式
sdk.robot_enable_down()           # 下使能

# 轨迹回放 (SDK 内置 50Hz 插值循环, 不用自己起 timer)
sdk.wait_for_first_state_udp()
sdk.load_trajectory_task_from_config_directory(config_root, task_name)
sdk.trajectory_playback_task(sdk, bundle, ...)
sdk.close()

# 单点插值 (VLA 部署用)
sdk.send_eef_interpolation(
    target_eef_l, target_eef_r,
    send_time,                    # 到达目标的时间
    target_waist_pos,
    target_cap_l, target_cap_r,
    interp_start="kStatus",       # 从 sens 起步
    time_law="kEaseInOut",        # smoothstep 加速减速
)
sdk.send_joint_interpolation(...)
```

### 4.4 ⭐ VLA 部署链路 (V3 确定)

```
DGX best.pt
 ↓ ONNX + TensorRT
model.engine
 ↓ 部署到灵龙 Jetson AGX Orin (算力域=数采主控)
LinglongHSdkClass (mode=1 末端模式 或 mode=0 关节模式)
 ↓ SDK 内置 50Hz 插值循环
 UDP 3336 → 运动控制器 → 23 DoF 电机
```

**VLA 输出是 70 维 joint → mode=0 直接填 `arm_q_exp_l/r`** (无需 IK)
或 mode=1 需要 IK (灵龙 SDK 没自带 IK 模块!)

---

## 5. ⭐⭐⭐ OpenLoong 对照分析 (V3 新增)

仓库: https://github.com/loongOpen/OpenLoong-Dyn-Control
已下载到 `workspace/reference/openloong/OpenLoong-Dyn-Control-main/` (113 MB)

### 5.1 关节对照: 青龙 31 DoF vs 灵龙 23 DoF

| 部位 | 青龙 (开源) | 灵龙h (私有) |
|---|---|---|
| 双臂 | 14 (J_arm_l/r_01..07) | 14 ✓ |
| 头部 | 2 (J_head_yaw + J_head_pitch) | 2 ✓ |
| 腰部 | 3 (J_waist_pitch/roll/yaw) | **4** (+1 维升降) |
| **双腿** | **12** (hip×3 + knee + ankle×2 × 2) | **0** (轮式替换!) |
| 底盘 | 无 | **3** (vx + wz + 1) |
| **总计** | **31** | **23** |

→ 灵龙 = 青龙 - 12腿 + 1 轮 + 1 腰升 + 1 底盘 = **23 DoF** ✓

### 5.2 ⭐ WBC 任务优先级架构 (HQP)

**行走 WBC 任务顺序** (HQP + 零空间投影):
```
1. static_Contact   ← 接触力约束(最优先)
2. PosRot           ← 机身位姿
3. SwingLeg         ← 摆动腿轨迹
4. RedundantJoints  ← 冗余关节自然运动
5. HandTrackJoints  ← 手臂跟踪(最低优先)
```

**关键意义**: VLA 部署时只需要发末端目标,灵龙 WBC 自动处理平衡优先级,**不用我们自己写 WBC**

### 5.3 ⭐ PVT 控制参数 (直接对照灵龙 driver_pvt)

OpenLoong `joint_ctrl_config.json` 字段:
```json
{
  "J_arm_l_01": {
    "PVT_LPF_Fc": 20,      ← 力矩输出低通频率 (Hz)
    "kd": 40.0,             ← 阻尼
    "kp": 800.0,            ← 刚度
    "maxPos": 2.96,         ← 关节限位 (rad)
    "maxSpeed": 3.14,       ← 最大速度 (rad/s)
    "maxTorque": 80.0,      ← 最大力矩 (Nm)
    "minPos": -2.96,
    "gear": 1
  }
}
```

→ 推测灵龙 `driver_pvt` 每关节的 5-6 列就是: **pos, vel, kp, kd, tau** (+1 reserved)
→ 灵龙的 PVT 控制架构应该跟 OpenLoong 同源 (来源: 同一家公司)

---

## 6. 数据状态

| 批次 | 数量 | 大小 | 状态 |
|------|------|------|------|
| 老 17 个 | 17 | ~13.7 GB | ✅ 已分析 |
| `_11.tar` | 1 | 1084 MB | ✅ 完整, 可用 |
| 新 _7 / _9 / _10 | 3 | 85 + 130 + 52 MB | ❌ 截断 (3.8-10.5%), 需重采 |
| **灵龙本地数采目录** | (新发现) | `/home/user/sampler_data/workspace/<日期>/temp/` | ROSBAG-LINGLOONG-V1, 含 `__loongdata_metadata.json` |

**8-11 真训练结果** (5 epoch, batch_size=4, lr=1e-4):
- Train: 4944 / 4 = 1236 steps × 5 = 6180 步
- 耗时: ~110s/epoch × 5 = **9.2 分钟** 总训练时间
- Loss: train=0.3437→0.3143, val=0.3594→0.3526 (收敛)
- best.pt: `/data/linglong-project/models/act_pick/checkpoints/best.pt` (619 MB)

---

## 7. DGX Spark 部署 (更新中)

### 7.1 ⭐ V3 新发现: DGX 是 ARM64 (aarch64)

```bash
$ uname -a
Linux spark-bafa 6.14.0-1015-nvidia #15-Ubuntu SMP PREEMPT_DYNAMIC ... aarch64 aarch64 aarch64 GNU/Linux

$ hostnamectl
Hardware Model: NVIDIA_DGX_Spark  ← 官方 DGX Spark, ARM64 Grace Blackwell Superchip
```

### 7.2 SSH 凭证
- 账号: `kk` (UID 1000, 在 sudo 组, password = `${DGX_PASSWORD}` 双 ii)
- IP: 10.86.51.122, Hostname: spark-bafa
- OS: Ubuntu 24.04.3 LTS aarch64 / Kernel 6.14.0-1015-nvidia

### 7.3 ⭐ V3 新发现: DGX WiFi 卡问题 (8-13 排查)

```
wlan0: MEDIATEK Corp. Device 7925 (PCI 0009:01:00.0)
状态: UP, DORMANT (休眠)
当前 channel: 44 (5220 MHz, 5GHz, 160 MHz 宽)
txpower: 3.00 dBm     ← 极低(正常 15-20)
country: 00 (UNSET) ← 未设国家
```

**DGX 找不到灵龙 WiFi 的根本原因**:
- DGX 在 Leo 私网 10.86.x.x, 灵龙在办公网 192.168.4.x
- **物理位置不同**(不同 WiFi 覆盖范围)
- 即使在同一位置, DGX 5GHz 跟灵龙 2.4GHz 也需要双频适配器

### 7.4 已部署环境 (`/data/linglong-project/`)
- venv at `/data/linglong-project/envs/vla/` (Python 3.12.3)
- torch 2.11.0+cu130, LeRobot 0.6.0, av 15.1.0 (从 18.0.0 降级)
- 完整 VLA pipeline 7 步已完成 5/7

### 7.5 GPU 状态 (V3 更新)
- **8-12 Leo 反馈**: "DGX 被他们拿去其他地方用了, 今天已恢复" → **GPU 8-12 恢复 ✓**

---

## 8. SSH 工具链 (V3 新增 - 跨平台踩坑教训)

### 8.1 ⭐ 跨 Windows-Linux SSH (终极方案)

**WSL Ubuntu + sshpass** + 密码文件 = 唯一稳定方案

```python
# scripts/linglong_ssh.py 核心
inner = f"sshpass -f {WSL_PWD_FILE} ssh -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=NUL {HOST} '{cmd}'"
subprocess.run(['wsl', 'bash', '-c', inner], ...)
```

### 8.2 ⭐ SSH 工具踩坑教训 (必读)

| 工具 | Windows 好用? | 失败原因 |
|------|---------------|----------|
| **WSL Ubuntu + sshpass** | ✅ **唯一稳定** | 全 KEX 支持, 无 hostkey 问题 |
| plink -ssh -batch -pw PWD | ✅ (但需已知 hostkey) | plink 不能自动接受新 hostkey |
| pywinpty | ❌ 卡 Leo prompt | Windows 密码交互不可靠 |
| OpenSSH `-tt` + stdin pipe | ❌ 卡 | Windows OpenSSH 不读 stdin 密码 |
| paramiko | ❌ DLL issue | crypto lib Python 3.9 不兼容 |
| Windows OpenSSH `ssh` | ❌ | **不支持 sntrup761x25519 KEX** (Ubuntu 22.04 默认) |

### 8.3 ⭐ Python 跨平台踩坑 (必读)

1. **WSL 装 sshpass**: `wsl sudo apt-get install -y sshpass`
2. **密码文件**: `C:\Users\DFET\AppData\Local\Temp\linglong_pwd.txt` (但 WSL 重启会丢)
3. **Windows console GBK**: `sys.stdout.reconfigure(encoding='utf-8')`
4. **PowerShell `&&` 不工作**: 用 `;` 或分两条命令
5. **PowerShell 不支持 `<()` process substitution**: 用临时文件
6. **Python docstring `\U` 转义**: 用 `r"""..."""`
7. **Out-File UTF8**: `2>&1 | Out-File -Encoding utf8`

---

## 9. 数据采集完整性 + DGX/灵龙 网络架构 (V3 新增)

### 9.1 完整性自检 (Leo 7-31 教训)
- 每个 .tar 上传前: `tar -tf episode.tar | tail -5` 看末尾零块
- 截断常见原因: ros2 bag OOM/SIGKILL / 打包没 fsync / 磁盘满

### 9.2 ⭐ 灵龙网络模式 (V3 新增 - 核心约束)

| 模式 | 灵龙网络 | 我能 SSH | DGX 能连 | 遥控手柄 |
|---|---|---|---|---|
| **A: 办公网模式** | wlan0 接 4-509 | ✅ | ✅ | ❌ **不能用** |
| **B: 本地 WiFi 模式** | linglong-h-006 热点 | ❌ | ❌ | ✅ |
| **C: 双开** ⭐ | wlan0 接办公 + 灵龙热点 | ✅ | ✅ | ✅ 理论上 |

**今天 Leo 说**: 数采时灵龙必须脱离办公网(否则手柄不能用)
→ 研发/部署走办公网 (模式 A), 数采走本地 WiFi (模式 B)

### 9.3 ⭐ DGX 接灵龙 WiFi 方案 (V3 待解)

| 方案 | 适用 | 成本 |
|---|---|---|
| A. **办公网中继** | DGX 接办公网, 反向连灵龙 | 0 (需 DGX 接办公网) |
| B. USB WiFi 适配器 | DGX 离灵龙近 | ¥50 |
| C. 网线直连 | DGX 跟灵龙物理可达 | 布线 |
| D. Walt 当网桥 | 我双网卡连两边 | 网络配置复杂 |

**Leo 决策**: 明天先确认 DGX 跟灵龙的**物理位置**, 再选方案

---

## 10. ⭐ 部署路线图 (V3 修正)

### 阶段 1: 纯软件,零风险 (明早开干)
1. ✅ **拉 SDK 到本地分析** (`workspace/sdk_local/`)
2. ✅ **拉一段数采数据** 验证 ROSBAG-LINGLOONG-V1 格式
3. ✅ **修诊断脚本** 的 grep 转义问题

### 阶段 2: 数采员开机后
4. 启动数采 Ubuntu 桌面 + 数采服务
5. 重跑 ROS 诊断 (验证 /driver_pvt 等 topic)

### 阶段 3: SDK 联通
6. LinglongHSdkClass DRY_RUN 测试
7. 真机发小动作 (Leo 手按急停)
8. 录1 段数据 + 解析验证

### 阶段 4: VLA 部署 (DGX)
9. best.pt → ONNX + TensorRT
10. 写 `inference_node_deploy.py` (订阅 ROS topic → SDK send)

### 阶段 5: 真机试运行
11. 影子模式 (模型输出只看不执行)
12. 低速 (10% speed limit, E-stop ready)
13. 正常速度 ≥ 20 次成功率统计

---

## 11. 关键里程碑历史

| 日期 | 事件 |
|------|------|
| 2026-07-25 | Episode 1 解析完成 |
| 2026-07-27 | 坐标系测试 V3.0 SOP |
| 2026-07-28 | 17 ep 验证 + VR 坐标系反推 + 数采员 SOP v3.4 |
| 2026-07-30 | 新 6 episode + DGX 部署 + venv + LeRobot 0.6.0 |
| 2026-07-31 | 训练骨架完成 (CPU mode) + dgx_ssh.py v3 + GPU 卡死调查 |
| 2026-08-12 | GPU 恢复 (Leo 反馈) |
| **2026-08-13** | ⭐ **灵龙数采主控接入办公网 + SDK 完整提取 + OpenLoong 对照 + DGX WiFi 排查** |

---

## 12. 当前活跃任务 (2026-08-13 19:35)

### 已完成 ✅
- ✅ 灵龙产品手册分析 (4 控制器架构)
- ✅ 灵龙 SDK 两个私有消息格式完整提取 (ctrl + sens)
- ✅ OpenLoong-Dyn-Control 仓库下载 + WBC 任务优先级 + PVT 控制分析
- ✅ 灵龙数采主控 SSH 接入 + 综合诊断 (Jetson AGX Orin 64GB 确认)
- ✅ DGX 网络诊断 (ARM64 + WiFi 卡问题根因)
- ✅ 新脚本: `linglong_ssh.py` (WSL+sshpass) + `check_linglong_robot.py`

### 临时文件状态 ⏸️
- ⚠️ `C:\Users\DFET\AppData\Local\Temp\linglong_pwd.txt` 含明文密码 (`user/admin`)
- 灵龙关机但密码不变 → **保留等明天用**

### 待办 ⏸️ (明早)
- Leo 决定 DGX 物理位置 → 选 DGX 接灵龙 WiFi 方案
- 拉 SDK + 拉一段数据 → 验证格式
- 部署 VLA 推理节点
- 真机试运行

---

## 13. 文件路径索引 (V3 更新)

| 资源 | 路径 |
|------|------|
| 项目根(本地) | `C:\Users\DFET\.openclaw\workspace\linglong\` |
| 项目根(DGX) | `/data/linglong-project/` |
| **SSH 工具(V3 新增)** | `scripts/linglong_ssh.py` ⭐ (WSL+sshpass) |
| **综合诊断入口(V3 新增)** | `scripts/check_linglong_robot.py` ⭐ |
| DGX SSH (老) | `scripts/dgx_ssh.py` (v3) |
| **灵龙主控配置 JSON (新)** | `memory/2026-08-13-linglong-host-config.json` |
| **灵龙规格(V3)** | `memory/2026-08-13-linglong-specs.md` |
| **OpenLoong 对照(V3)** | `memory/2026-08-13-openloong-vs-linglong.md` |
| **今日工作日志(V3)** | `memory/2026-08-13.md` |
| OpenLoong 仓库 | `workspace/reference/openloong/OpenLoong-Dyn-Control-main/` |
| 训练骨架总结 | `linglong\README_V1_骨架完成_2026-07-31.md` |
| SOP v3.4 | `linglong\灵龙数据采集操作规范_v3.4_坐标系更新版.docx` |

---

**文档结束** | 版本 V3.0 | 2026-08-13

---

## 12. APP 快捷键口语映射 VLA（Leo 2026-09-22 设计）

### 12.1 设计目标

**把手机 APP 上的所有快捷按钮，都用自然口语命令来调用，让机器人听懂自然语言操控**

```
原本：APP 屏幕点按钮 -> 走快捷键 -> 触发动作
现在：用户说灵龙，上使能 -> ASR -> action_router -> 触发同样动作
```

### 12.2 当前已实现 vs 待实现

| 手机 APP 按钮 | 口语命令 | 当前状态 | 待做 |
|------------|---------|---------|------|
| 上使能 | 上使能 / 启动 | OK done | — |
| 下使能 | 下使能 / 关闭 | OK done | — |
| 归位 | 归位 / 复位 | OK done | — |
| 松左手 | 松开左手 | OK done | — |
| 握左手 | 握左手 | OK done | — |
| 松右手 | 松开右手 | OK done | — |
| 握右手 | 握右手 | OK done | — |
| 前进 0.3m | 前进 30 公分 | OK done | — |
| 后退 0.2m | 后退 20 公分 | OK done | — |
| 左转 45度 | 左转 45 度 | OK done | — |
| 右转 45度 | 右转 45 度 | OK done | — |
| 停 | 停 / 刹车 | OK done | — |
| 去门口 | 去门口 | OK done (3051 nav) | — |

### 12.3 实现位置

- **口语命令源**：scripts/action_router.py ACTION_DICT 字典
- **底层实现**：复用 voice_cmd_server.py :7778 + chassis_server.py :7781（不改服务端）
- **AGV 站点导航**：直接 TCP :19206 发 3051 nav JSON

### 12.4 矢量数据（Leo 9/22 17:00 要求）

**距离 0.3 米**（默认前进距离）：

```python
# scripts/action_router.py
LINEAR_VEL = 0.1   # m/s
DEFAULT_DISTANCE = 0.3  # 米（默认前进后退）
# 例：前进 0.3m -> duration = 0.3 / 0.1 = 3 秒
```

**角度 45 度**（默认转弯角度）：

```python
ANGULAR_VEL = 0.1  # rad/s ~ 5.73度/s
DEFAULT_ANGLE = 45.0  # 度（默认转弯）
# 例：左转 45度 -> duration = 45 / 5.73 ~ 7.85 秒
```

**Leo 9/22 17:00 拍板**：默认距离 0.3m、默认角度 45度（不只是 15度）。

---

### 12.5 VLA 动作映射表（完整版）

**完整表在**：`scripts/vla_action_mapping.md`（VLA 训练用，本地维护）

**当前已实现 9 个 VLA 操作**（Leo 9/22 17:00 拍板）：

| 类别 | 动作 | 口语命令 |
|------|------|---------|
| 上肢 | enable | 上使能 / 启动 |
| 上肢 | disable | 下使能 / 关闭 |
| 上肢 | home | 归位 / 复位 |
| 上肢 | grip_L_close | 握左手 |
| 上肢 | grip_L_open | 松左手 |
| 上肢 | grip_R_close | 握右手 |
| 上肢 | grip_R_open | 松右手 |
| 底盘 | chassis_fwd | 前进 0.3米（默认）/ 前进 N米 |
| 底盘 | chassis_back | 后退 0.3米 / 后退 N米 |
| 底盘 | chassis_left | 左转 45度（默认）/ 左转 N度 |
| 底盘 | chassis_right | 右转 45度 / 右转 N度 |
| 底盘 | chassis_stop | 停 / 刹车 |
| AGV | nav_to_station | 去门口 / 回工位1 |
| AGV | nav_station_to_station | 从工位1去门口 |

**矢量默认值**（Leo 9/22 17:00 拍板）：

- 距离：DEFAULT_DISTANCE_M = 0.3 米
- 角度：DEFAULT_ANGLE_DEG = 45.0 度


_更新_2026-09-22 17:25_
