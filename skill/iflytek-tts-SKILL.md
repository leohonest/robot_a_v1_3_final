---
name: iflytek-tts
description: iFlytek (科大讯飞) Text-to-Speech integration. Use when converting text to speech using iFlytek's Xunfei TTS API. Supports multiple voices including Xiaofeng (小峰) for standard Mandarin male voice.
---

# iFlytek TTS Skill

This skill provides text-to-speech capabilities using iFlytek (科大讯飞) Xunfei TTS API.

## Capabilities

### 1. Text-to-Speech Conversion
- Convert Chinese text to speech
- Convert English text to speech
- Support mixed Chinese-English content

### 2. Voice Options
- **Xiaofeng (小峰)** - Standard Mandarin male voice (default)
- Xiaoyan (小燕) - Standard Mandarin female voice
- Other voices available

### 3. Audio Output
- Save to file
- Direct playback
- Queue management for sequential playback

## Configuration

### API Credentials
```
APPID: 3577b384
APIKey: ${IFLYTEK_API_KEY}
APISecret: ${IFLYTEK_API_SECRET}
```

### Voice Settings
- Voice: xiaofeng (小峰)
- Language: zh_cn (Chinese)
- Speed: 50 (normal)
- Volume: 50 (normal)
- Pitch: 50 (normal)

## Scripts

### scripts/iflytek_tts.py
Main TTS script.

Usage:
```bash
python3 scripts/iflytek_tts.py "要播报的文本"
```

### scripts/speak.sh
Quick speech wrapper.

Usage:
```bash
./scripts/speak.sh "要播报的文本"
```

## Integration with Video Window

When using TTS, simultaneously write to queue file for video window sync:

```bash
# Voice broadcast + video sync
echo "播报文本" > /tmp/david_speak_queue.txt
python3 scripts/iflytek_tts.py "播报文本"
```

## References

- references/voice_list.md - Available voices and parameters
- references/api_reference.md - iFlytek API documentation
