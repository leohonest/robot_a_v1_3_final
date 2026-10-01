# Environment Configuration

## System Dependencies (apt)

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install python3.10 python3-pip python3-venv sshpass

# 硬件依赖 (Linglong 端)
sudo apt install python3-ros-humble python3-rclpy python3-cv-bridge \
    python3-opencv alsa-utils pulseaudio
```

## Python Dependencies

```bash
pip install -r requirements.txt
```

## Environment Variables

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
# Edit .env with your credentials
```

Required:
- `MINIMAX_API_KEY` - MiniMax M3 API key
- `DGX_PASSWORD` - DGX server SSH password
- `LANGSHOST_PASSWORD` - Linglong SSH password

Optional:
- `FEISHU_APP_ID`, `FEISHU_APP_SECRET` - Feishu application
- `IFLYTEK_API_KEY`, `IFLYTEK_API_SECRET` - iFlytek TTS

## Loading Environment

The Python code reads env vars via `os.environ.get()`. Make sure your shell loads them:

```bash
# Option 1: export manually
export MINIMAX_API_KEY=sk-cp-...
export DGX_PASSWORD=...

# Option 2: use direnv
echo "source .env" > .envrc
direnv allow .

# Option 3: use python-dotenv in your scripts
# pip install python-dotenv
# Then add to top of script:
# from dotenv import load_dotenv
# load_dotenv()
