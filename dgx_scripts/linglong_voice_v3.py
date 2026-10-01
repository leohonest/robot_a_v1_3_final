#!/usr/bin/env python3
"""
灵龙端 Listener v3 (VAD 端点检测 + 完整段落)
============================================
架构：
  灵龙 mic (sounddevice) → webrtcvad 端点检测 → 完整 wav 段落
  → POST DGX 7780 /asr → text
  → 检测 wake 词 (本地)
  → 带对话窗口 (唤醒后 30 秒内 query 直接送)
  → POST DGX 11434 LLM → reply
  → POST DGX 9002 TTS → wav → aplay 播放

优势：
  - VAD 在灵龙端，准确切分完整句子（不被 1 秒切片打断）
  - DGX 只跑单文件 ASR（用 v1 transformers backend，7780，稳定）
  - 网络调用只发生在有完整段落时（不是每帧）
  - 唤醒后 30 秒对话窗口，支持多轮
"""
import os
import sys
import io
import time
import json
import wave
import base64
import logging
import argparse
import subprocess
import re
import struct
import collections
from datetime import datetime

import webrtcvad
import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(message)s'
)
logger = logging.getLogger('ll_v3')

# ================= 配置 =================

# DGX 地址
DGX_HOST = os.environ.get("DGX_HOST", "127.0.0.1")  # SSH 反向隧道场景保持 127.0.0.1
ASR_URL = f"http://{DGX_HOST}:7780/asr"
LLM_URL = f"http://{DGX_HOST}:7792/chat"  # Orchestrator (含 web_search)
# LLM_MODEL removed (7792 orchestrator handles model selection)
TTS_URL = f"http://{DGX_HOST}:9003/tts_base64"

# 音频
SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = 'int16'

# VAD
VAD_FRAME_MS = 30             # webrtcvad 接受 10/20/30ms
VAD_AGGRESSIVENESS = 1        # 0-3，越高越激进（更多静音判定为非语音）
SILENCE_FRAMES_TO_END = 50    # 连续 30 帧（900ms）静音 → 端点（Leo 放宽到 900ms 防切早）
MIN_SPEECH_FRAMES = 3         # 至少 5 帧（150ms）才认作有效语音

# 设备（Leo 2026-09-25 11:30 恢复 9/22 + 修正端口号）
# Card 3 = B107A6 (BATSound) - 纯麦 capture only (主 mic)
# Card 0 = USB Composite Device (Jieli) - 双工 (只用 playback 当喇叭)
# 启动参数: --device plughw:3,0 --speaker-device plughw:0,0
INPUT_DEVICE = None  # 由 --device 控制
SPEAKER_DEVICE = None  # 由 --speaker-device 控制

# 对话窗口
DIALOG_WINDOW_SEC = 30

# ASR 修正
WAKE_PATTERNS = [
    r'你好[,.，。、\s]*[玲珑临林凌陵龄隆龙]',
    r'^[玲珑临林凌陵龄隆龙]',
    r'[玲珑临林凌陵龄隆龙][,，]?\s*在吗',
    r'[玲珑临林凌陵龄隆龙][,，]?\s*你',
]
ASR_CORRECTIONS = [
    ('玲珑', '灵龙'), ('临龙', '灵龙'), ('林龙', '灵龙'),
    ('凌龙', '灵龙'), ('陵龙', '灵龙'), ('龄龙', '灵龙'),
    ('隆龙', '灵龙'), ('柠檬', '灵龙'),
    # (decision recorded in git log)
    ('回宫卫衣', '回工位一'),
    ('回宫位衣', '回工位一'),
    ('宫卫衣', '工位一'),
    ('宫', '工'),  # ASR同音（只用在工位上下文）
    ('卫', '位'),  # 同上
    ('衣', '一'),  # 同上
]


# ================= DialogManager =================

class DialogManager:
    def __init__(self, max_turns=6):
        self.history = []
        self.max_turns = max_turns
    
    def add_user(self, text):
        if text and text.strip():
            self.history.append({"role": "user", "content": text.strip()})
            self._trim()
    
    def add_assistant(self, text):
        if text and text.strip():
            self.history.append({"role": "assistant", "content": text.strip()})
            self._trim()
    
    def _trim(self):
        while len(self.history) > self.max_turns * 2:
            self.history.pop(0)
    
    def clear(self):
        self.history.clear()
    
    def render_prompt(self, system_prompt, current_query):
        parts = [f"[SYSTEM] {system_prompt}"]
        for m in self.history:
            role = m["role"].upper()
            parts.append(f"[{role}] {m['content']}")
        parts.append(f"[USER] {current_query}")
        parts.append("[ASSISTANT]")
        return "\n".join(parts)


# ================= 唤醒词检测 =================

def detect_wake(text):
    # Loose wake: any position contains 灵龙/玲珑/龙/珑/笼 (Leo 13:34 放宽)
    # 按长度从长到短排序，避免"龙"截胡"灵龙"
    if not text:
        return False, ""
    corrected = text
    for w, r in ASR_CORRECTIONS:
        corrected = corrected.replace(w, r)
    corrected = corrected.strip()

    WAKE_KEYWORDS = ['灵龙', '玲珑', '林龙', '麟龙', '临龙', '凌龙', '陵龙', '龄龙', '隆龙', '柠檬', '龙', '珑', '笼', '隆', '咙']
    for kw in WAKE_KEYWORDS:
        idx = corrected.find(kw)
        if idx >= 0:
            query = corrected[idx + len(kw):].strip(' ,，。.?？!！')
            logger.info(f"  wake keyword '{kw}' at pos {idx} -> query='{query}'")
            return True, query

    logger.info(f"  no wake keyword in '{corrected}' (orig='{text}')")
    return False, ""

# ================= Filler 词过滤 =================
FILLER_WORDS = set('嗯啊哦呃哎喔噢呀哈嚯诶呜嘻呵')


def is_filler_only(text):
    # if text is only sound-symbol (white noise miss-trigger), return True
    if not text:
        return True
    cleaned = re.sub(r'[，。、！?？\. ,。？！、]+', '', text).strip()
    if not cleaned:
        return True
    for ch in cleaned:
        if ch not in FILLER_WORDS:
            return False
    return True


# ================= DGX HTTP 调用 =================

def asr_call(wav_bytes):
    """上传 wav 到 DGX ASR"""
    files = {'audio': ('chunk.wav', io.BytesIO(wav_bytes), 'audio/wav')}
    data = {'language': 'Chinese'}
    try:
        resp = requests.post(ASR_URL, files=files, data=data, timeout=30)
        resp.raise_for_status()
        return resp.json().get('text', '').strip()
    except Exception as e:
        logger.error(f"ASR 失败: {e}")
        return ""


def llm_call(query, dm=None):
    """调 DGX Orchestrator (7792) 走 LLM"""

    logger.info(f"[LLM] history_turns={len(dm.history)//2 if dm else 0}, query='{query}'")
    try:
        payload = {"query": query or "你好"}
        if dm and dm.history:
            payload["history"] = [{"role": m["role"], "content": m["content"]} for m in dm.history[-6:]]
        resp = requests.post(LLM_URL, json=payload, timeout=60)
        resp.raise_for_status()
        d = resp.json()
        text = d.get("response", "").strip()
        source = d.get("source", "?")
        intent = d.get("intent", "?")
        logger.info(f"[LLM] source={source}, intent={intent}, len={len(text)}")
        return text or "抱歉，没有好的答案。"
    except Exception as e:
        logger.error(f"LLM 调用失败: {e}")
        return "抱歉，LLM 服务超时请重试。"


def action_check(query: str):
    """
    Leo 2026-09-28 11:57: 直连 DGX action_router_server (7793) 检查 query 是否命中本地操作。
    返回 dict {matched, audio_b64, action_id, params, result}
    """
    ACTION_URL = f"http://{DGX_HOST}:7793/action"
    try:
        resp = requests.post(ACTION_URL, json={"query": query}, timeout=5)
        resp.raise_for_status()
        d = resp.json()
        matched = d.get("matched", False)
        result = d.get("result", {})
        audio_b64 = result.get("audio_b64") if matched else None
        action_id = d.get("action_id") if matched else None
        params = d.get("params", {})
        logger.info(f"[ACTION_CHECK] matched={matched}, action_id={action_id}, audio_b64={'yes' if audio_b64 else 'no'}")
        return {
            "matched": matched,
            "audio_b64": audio_b64,
            "action_id": action_id,
            "params": params,
            "result": result,
        }
    except Exception as e:
        logger.warning(f"[ACTION_CHECK] failed: {e}")
        return {"matched": False, "audio_b64": None, "action_id": None, "params": {}, "result": {}}


def _aplay_cmd(wav_path: str):
    """构造 aplay 命令（列表参数，无 shell 注入风险）"""
    cmd = ['aplay', '-q']
    if SPEAKER_DEVICE:
        cmd += ['-D', SPEAKER_DEVICE]
    cmd.append(wav_path)
    return cmd


def play_conf_audio(audio_b64: str = None):
    """
    Leo 2026-09-28 12:09: 异步播放"收到"音频（不阻塞调用方）。
    用 subprocess.Popen 让 aplay 后台跑，WAKE 触发后立即返回。
    优先用 DGX 发来的 audio_b64（最新），fallback 到本地 /home/user/sounds/shoudao.wav。
    """
    wav_path = '/home/user/sounds/shoudao.wav'
    if audio_b64:
        try:
            conf_wav = base64.b64decode(audio_b64)
            tmp = '/tmp/_ll_conf_play.wav'
            with open(tmp, 'wb') as f:
                f.write(conf_wav)
            subprocess.Popen(_aplay_cmd(tmp))
            logger.info(f"[CONF] async playing {len(conf_wav)} bytes audio_b64")
            return
        except Exception as e:
            logger.error(f"[CONF] audio_b64 decode failed: {e}, fallback to local file")
    try:
        subprocess.Popen(_aplay_cmd(wav_path))
        logger.info(f"[CONF] async playing local {wav_path}")
    except Exception as e:
        logger.error(f"[CONF] fallback play failed: {e}")


def tts_call(text):
    """调 DGX TTS"""
    try:
        resp = requests.post(TTS_URL, json={
            "text": text, "language": "Chinese",
            "use_cache": True, "auto_cantonese": True
        }, timeout=120)
        resp.raise_for_status()
        d = resp.json()
        b64 = d.get('audio_base64') or d.get('audio_b64')
        if not b64:
            raise RuntimeError(f"no audio: {d}")
        return base64.b64decode(b64)
    except Exception as e:
        logger.error(f"TTS 失败: {e}")
        return None


def play_wav(wav_bytes):
    """保存 + aplay 播放到指定 speaker (Leo 2026-09-25 11:30 端口修正)"""
    tmp = "/tmp/ll_reply.wav"
    with open(tmp, 'wb') as f:
        f.write(wav_bytes)
    rc = subprocess.run(_aplay_cmd(tmp))
    if rc.returncode != 0:
        logger.error(f"[PLAY] aplay failed, rc={rc.returncode}")
    return rc.returncode == 0


# ================= VAD 录音 =================
def play_wav_bytes(wav_bytes: bytes):
    """Leo 11:48: 同步播放 wav bytes（不读盘，DGX 发来的 audio_b64）"""
    tmp = "/tmp/_ll_conf_play.wav"
    try:
        with open(tmp, 'wb') as f:
            f.write(wav_bytes)
        subprocess.run(_aplay_cmd(tmp), timeout=30)
    except Exception as e:
        logger.error(f"play_wav_bytes failed: {e}")


def play_wav_file(path: str):
    """Leo 11:48: 同步播放 wav 文件路径（fallback 到灵龙本地文件）"""
    try:
        subprocess.run(_aplay_cmd(path), timeout=30)
    except Exception as e:
        logger.error(f"play_wav_file failed: {e}")


def record_until_vad(input_device, sample_rate=SAMPLE_RATE, frame_ms=VAD_FRAME_MS,
                     silence_to_end=SILENCE_FRAMES_TO_END, min_speech=MIN_SPEECH_FRAMES,
                     max_duration_sec=15):
    """
    用 arecord 子进程流式录音 + webrtcvad 端点检测
    返回：bytes (完整段落 wav int16) 或 None
    """
    vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
    frame_size = int(sample_rate * frame_ms / 1000)
    frame_bytes = frame_size * 2  # int16
    
    # arecord 命令：plughw:2,0 (B107A6 mic)
    cmd = ['arecord', '-D', input_device, '-f', 'S16_LE', '-r', str(sample_rate),
           '-c', '1', '-t', 'raw', '-q']
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            bufsize=frame_bytes * 4)
    
    speech_buffer = bytearray()
    speech_frame_count = 0
    silence_frame_count = 0
    in_speech = False
    started_at = time.time()
    
    logger.info(f"[VAD] listening... (device={input_device}, frame={frame_ms}ms, silence_to_end={silence_to_end}frames)")
    
    try:
        while True:
            pcm = proc.stdout.read(frame_bytes)
            if len(pcm) < frame_bytes:
                # arecord 异常退出
                logger.warning(f"arecord 返回 {len(pcm)} bytes, 期望 {frame_bytes}")
                break
            
            try:
                is_speech = vad.is_speech(pcm, sample_rate)
            except Exception as e:
                logger.warning(f"VAD 错误: {e}")
                continue
            
            if is_speech:
                if not in_speech:
                    logger.info(f"[VAD] speech START")
                    in_speech = True
                speech_buffer.extend(pcm)
                speech_frame_count += 1
                silence_frame_count = 0
            else:
                if in_speech:
                    silence_frame_count += 1
                    speech_buffer.extend(pcm)  # 仍保留静音帧（避免截断）
                    
                    if silence_frame_count >= silence_to_end:
                        # 端点
                        if speech_frame_count >= min_speech:
                            logger.info(f"[VAD] speech END ({speech_frame_count} frames, {len(speech_buffer)} bytes)")
                            # 去掉末尾的静音帧
                            keep_bytes = (speech_frame_count + (silence_frame_count // 2)) * frame_bytes
                            speech_buffer = speech_buffer[:keep_bytes]
                            proc.terminate()
                            proc.wait(timeout=2)
                            return bytes(speech_buffer)
                        else:
                            logger.info(f"[VAD] too short ({speech_frame_count} frames), ignore")
                            speech_buffer = bytearray()
                            speech_frame_count = 0
                            silence_frame_count = 0
                            in_speech = False
            
            # 上限保护
            if time.time() - started_at > max_duration_sec:
                logger.info(f"[VAD] timeout, return")
                proc.terminate()
                proc.wait(timeout=2)
                if speech_frame_count >= min_speech:
                    return bytes(speech_buffer)
                return None
    
    except KeyboardInterrupt:
        proc.terminate()
        return None
    except Exception as e:
        logger.error(f"录音失败: {e}")
        try: proc.terminate()
        except: pass
        return None


def pcm_to_wav_bytes(pcm_data, sample_rate=SAMPLE_RATE, channels=CHANNELS):
    """int16 PCM bytes → wav bytes"""
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)  # int16
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return buf.getvalue()


# ================= 主流程 =================

_last_tts_end = 0.0
COOLDOWN_SEC = 4.0

def process_query(query, dm):
    """
    Leo 2026-09-28 11:57: 先直连 action_router_server (7793) 命中本地操作 → 放 conf + TTS 状态。
    未命中 → 走 orchestrator (7792) LLM → TTS 回复文本。
    不修改 orchestrator（Leo 说"不涉及 orchestrator 修改"）。
    """
    if not query:
        query = ""
    dm.add_user(query)

    # === Step 1: 先查 action_router_server ===
    ar = action_check(query)
    if ar["matched"]:
        logger.info(f"[ACTION] 命中: {ar['action_id']} params={ar['params']}")
        # 立即放"收到"音频（与底盘/上肢操作并行，不阻塞）
        # TTS 播放结果状态
        action_id = ar["action_id"]
        params = ar["params"]
        if action_id in ("chassis_fwd", "chassis_back"):
            d = params.get("distance_m", 0.3)
            status_text = f"好的，前进{int(d*100)}公分" if "fwd" in action_id else f"好的，后退{int(d*100)}公分"
        elif action_id in ("chassis_left", "chassis_right"):
            ang = params.get("angle_deg", 45)
            status_text = f"好的，左转{int(ang)}度" if "left" in action_id else f"好的，右转{int(ang)}度"
        elif action_id == "chassis_stop":
            status_text = "好的，停了"
        elif action_id == "enable":
            status_text = "好的，上使能"
        elif action_id == "disable":
            status_text = "好的，下使能"
        elif action_id == "home":
            status_text = "好的，归位"
        elif action_id == "grip_L_close":
            status_text = "好的，左手握紧"
        elif action_id == "grip_L_open":
            status_text = "好的，左手松开"
        elif action_id == "grip_R_close":
            status_text = "好的，右手握紧"
        elif action_id == "grip_R_open":
            status_text = "好的，右手松开"
        elif action_id in ("nav_to_station", "nav_station_to_station"):
            status_text = "好的，导航中"
        else:
            status_text = "好的，命令已执行"
        # TTS 状态文本
        wav = tts_call(status_text)
        if wav:
            play_wav(wav)
            global _last_tts_end
            _last_tts_end = time.time()
        dm.add_assistant(status_text)
        return  # 不走 LLM

    # === Step 2: 未命中，走 orchestrator LLM ===
    logger.info("[LLM_PATH] action_router 未命中，走 LLM")
    reply_text = llm_call(query, dm)
    logger.info(f"[LLM] reply='{reply_text[:80]}'")
    dm.add_assistant(reply_text)

    # LLM 路径不放"收到"（只在 action 路径放）
    # 强制 TTS 播放 reply 文本
    segments = [s.strip() for s in re.split(r'[。,.!?;:、!?;:\n]+', reply_text) if s.strip()]
    logger.info(f"[TTS] split into {len(segments)} segments")
    played = False
    for seg in segments:
        wav = tts_call(seg + '。')
        if not wav:
            continue
        logger.info(f"[TTS] playing segment ({len(wav)} bytes)")
        play_wav(wav)
        _last_tts_end = time.time()
        played = True
    if played:
        logger.info(f"[COOLDOWN] mic skip for {COOLDOWN_SEC}s after TTS")
    else:
        logger.error("[TTS] 失败，无法播放")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--device', type=str, default='plughw:3,0', help='Input device (default: plughw:3,0 = Card 3 B107A6 USB mic)')
    parser.add_argument('--speaker-device', type=str, default='plughw:0,0', help='Output speaker device (default: plughw:0,0 = Card 0 USB Composite playback)')
    parser.add_argument('--max-duration', type=int, default=15, help='Max speech duration (s)')
    args = parser.parse_args()
    
    device = args.device
    speaker = args.speaker_device
    
    # (decision recorded in git log)
    global SPEAKER_DEVICE
    SPEAKER_DEVICE = speaker
    
    logger.info("=" * 60)
    logger.info(f"灵龙端 Listener v3 (VAD 端点) 启动")
    logger.info(f"  Input  device: {device}  (mic)")
    logger.info(f"  Output device: {speaker} (speaker/TTS playback)")
    logger.info(f"  DGX ASR: {ASR_URL}")
    logger.info(f"  DGX LLM: {LLM_URL} (Orchestrator)")
    logger.info(f"  DGX TTS: {TTS_URL}")
    logger.info(f"  VAD frame: {VAD_FRAME_MS}ms | silence to end: {SILENCE_FRAMES_TO_END} frames ({SILENCE_FRAMES_TO_END*VAD_FRAME_MS}ms)")
    logger.info(f"  Dialog window: {DIALOG_WINDOW_SEC}s")
    logger.info("=" * 60)
    
    dm = DialogManager(max_turns=6)
    in_dialog = False
    dialog_start = 0.0
    asr_buffer = []           # ASR 多段拼接 buffer（解决"灵龙"被切到上一段）
    asr_buffer_last_t = 0.0  # 上次 buffer 时间戳
    
    try:
        while True:
            # TTS cooldown: mic mute to prevent feedback loop
            cooldown_left = COOLDOWN_SEC - (time.time() - _last_tts_end)
            if cooldown_left > 0:
                logger.info(f"[COOLDOWN] skip ({cooldown_left:.1f}s left)")
                time.sleep(min(0.3, cooldown_left))
                continue

            pcm = record_until_vad(device, max_duration_sec=args.max_duration)
            if not pcm:
                continue
            
            # 转 wav
            wav_bytes = pcm_to_wav_bytes(pcm)
            
            # 调 DGX ASR
            text = asr_call(wav_bytes)
            if not text:
                logger.info("[ASR] empty, ignore")
                continue
            
            # (decision recorded in git log)
            # buffer 仍照常更新（让 filler 累积参与 wake 判断）
            if not is_filler_only(text):
                logger.info(f"[ASR] '{text}'")
            
            # 对话窗口检查
            now = time.time()
            if in_dialog and (now - dialog_start) > DIALOG_WINDOW_SEC:
                logger.info(f"[DIALOG] window timeout ({(now-dialog_start):.1f}s), close")
                in_dialog = False
            
            # ASR buffer 拼接（Leo 方案2）：保留最近几段，3 秒内拼接后 detect_wake
            # 解决"灵龙"被 VAD 切到上一段的问题
            asr_buffer.append(text)
            asr_buffer = asr_buffer[-3:]  # 最多保留 3 段
            joined = ' '.join(asr_buffer)
            matched, query = detect_wake(joined)
            if matched:
                # === Leo 12:09: WAKE 触发立即异步放"收到"，与 TTS 完全独立 ===
                play_conf_audio()
                logger.info(f"[WAKE] query='{query}' (joined buffer: {asr_buffer})")
                process_query(query, dm)
                asr_buffer = []
                asr_buffer_last_t = now
            else:
                # Strict: 没有灵龙前缀一律 ignore
                logger.info(f"[IGNORE] '{text}' (no 灵龙 prefix, skipped; buffer={asr_buffer})")
                # 3 秒没新段就清 buffer
                if now - asr_buffer_last_t > 3.0:
                    asr_buffer = []
                asr_buffer_last_t = now
    
    except KeyboardInterrupt:
        logger.info("Stopped by Ctrl+C")


if __name__ == '__main__':
    main()