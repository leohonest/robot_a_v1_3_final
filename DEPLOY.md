# Robot-A Deployment Guide V1.3

> Version: V1.3 (2026-10-01)
> Changes vs V1.2: fixed 6 corrupted files (nested env.get replacements), unified
> chassis_server to v1.24, added motion clamps + working DRYRUN, removed secrets/logs/APK,
> added packaging gate (`tools/prepackage_check.py`) and unit tests.

## Topology

```
[Walt Windows 10.86.50.x] --SSH--> [DGX 10.86.51.122] --SSH/WiFi--> [Linglong Jetson 192.168.1.12]
                                                                           |--WiFi--> [AGV 192.168.1.204]
```

All hosts/credentials come from environment variables (`.env.example`).

## 1. System Dependencies

```bash
# DGX (Ubuntu 22.04)
sudo apt install python3.10 python3-pip sshpass alsa-utils
# Robot (Jetson)
sudo apt install alsa-utils
```

## 2. Python Environment

```bash
pip install -r requirements.txt        # DGX side (torch/transformers/fastapi...)
pip install webrtcvad sounddevice requests paramiko   # robot side (linglong_voice_v3.py)
```

Models (download separately, set paths in .env):
- Qwen3-ASR 0.6B -> DGX_PYTHON_PATH/model/qwen3-asr-0.6b
- Qwen3-TTS VoiceDesign -> DGX_PYTHON_PATH/model/Qwen3-TTS-12Hz-1.7B-VoiceDesign

## 3. Configure

```bash
cp .env.example .env
# Minimum required: MINIMAX_API_KEY, DGX_HOST, AGV_HOST
```

## 4. Deploy Files

```bash
# DGX
cp dgx_scripts/*.py        /data/linglong-project/scripts/

# Linglong robot (single source of truth)
cp dgx_scripts/linglong_voice_v3.py   /home/user/
```

## 5. Start Services

See README "Quick Start" for the full ordered list. Verify each health endpoint:

```bash
curl http://127.0.0.1:7780/health   # ASR
curl http://127.0.0.1:7781/health   # chassis
curl http://127.0.0.1:7793/health   # action router (GET)
curl http://127.0.0.1:7792/health   # orchestrator
curl http://127.0.0.1:9003/health   # TTS
```

## 6. Verify Voice Loop

- [ ] Say wake word -> "shoudao" confirmation audio plays
- [ ] "上使能" -> arms enable; "下使能" -> arms disable (direction matters!)
- [ ] "前进一米" -> chassis moves ~1 m then auto-stops
- [ ] "去海边玩" -> NOT matched by action router (falls to LLM)
- [ ] `ACTION_ROUTER_DRYRUN=1 python dgx_scripts/action_router.py` passes all built-in cases

## 7. Packaging Gate (before any GitHub release)

```bash
python tools/prepackage_check.py    # must exit 0
python -m pytest tests/ -v          # unit tests for the matcher
```
