#!/usr/bin/env python3
"""
DGX 端混合 LLM Orchestrator
============================
端口: 7792

功能：
- 接收 query + history
- 意图分类 → 路由到本地 4B 或云端 MiniMax
- 注入当前时间（避免 LLM 瞎编时间）
- 返回 response + source 标记

接口：
  POST /chat
    body: {
      "query": "用户问题",
      "history": [{"role":"user","content":"..."}, ...],  # 可选
      "prefer": "auto" | "local" | "cloud"  # 可选，默认 auto
    }
    response: {
      "response": "LLM 回答",
      "source": "local" | "cloud",
      "elapsed_ms": 1234,
      "intent": "search" | "reasoning" | "chitchat"
    }

意图分类（关键词快速路由）：
  cloud (search):
    - 今天/明天/昨天/现在/当前
    - 几号/星期几/日期/时间/几点
    - 天气/气温/温度
    - 新闻/最新/刚刚/刚才
    - 股票/股价/价格/涨跌
    - 查/搜/搜索/帮我查
  local (reasoning):
    - 其他（闲聊、推理、常识、故事等）
"""
import os
import sys
import json
import time
import logging
import argparse
import asyncio
from datetime import datetime
from typing import Optional, List, Dict, Any

import urllib.request, urllib.error
import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
import uvicorn

# === 动作路由接入（Leo 2026-09-22 06:54）===
sys.path.insert(0, os.environ.get('DGX_SCRIPTS_DIR', '/data/linglong-project/scripts'))
try:
    import action_router
    ACTION_ROUTER_ENABLED = True
    logging.getLogger('orch').info("action_router loaded OK")
except Exception as e:
    ACTION_ROUTER_ENABLED = False
    logging.getLogger('orch').warning(f"action_router load failed: {e}, LLM-only mode")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(message)s'
)
logger = logging.getLogger('orch')

# ================= 配置 =================

# 本地 Ollama
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen3-vl:4b-instruct-q4_K_M"

# 云端 MiniMax
MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "")
MINIMAX_URL = "https://api.minimaxi.com/anthropic/v1/messages"
MINIMAX_MODEL = "MiniMax-M3"

# 端口
DEFAULT_PORT = 7792

# 意图分类关键词
CLOUD_KEYWORDS = [
    "今天", "明天", "昨天", "后天", "前天",
    "现在", "当前", "此刻", "刚才", "刚刚",
    "几号", "几月", "星期几", "周几", "礼拜几",
    "日期", "时间", "几点", "几时",
    "天气", "气温", "温度", "下雨", "下雪", "刮风",
    "新闻", "最新", "最近", "刚发生", "实时",
    "股票", "股价", "价格", "涨跌", "走势", "行情",
    "汇率", "利率", "油价", "金价",
    "查", "搜", "搜索", "帮我查", "帮我搜",
    "最新款", "新版本", "更新到", "升级",
]

CHITCHAT_KEYWORDS = [
    "你好", "hello", "hi", "嗨", "在吗",
    "你是谁", "你叫什么", "介绍", "自我介绍",
    "谢谢", "感谢", "再见", "拜拜",
    "早上好", "中午好", "下午好", "晚上好", "晚安",
]


# ================= 工具 =================

def get_current_time_str() -> str:
    """生成系统提示中的当前时间字符串"""
    now = datetime.now()
    weekday_names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday = weekday_names[now.weekday()]
    return f"{now.year}年{now.month}月{now.day}日 {weekday} {now.hour:02d}:{now.minute:02d}:{now.second:02d}"


def build_system_prompt(extra: str = "") -> str:
    """构建带时间注入的系统提示"""
    now_str = get_current_time_str()
    return (
        f"你是灵龙，一个友好、简洁的中文助手。"
        f"【当前时间：{now_str}】（用户问时间/日期/星期几等问题必须参考此时间，不要瞎编）。"
        f"{extra}"
        f"请用 1-2 句简短中文回答用户问题。"
    )


def classify_intent(query: str) -> str:
    """关键词快速分类意图"""
    q = query.strip()
    
    # 闲聊
    for kw in CHITCHAT_KEYWORDS:
        if kw in q:
            return "chitchat"
    
    # 云端（实时/搜索）
    for kw in CLOUD_KEYWORDS:
        if kw in q:
            return "search"
    
    # 默认本地
    return "reasoning"


def build_messages(query: str, history: List[Dict] = None) -> List[Dict]:
    """构建 OpenAI / Anthropic 兼容的 messages 格式"""
    msgs = []
    
    # history 最多 8 轮（4 来回）
    if history:
        msgs.extend(history[-8:])
    
    msgs.append({"role": "user", "content": query})
    return msgs


# ================= 本地 4B (Ollama) =================

def call_ollama(query: str, history: List[Dict] = None) -> str:
    """调本地 Ollama 4B"""
    system_prompt = build_system_prompt()
    msgs = build_messages(query, history)
    
    # Ollama /api/generate 用 prompt 字符串（不是 messages）
    prompt_parts = [f"[SYSTEM] {system_prompt}"]
    for m in msgs[:-1]:
        role = m["role"].upper()
        prompt_parts.append(f"[{role}] {m['content']}")
    prompt_parts.append(f"[USER] {query}")
    prompt_parts.append("[ASSISTANT]")
    prompt = "\n".join(prompt_parts)
    
    try:
        resp = requests.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False
        }, timeout=60)
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception as e:
        logger.error(f"Ollama 失败: {e}")
        raise HTTPException(503, f"local LLM error: {e}")


# ================= 云端 MiniMax =================

def call_minimax(query: str, history: List[Dict] = None) -> str:
    """调云端 MiniMax M3 (Anthropic 兼容 API)"""
    system_prompt = build_system_prompt(
        extra="【联网搜索提示：用户问最新信息（天气/股票/新闻等）时，请主动调用 web_search 工具查询后再回答，不要瞎编。】"
    )
    msgs = build_messages(query, history)
    
    body = json.dumps({
        "model": MINIMAX_MODEL,
        "system": system_prompt,
        "messages": msgs,
        "max_tokens": 1024
        ,"tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]
    }).encode('utf-8')
    
    try:
        req = urllib.request.Request(MINIMAX_URL, data=body, method='POST',
                                     headers={
                                         'X-Api-Key': MINIMAX_API_KEY,
                                         'Content-Type': 'application/json',
                                         'anthropic-version': '2023-06-01'
                                     })
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read())
            content = d.get('content', [])
            if isinstance(content, list):
                text = "".join(b.get('text', '') for b in content if b.get('type') == 'text')
            else:
                text = str(content)
            return text.strip()
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        logger.error(f"MiniMax 失败 {e.code}: {err[:300]}")
        raise HTTPException(503, f"cloud LLM error: {e.code} {err[:200]}")
    except Exception as e:
        logger.error(f"MiniMax 失败: {e}")
        raise HTTPException(503, f"cloud LLM error: {e}")


# ================= FastAPI =================

app = FastAPI(title="DGX LLM Orchestrator (本地 + 云端)")


@app.post("/chat")
def chat(req: dict):  # 普通函数：FastAPI 自动放线程池，避免阻塞事件循环
    query = req.get("query", "").strip()
    history = req.get("history", [])
    prefer = req.get("prefer", "auto")
    
    if not query:
        raise HTTPException(400, "query empty")
    
    t0 = time.time()

    # === 动作路由优先（Leo 2026-09-22 06:54）===
    # query 先过 action_router，命中则直接返回动作结果，不走 LLM
    if ACTION_ROUTER_ENABLED:
        try:
            ar_result = action_router.route(query)
            if ar_result.get('matched'):
                elapsed_ms = int((time.time() - t0) * 1000)
                logger.info(f"[action_router] query={query!r} → {ar_result['action_id']}")
                return JSONResponse({
                    "response": f"[动作] {ar_result['action_id']}: {ar_result.get('result', {}).get('status', 'ok')}",
                    "source": "action_router",
                    "intent": "action",
                    "elapsed_ms": elapsed_ms,
                    "action_id": ar_result['action_id'],
                    "action_result": ar_result.get('result', {}),
                    "timestamp": datetime.now().isoformat()
                })
        except Exception as ar_err:
            logger.warning(f"action_router error: {ar_err}")
    # === 动作路由 end ===
    
    intent = classify_intent(query)
    
    # 路由
    if prefer == "local" or (prefer == "auto" and intent == "reasoning"):
        try:
            response = call_ollama(query, history)
            source = "local"
        except HTTPException as e:
            # 本地失败 fallback 云端
            logger.warning(f"本地 LLM 失败，fallback 云端: {e}")
            response = call_minimax(query, history)
            source = "cloud (fallback)"
    elif prefer == "cloud" or (prefer == "auto" and intent == "search"):  # 闲聊走本地，省云端费用
        try:
            response = call_minimax(query, history)
            source = "cloud"
        except HTTPException as e:
            logger.warning(f"云端 LLM 失败，fallback 本地: {e}")
            response = call_ollama(query, history)
            source = "local (fallback)"
    else:
        response = call_ollama(query, history)
        source = "local"
    
    elapsed_ms = int((time.time() - t0) * 1000)
    return JSONResponse({
        "response": response,
        "source": source,
        "intent": intent,
        "elapsed_ms": elapsed_ms,
        "timestamp": datetime.now().isoformat()
    })


@app.get("/")
@app.get("/health")
async def health():
    return JSONResponse({
        "status": "ok",
        "service": "dgx-llm-orchestrator",
        "models": {
            "local": OLLAMA_MODEL,
            "cloud": MINIMAX_MODEL
        },
        "current_time": get_current_time_str()
    })


@app.get("/intents")
async def intents():
    """返回意图分类规则（调试用）"""
    return JSONResponse({
        "cloud_keywords": CLOUD_KEYWORDS,
        "chitchat_keywords": CHITCHAT_KEYWORDS,
        "rule": "先匹配 chitchat，再匹配 cloud，最后 reasoning"
    })


# ================= Main =================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    parser.add_argument('--host', default='0.0.0.0')
    args = parser.parse_args()
    
    if not MINIMAX_API_KEY:
        logger.error("MINIMAX_API_KEY 环境变量未设置！")
        sys.exit(1)
    
    logger.info(f"=" * 60)
    logger.info(f"DGX LLM Orchestrator 启动")
    logger.info(f"  Local: {OLLAMA_URL} ({OLLAMA_MODEL})")
    logger.info(f"  Cloud: {MINIMAX_URL} ({MINIMAX_MODEL})")
    logger.info(f"  Port: {args.port}")
    logger.info(f"  Time: {get_current_time_str()}")
    logger.info(f"=" * 60)
    
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == '__main__':
    main()