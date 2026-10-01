# 灵龙技能库索引（SKILL Index）

本目录存放机器人工程的运维/调试技能文档。每个 `*-SKILL.md` 是一个独立技能说明。

## 底盘与运动控制
- [linglong-chassis-ackermann-SKILL.md](linglong-chassis-ackermann-SKILL.md) — 底盘 Ackermann 运动协议与 TCP 命令
- [linglong-chassis-safety-SKILL.md](linglong-chassis-safety-SKILL.md) — 底盘安全机制（限速 / watchdog / 急停）
- [chassis-action-stop-timer-SKILL.md](chassis-action-stop-timer-SKILL.md) — 语音动作的定时停止机制

## 语音链路（VAD / ASR / TTS / 路由）
- [voice-chassis-architecture-SKILL.md](voice-chassis-architecture-SKILL.md) — 语音→底盘整体架构
- [voice-v3-listener-SKILL.md](voice-v3-listener-SKILL.md) — 灵龙端 VAD 监听器（linglong_voice_v3.py）
- [linglong-audio-SKILL.md](linglong-audio-SKILL.md) — 音频设备（麦克风/扬声器）配置
- [linglong-voice-qa-SKILL.md](linglong-voice-qa-SKILL.md) — 语音链路 QA 测试方法
- [linglong-action-router-SKILL.md](linglong-action-router-SKILL.md) — 关键词动作路由（action_router.py）
- [orchestrator-module-reload-SKILL.md](orchestrator-module-reload-SKILL.md) — LLM 编排器模块热加载
- [play-conf-audio-SKILL.md](play-conf-audio-SKILL.md) — "收到"确认音播放
- [iflytek-tts-SKILL.md](iflytek-tts-SKILL.md) — 讯飞 TTS 备选方案
- [windows-asr-window-SKILL.md](windows-asr-window-SKILL.md) — Windows 端 ASR 调试窗口

## 机械臂与 SDK
- [linglong-sdk-debug-SKILL.md](linglong-sdk-debug-SKILL.md) — 灵龙 SDK 调试
- [linglong-disable-SKILL.md](linglong-disable-SKILL.md) — 机器人下能/禁用流程
- [linglong-vla-SKILL.md](linglong-vla-SKILL.md) — VLA 视觉-语言-动作

## 基础设施与部署
- [linglong-dgx-ssh-SKILL.md](linglong-dgx-ssh-SKILL.md) — DGX SSH 跳板（凭证用环境变量）
- [linglong-dgx-local-SKILL.md](linglong-dgx-local-SKILL.md) — DGX 本地运维

## AGV / SLAM
- [xiangong-agv-slam/SKILL.md](xiangong-agv-slam/SKILL.md) — 仙工 AGV 扫图与地图上传（agv_slam_server.py）

---
所有主机凭证一律通过环境变量注入（见根目录 `.env.example`），禁止写入文档。
