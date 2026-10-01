# 灵龙音频系统 SKILL (v1.0)

**版本**: V1.0 (2026-09-18 创建立框架，明天补完)
**作者**: Walt
**目的**: 记录灵龙音频系统根本架构 + USB 音响方案，避免重复踩坑

---

## ⛔ 核心真相（最重要，先看）

**灵龙的内部扬声器接在运控域（motion MCU），不接 Linux 主板！**

| 层级 | 音频能力 |
|------|---------|
| **Linux 主板**（Jetson AGX Orin）| **只接 HDMI / 3.5mm 音频口**（不是内部喇叭）|
| **运控 MCU**（EtherCAT 底层）| **接内部扬声器**，**只用于故障播报** |
| **可写音频到内部喇叭** | ❌ **不能**（只能通过运控 MCU） |

**结论**：Linux 主板的 ALSA / PulseAudio 配置（DSPK1 48k/16bit/2ch 等）**完全不影响内部扬声器**！

---

## 1. 集成商确认（2026-09-18 微信对话）

联系人："豪到了吗"（灵龙集成商）

| 事实 | 来源 |
|------|------|
| 内部音响接底层（motion MCU）| 集成商原话 |
| 用于播报机器人故障（急停/过温/低电量）| 集成商 |
| Linux 主板"不应该做开发"（指音频）| 集成商 |
| 建议外接 USB 音响 | 集成商 |
| 可以关闭内部音响 | 集成商可做 |

---

## 2. 过去错误的调查方向（9/16 已推翻）

我们之前以为：
- ❌ aplay/paplay 应该能驱动内部扬声器
- ❌ 内部扬声器被"独占 ALSA daemon 屏蔽"
- ❌ Leo 听到"我是六号机器人"是 OpenLoongBrain TTS

**实际**：
- ✅ 内部扬声器只接底层 MCU，Linux 主板根本管不到
- ✅ "我是六号机器人"是底层 MCU 故障播报系统启动音（不是 TTS）
- ✅ aplay rc=0 是因为写到 Linux 主板的 HDMI/3.5mm（不是内部喇叭）

---

## 3. 推荐方案（明天实施）

### A. 外接 USB 音响（主推 ✅）

**为什么是 USB**：
- Jetson AGX Orin 有 4 个 USB 3.0 口
- 标准 UAC 设备免驱
- 不需要拆机

**关键参数**：
- USB 供电（不要单独电源）
- UAC 1.0（标准 USB Audio Class）
- 16bit/44.1k 或 48k 兼容

**推荐型号**（带麦 + AEC，因为要做语音问答）：

| 型号 | 价格 | 特点 |
|------|------|------|
| **漫步者 HECATE G1500** | ~150 | USB 音箱 + 3.5mm 麦（推荐）|
| **腾讯听听** | ~300 | 自带 DSP 回声消除 |
| **罗技 H110 头戴** | ~50 | 耳机+麦一体（便宜但 Leo 不戴不合适）|

### B. 外接蓝牙音响（备选）

灵龙主板支持蓝牙（音频接收），但需要配对 + PulseAudio `module-bluetooth-discover`

### C. HDMI 显示器音响（不推荐）

只适合临时测试，机器人不能背着显示器

---

## 4. 灵龙本地麦克风调查（明天）

**问题**：灵龙本体是否有麦克风？

**调查命令**（Leo 现场跑）：
```bash
arecord -l               # 列出录音设备
pactl list sources short # PulseAudio 输入源
ls /dev/snd/             # 所有 ALSA 节点
```

**预期结果**：
- 如果 `**** List of CAPTURE Hardware Devices ****` 有内容 → 有麦
- 如果只有 PLAYBACK → 没麦，需要外接 USB 麦

**如果灵龙没麦** → 必须买带麦的 USB 音响（A 方案必须）

---

## 5. AEC 回声消除（语音问答必需）

如果走灵龙本地麦做语音问答：
- 灵龙播放"我在" → 麦同时听到 → **自激振荡**
- 必须 AEC

**方案**：
1. **硬件级**：选自带 DSP 回声消除的音响（腾讯听听）
2. **系统级**：PulseAudio `module-echo-cancel`
   ```bash
   # /etc/pulse/default.pa
   load-module module-echo-cancel source_name=ec_sink sink_name=ec_source
   ```

---

## 6. 永久铁律（不破解运控域）

**Leo 10:39 问"能破解运控域吗？"**

| 不可做 | 原因 |
|--------|------|
| 改 SDK 源码 | SOUL.md 红线 |
| 绕过 SDK 直接发 UDP | SOUL.md 红线 |
| 改运控 MCU 固件 | 厂商封闭 |
| 注入音频到运控 MCU | 破坏故障播报 = **安全隐患** |

**唯一合法路径**：联系集成商/灵龙厂商加 API（"豪到了吗"可协助）

---

## 7. 待写脚本（明天补完）

- [ ] `scripts/check_linglong_mic.py` — SSH 灵龙查 arecord -l
- [ ] `scripts/play_to_usb_speaker.py` — 测试 USB 音响 aplay
- [ ] `scripts/setup_aec.py` — PulseAudio echo-cancel 配置
- [ ] `scripts/find_usb_audio_devices.py` — 查灵龙 USB 控制器识别

---

## 8. 关键文件路径

- Leo 微信截图：`C:\Users\DFET\.openclaw\media\inbound\fe973038-68c4-40dc-9ec8-a975db6e76f4.jpg`
- 9/16 音频调试日志：`memory/2026-09-16.md`
- 9/18 调查日志：`memory/2026-09-18.md`
- ALSA 配置脚本：`scripts/setup_ape_audio.py`（**已过时，但保留**）
- wav 文件：`/tmp/nihao_leo.wav`（16bit mono 24000Hz）

---

## 10. 历史教训（永久）

1. **永远先查集成商/厂商**——不要凭技术推断乱猜（9/16 整个调查方向都错了）
2. **音频问题先看硬件接线**——不要直接配 ALSA
3. **Doubao 给的信息可能不准确**——需要厂商验证
4. **Linux 主板 ≠ 机器人全身**——灵龙是分布式系统
5. **运控域永远不动**——故障播报是安全相关

---

## 11. 明天行动清单

- [ ] Leo 直连灵龙 WiFi（4G 内部 WiFi，不是办公网）
- [ ] SSH 灵龙 → 跑 arecord -l / pactl list sources
- [ ] Leo 买/接 USB 音响（带麦+AEC）
- [ ] 现场测 aplay -D plughw:X,Y
- [ ] 验证 Leo 能听到声音（**最终成功标志**）
- [ ] 验证 AEC 工作（不发生自激）

---

_最后更新: 2026-09-18 11:56 - Leo 决定今天停手，明天现场搞_
_基于：Leo 微信截图（集成商对话）+ Doubao 调查结果 + 9/16 ALSA 调试教训_

---

## 12. USB 麦克风喇叭 CARD 口变化问题（2026-09-22 确认）

### 12.1 症状

- 9/22 启动 v3 listener 时，arecord -D plughw:2,0 工作正常
- 9/22 下午突然不响应
- arecord -l 显示 mic 在 card 0 / device 0（不是 2 / 0）
- 喇叭 aplay -D plughw:3,0 -> device busy 或 no sound

### 12.2 根因

**USB 设备插入顺序 / 内核重载 -> ALSA 重新分配 card number**

- USB hub 上电顺序变了 -> USB 枚举顺序变了 -> ALSA 重新编号
- 内核 hotplug 重启 -> 同上
- 拔掉 USB 麦再插回去 -> 重新枚举，可能 card 0->3 或 2->0
- **每次冷启动 v3 listener 之前，必须跑 arecord -l 验证当前 card 号**

### 12.3 解决：动态查询 + 软链接

**v3 listener 启动时动态查询**：

```python
import subprocess, re

def find_audio_device_by_name(name_substr, mode='capture'):
    # arecord -l / aplay -l 找匹配 name_substr 的设备，返回 plughw:X,0
    cmd = 'arecord' if mode == 'capture' else 'aplay'
    out = subprocess.check_output([cmd, '-l'], text=True)
    pattern = re.compile(
        r'card\s+(\d+):\s+(\S+)\s+\[.*?' + re.escape(name_substr) + r'.*?\].*?device\s+(\d+):',
        re.IGNORECASE
    )
    m = pattern.search(out)
    if m:
        return 'plughw:' + m.group(1) + ',' + m.group(3)
    return None

mic_dev = find_audio_device_by_name('B107A6', mode='capture')
spk_dev = find_audio_device_by_name('Jieli', mode='playback')
if not mic_dev or not spk_dev:
    raise RuntimeError('Audio devices not found')
```

**为什么 v3 listener 之前硬编码 plughw:2,0 / plughw:3,0**：9/22 Leo 拍板要启动前先验证，不验证了就直接 abort 防止假成功。

### 12.4 软链接方案（备选）

```bash
# /etc/udev/rules.d/99-usb-audio.rules
SUBSYSTEM=="sound", ATTRS{idVendor}=="1234", ATTRS{idProduct}=="5678", SYMLINK+="audio/mic_b107a6"
SUBSYSTEM=="sound", ATTRS{idVendor}=="abcd", ATTRS{idProduct}=="ef01", SYMLINK+="audio/spk_jieli"

# 灵龙端 /home/user/.asoundrc
pcm.mic_b107a6 { type plug slave { pcm "hw:audio/mic_b107a6" } }
pcm.spk_jieli { type plug slave { pcm "hw:audio/spk_jieli" } }

# v3 listener 配置
MIC_DEVICE = 'mic_b107a6'
SPK_DEVICE = 'spk_jieli'
```

**Leo 9/22 17:30 拍板**：先用 12.3 动态查询（30 秒搞定），**不动 udev 规则**（避免越界）。

### 12.5 故障排查清单

| 症状 | 检查 |
|------|------|
| v3 启动报 audio not found | asrecord -l / aplay -l 看 card number 变了没 |
| mic 没声音 | asrecord -D plughw:X,0 -d 1 /tmp/test.wav |
| 喇叭只响一半 | aplay -D plughw:X,0 /usr/share/sounds/alsa/Front_Center.wav |
| 设备时不时变 | USB hub power 不稳 -> 换带电源 hub |

---
_更新_2026-09-22 17:15_
