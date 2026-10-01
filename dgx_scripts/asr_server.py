#!/usr/bin/env python3
"""asr_server.py - DGX 端语音识别服务（基于 Qwen3-ASR 0.6B）

用法（在 DGX 上）：
  os.environ.get("DGX_PYTHON_PATH", "/opt/robot-a/pyPobect")/venv/bin/python asr_server.py --port 7780

POST /asr
  multipart/form-data, field "audio" = audio file (wav/webm/m4a/opus/mp3)
  返回: {"text": "归位", "language": "Chinese", "duration": 1.23}
"""
import os
import sys
import time
import tempfile
import logging
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from email.parser import BytesParser
from email.policy import default as _email_policy
from urllib.parse import urlparse
import json
# NOTE: GitHub release - cgi replaced with email.message (Python 3.13+ compat)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger('asr_server')

# ================= 配置 =================
ASR_MODEL_PATH = os.path.join(
    os.environ.get("DGX_PYTHON_PATH", "/opt/robot-a"), "model", "qwen3-asr-0.6b")
SAMPLE_RATE = 16000  # Qwen3-ASR 期望 16kHz

# ================= 加载模型 =================
logger.info(f"加载 Qwen3-ASR 模型: {ASR_MODEL_PATH}")
import torch
from qwen_asr import Qwen3ASRModel

asr_model = Qwen3ASRModel.from_pretrained(
    ASR_MODEL_PATH,
    dtype=torch.bfloat16,
    device_map="cuda:0",
    max_inference_batch_size=8,  # 单请求场景，batch 小
    max_new_tokens=256,
)
logger.info("Qwen3-ASR 加载完成 ✅")


def parse_multipart(body_bytes: bytes, content_type: str) -> dict:
    """用标准库 email 解析 multipart/form-data（替代已废弃的 cgi.FieldStorage）。"""
    header = ("Content-Type: " + content_type + "\r\nMIME-Version: 1.0\r\n\r\n").encode("utf-8")
    msg = BytesParser(policy=_email_policy).parsebytes(header + body_bytes)
    fields = {}
    for part in (msg.iter_parts() if msg.is_multipart() else []):
        name = part.get_param("name", header="content-disposition")
        if name is None:
            continue
        fields[name] = {"filename": part.get_filename(), "data": part.get_payload(decode=True) or b""}
    return fields


def transcribe_file(audio_path: str, language: str = "Chinese") -> dict:
    """识别单个音频文件"""
    try:
        # 用 librosa 加载到 16kHz mono
        import librosa
        audio_np, sr = librosa.load(audio_path, sr=SAMPLE_RATE, mono=True)
        duration = len(audio_np) / sr
        logger.info(f"音频: {duration:.2f}s, {sr}Hz")

        # 调 Qwen3-ASR
        result = asr_model.transcribe(
            audio=(audio_np, sr),
            language=language,
            return_time_stamps=False,
        )
        if result and len(result) > 0:
            text = result[0].text.strip()
            lang = getattr(result[0], 'language', language)
            return {"text": text, "language": lang, "duration": round(duration, 2)}
        return {"text": "", "language": language, "duration": round(duration, 2)}
    except Exception as e:
        logger.error(f"识别失败: {e}")
        return {"error": str(e), "text": "", "language": "", "duration": 0}


# ================= HTTP 服务 =================
class ASRHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        logger.info('HTTP ' + (format % args))

    def do_GET(self):
        if self.path == '/' or self.path == '/health':
            body = json.dumps({
                'status': 'ok',
                'service': 'qwen3-asr',
                'model': ASR_MODEL_PATH,
                'sample_rate': SAMPLE_RATE,
            }, ensure_ascii=False)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Content-Length', str(len(body.encode('utf-8'))))
            self.end_headers()
            self.wfile.write(body.encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path != '/asr':
            self.send_response(404)
            self.end_headers()
            return

        # 解析 multipart form-data
        content_type = self.headers.get('Content-Type', '')
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length > 50 * 1024 * 1024:  # 50MB 上传限制
            self._send_json({'status': 'error', 'msg': 'file too large'}, status=413)
            return

        if 'multipart/form-data' not in content_type:
            self.send_response(400)
            body = json.dumps({"error": "期望 multipart/form-data"})
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body.encode())))
            self.end_headers()
            self.wfile.write(body.encode())
            return

        try:
            # 读取原始 body，用标准库 email 解析 multipart（cgi 已废弃）
            body_bytes = self.rfile.read(content_length)
            fields = parse_multipart(body_bytes, content_type)

            if 'audio' not in fields or not fields['audio']['data']:
                self.send_response(400)
                body = json.dumps({"error": "缺少 audio 字段"})
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body.encode())))
                self.end_headers()
                self.wfile.write(body.encode())
                return

            # 读取 language 参数（可选）
            language = 'Chinese'
            lang_data = fields.get('language', {}).get('data')
            if lang_data:
                language = lang_data.decode('utf-8', 'replace').strip() or 'Chinese'

            # 保存到临时文件（Qwen3-ASR 也支持 numpy array，但用文件更稳）
            with tempfile.NamedTemporaryFile(suffix='.audio', delete=False) as tmp:
                tmp.write(fields['audio']['data'])
                tmp_path = tmp.name

            try:
                t0 = time.time()
                result = transcribe_file(tmp_path, language=language)
                elapsed = time.time() - t0
                result['elapsed_sec'] = round(elapsed, 2)
                logger.info(f"识别完成: '{result.get('text', '')}' 耗时 {elapsed:.2f}s")

                resp = json.dumps(result, ensure_ascii=False)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Content-Length', str(len(resp.encode('utf-8'))))
                self.end_headers()
                self.wfile.write(resp.encode('utf-8'))
            finally:
                try: os.unlink(tmp_path)
                except: pass

        except Exception as e:
            logger.error(f"POST /asr 异常: {e}")
            self.send_response(500)
            body = json.dumps({"error": str(e)})
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body.encode())))
            self.end_headers()
            self.wfile.write(body.encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=7780)
    parser.add_argument('--host', default='0.0.0.0')
    args = parser.parse_args()

    srv = ThreadingHTTPServer((args.host, args.port), ASRHandler)
    logger.info(f'ASR 服务就绪: http://{args.host}:{args.port}')
    logger.info(f'POST /asr (multipart audio) → text')
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        logger.info('停止')


if __name__ == '__main__':
    main()
