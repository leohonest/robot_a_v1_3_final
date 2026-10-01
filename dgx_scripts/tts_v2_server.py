#!/usr/bin/env python3
"""
Qwen3-TTS VoiceDesign Server (普通话)
====================================
端口: 9003 (避开 9002 粤语 server)

功能：
- 用 Qwen3-TTS VoiceDesign 模型生成普通话声音
- 不需要参考音频（vs voice_clone 模式）
- API 兼容 9002 接口 (auto_cantonese 强制 False)

接口：
  POST /tts_base64
    body: {"text": "...", "language": "Chinese"}
    response: {"audio_base64": "...", "audio_b64": "..."}
  POST /tts
    body: {"text": "...", "language": "Chinese"}
    response: 直接返回 wav
"""
import os
import sys
import json
import time
import hashlib
import base64
import logging
import argparse
from typing import Optional

import torch
import io
import soundfile as sf
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger('tts_v2')

# ================= 配置 =================

# VoiceDesign 模型路径
_DGX_ROOT = os.environ.get("DGX_PYTHON_PATH", "/opt/robot-a")
TTS_MODEL_PATH = os.getenv(
    "QWEN_TTS_VD_MODEL_PATH",
    os.path.join(_DGX_ROOT, "model", "Qwen3-TTS-12Hz-1.7B-VoiceDesign"),
)

# 普通话声音描述 (instruct prompt)
DEFAULT_INSTRUCT = os.getenv(
    "QWEN_TTS_INSTRUCT",
    "成熟稳重男声，磁性浑厚，标准普通话，商务风格"  # 默认成熟男声普通话
)

# 缓存目录
CACHE_DIR = os.getenv("QWEN_TTS_CACHE_DIR", os.path.join(_DGX_ROOT, "audio", "result", "voice_design"))
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(f"{CACHE_DIR}/cache", exist_ok=True)

# 端口
DEFAULT_PORT = 9003

# ================= 加载模型 =================

logger.info(f"加载 Qwen3-TTS VoiceDesign: {TTS_MODEL_PATH}")
from qwen_tts import Qwen3TTSModel

tts_model = Qwen3TTSModel.from_pretrained(
    TTS_MODEL_PATH,
    device_map="cuda:0",
    dtype=torch.bfloat16,
)
logger.info("VoiceDesign 模型加载完成 ✅")

# 支持的语言
SUPPORTED_LANGUAGES = ["Chinese", "English", "Japanese", "Korean", "German", "French", "Russian", "Italian", "Spanish", "Portuguese", "Auto"]

# ================= 请求 =================

class TTSRequest(BaseModel):
    text: str
    language: str = "Chinese"
    use_cache: bool = True
    instruct: Optional[str] = None  # 可选自定义描述


# ================= 工具 =================

def normalize_text(text: str) -> str:
    """清理文本"""
    text = text.strip()
    import re
    text = re.sub(r'[\*\#\`\[\]\(\)]', '', text)
    return text.strip()


def get_cache_path(text: str, language: str, instruct: str) -> str:
    """生成缓存路径"""
    key = hashlib.md5(f"{language}|{instruct}|{text}".encode("utf-8")).hexdigest()
    return os.path.join(CACHE_DIR, "cache", f"{key}.wav")


# ================= FastAPI =================

app = FastAPI(title="Qwen3-TTS VoiceDesign (普通话)")


@app.get("/")
@app.get("/health")
async def health():
    return JSONResponse({
        "status": "ok",
        "service": "qwen3-tts-voicedesign",
        "model": TTS_MODEL_PATH,
        "default_instruct": DEFAULT_INSTRUCT,
        "supported_languages": SUPPORTED_LANGUAGES,
    })


def synthesize(text: str, language: str, instruct: str = None):
    """核心合成函数"""
    text = normalize_text(text)
    if not text:
        raise HTTPException(400, "empty text")
    
    if language not in SUPPORTED_LANGUAGES:
        language = "Chinese"  # fallback
    
    if instruct is None:
        instruct = DEFAULT_INSTRUCT
    
    # 缓存
    cache_path = get_cache_path(text, language, instruct)
    if os.path.exists(cache_path):
        logger.info(f"[CACHE HIT] {text[:30]}...")
        with open(cache_path, 'rb') as f:
            return f.read()
    
    # 合成
    logger.info(f"[TTS] '{text[:50]}' language={language}")
    t0 = time.time()
    try:
        wavs, sr = tts_model.generate_voice_design(
            text=text,
            instruct=instruct,
            language=language,
        )
        elapsed = time.time() - t0
        logger.info(f"[TTS] {len(wavs)} wavs, sr={sr}, {elapsed:.2f}s")
        
        # 第一个 wav
        wav_np = wavs[0]
        
        # 写入缓存
        sf.write(cache_path, wav_np, sr)
        
        # 返回 bytes
        buf = io.BytesIO()
        sf.write(buf, wav_np, sr, format='WAV')
        return buf.getvalue()
    
    except Exception as e:
        logger.error(f"TTS 失败: {e}")
        raise HTTPException(500, f"TTS error: {e}")


@app.post("/tts")
@app.post("/tts_base64")
def tts_base64(req: TTSRequest):  # 同步函数：GPU 推理放线程池，不阻塞事件循环
    wav_bytes = synthesize(req.text, req.language, req.instruct)
    b64 = base64.b64encode(wav_bytes).decode()
    return JSONResponse({
        "audio_base64": b64,
        "audio_b64": b64,
        "language": req.language,
    })


@app.post("/tts_raw")
def tts_raw(req: TTSRequest):
    """直接返回 wav bytes（不需要 base64）"""
    wav_bytes = synthesize(req.text, req.language, req.instruct)
    from fastapi.responses import Response
    return Response(content=wav_bytes, media_type="audio/wav")


# ================= Main =================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--instruct', type=str, default=None, help='默认声音描述 (覆盖 env)')
    args = parser.parse_args()
    
    global DEFAULT_INSTRUCT
    if args.instruct:
        DEFAULT_INSTRUCT = args.instruct
    
    logger.info("=" * 60)
    logger.info(f"Qwen3-TTS VoiceDesign Server 启动")
    logger.info(f"  Model: {TTS_MODEL_PATH}")
    logger.info(f"  Default instruct: {DEFAULT_INSTRUCT}")
    logger.info(f"  Port: {args.port}")
    logger.info(f"  Cache: {CACHE_DIR}")
    logger.info("=" * 60)
    
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == '__main__':
    main()