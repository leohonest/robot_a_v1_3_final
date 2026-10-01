#!/usr/bin/env python3
"""
hybrid_router - 本地+云端混合智能路由服务
DGX 端口: 7785

功能：
- /decide    决定任务本地还是云端
- /async_call 异步云端调用（不阻塞）
- /result    获取异步结果
- /stats      统计（token 用量 / 缓存命中）
- /health     健康检查

设计: 见 skills/linglong-hybrid-architecture-SKILL.md
"""
import http.server
import json
import threading
import queue
import time
import uuid
import sqlite3
import os
import sys
from datetime import datetime, timedelta

# ==================== 配置 ====================
PORT = 7785
DB_PATH = '/data/linglong-project/hybrid_router.db'
TOKEN_BUDGET_PER_DAY = 70_000_000  # 70M tokens/天（Leo 9/30 14:15 更正）
DEFAULT_TTL_SEC = 600  # 缓存默认 10 分钟
WORKER_COUNT = 4  # 后台 worker 线程数

# ==================== Token 计数器 ====================
class TokenCounter:
    def __init__(self, db_path):
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS token_usage (
            date TEXT,
            tokens_used INTEGER DEFAULT 0,
            PRIMARY KEY (date)
        )""")
        self.db.commit()
        self._lock = threading.Lock()

    def today_date(self):
        return datetime.now().strftime('%Y-%m-%d')

    def today_used(self):
        today = self.today_date()
        row = self.db.execute(
            'SELECT tokens_used FROM token_usage WHERE date=?', (today,)
        ).fetchone()
        return row[0] if row else 0

    def remaining(self):
        return max(0, TOKEN_BUDGET_PER_DAY - self.today_used())

    def add(self, tokens):
        today = self.today_date()
        with self._lock:
            self.db.execute(
                'INSERT INTO token_usage(date, tokens_used) VALUES(?, ?) '
                'ON CONFLICT(date) DO UPDATE SET tokens_used=tokens_used+?',
                (today, tokens, tokens)
            )
            self.db.commit()

    def can_spend(self, tokens):
        return self.today_used() + tokens <= TOKEN_BUDGET_PER_DAY

# ==================== 缓存 ====================
class HybridCache:
    def __init__(self, db_path):
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS cache (
            signature TEXT PRIMARY KEY,
            result TEXT,
            created_at INTEGER,
            ttl_sec INTEGER
        )""")
        self.db.commit()
        self._lock = threading.Lock()

    def get(self, sig):
        with self._lock:
            row = self.db.execute(
                'SELECT result, created_at, ttl_sec FROM cache WHERE signature=?',
                (sig,)
            ).fetchone()
            if not row:
                return None
            result, created_at, ttl_sec = row
            if time.time() - created_at > ttl_sec:
                return None
            try:
                return json.loads(result)
            except:
                return None

    def set(self, sig, result, ttl_sec=DEFAULT_TTL_SEC):
        with self._lock:
            self.db.execute(
                'INSERT OR REPLACE INTO cache VALUES (?, ?, ?, ?)',
                (sig, json.dumps(result, ensure_ascii=False), int(time.time()), ttl_sec)
            )
            self.db.commit()

    def clear_expired(self):
        with self._lock:
            cutoff = int(time.time()) - 86400
            self.db.execute('DELETE FROM cache WHERE created_at < ?', (cutoff,))
            self.db.commit()

# ==================== 云端调用 Worker ====================
class CloudWorker(threading.Thread):
    def __init__(self, task_queue, results_dict, token_counter, cloud_url=None):
        super().__init__(daemon=True)
        self.queue = task_queue
        self.results = results_dict
        self.tokens = token_counter
        self.cloud_url = cloud_url or "http://127.0.0.1:7786/cloud/walt"  # 未来接入 Walt 云端
        self.running = True

    def run(self):
        while self.running:
            try:
                req = self.queue.get(timeout=1)
            except queue.Empty:
                continue
            self._process(req)

    def _process(self, req):
        try:
            # TODO: 接入 Walt/M3 云端 HTTP 调用
            # 临时 mock：返回本地决策
            result = {
                "status": "mock",
                "request_id": req["id"],
                "msg": "Walt cloud endpoint not yet configured - using local mock",
                "result": {"action": "local", "confidence": 0.5}
            }
            # 估算 token 消耗（占位）
            estimated_tokens = len(str(req)) * 2
            if self.tokens.can_spend(estimated_tokens):
                self.tokens.add(estimated_tokens)
                result["tokens_used"] = estimated_tokens
            else:
                result = {
                    "status": "token_exceeded",
                    "request_id": req["id"],
                    "msg": f"Daily token budget ({TOKEN_BUDGET_PER_DAY}) exceeded"
                }
            self.results[req["id"]] = result
        except Exception as e:
            self.results[req["id"]] = {
                "status": "error",
                "request_id": req["id"],
                "msg": str(e)
            }

# ==================== HTTP 服务 ====================
class HybridRouter:
    def __init__(self):
        self.cache = HybridCache(DB_PATH)
        self.tokens = TokenCounter(DB_PATH)
        self.cloud_queue = queue.Queue()
        self.cloud_results = {}
        self.workers = []
        for i in range(WORKER_COUNT):
            w = CloudWorker(self.cloud_queue, self.cloud_results, self.tokens)
            w.start()
            self.workers.append(w)

    def decide(self, task):
        """决定本地/云端"""
        sig = self._signature(task)
        # 1. 查缓存
        cached = self.cache.get(sig)
        if cached:
            return {"action": "cache", "result": cached}
        # 2. 简单任务本地处理
        if task.get("type") == "chassis_simple":
            return {"action": "local", "result": "chassis_server handles"}
        # 3. 复杂任务上云
        return {"action": "cloud", "queue_position": self.cloud_queue.qsize()}

    def async_call(self, task):
        """异步云端调用"""
        req_id = uuid.uuid4().hex
        self.cloud_queue.put({"id": req_id, "task": task, "ts": time.time()})
        return {"request_id": req_id}

    def get_result(self, req_id, timeout=5):
        """获取异步结果"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if req_id in self.cloud_results:
                return self.cloud_results.pop(req_id)
            time.sleep(0.1)
        return {"status": "timeout", "request_id": req_id}

    def stats(self):
        return {
            "today_tokens_used": self.tokens.today_used(),
            "today_tokens_remaining": self.tokens.remaining(),
            "daily_budget": TOKEN_BUDGET_PER_DAY,
            "queue_size": self.cloud_queue.qsize(),
            "pending_results": len(self.cloud_results),
            "workers": len(self.workers)
        }

    def _signature(self, task):
        import hashlib
        key = json.dumps(task, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(key.encode()).hexdigest()

# 全局实例
_router = HybridRouter()

class HybridHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Production safety: log all incoming requests (audit + debugging)
        import logging
        logging.info(f"{self.command} {self.path} - {fmt % args if args else fmt}")

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8') if length else '{}'
        try:
            req = json.loads(body)
        except:
            self._send_json({"status": "error", "msg": "invalid json"}, 400)
            return

        if self.path == '/decide':
            self._send_json(_router.decide(req))
        elif self.path == '/async_call':
            self._send_json(_router.async_call(req))
        elif self.path == '/result':
            req_id = req.get('request_id')
            if not req_id:
                self._send_json({"status": "error", "msg": "missing request_id"}, 400)
                return
            self._send_json(_router.get_result(req_id, timeout=req.get('timeout_sec', 5)))
        else:
            self._send_json({"status": "error", "msg": "not found"}, 404)

    def do_GET(self):
        if self.path == '/health':
            self._send_json({"status": "ok", "service": "hybrid_router", "port": PORT})
        elif self.path == '/stats':
            self._send_json(_router.stats())
        else:
            self._send_json({"status": "error", "msg": "not found"}, 404)


if __name__ == '__main__':
    server = http.server.ThreadingHTTPServer(('0.0.0.0', PORT), HybridHandler)
    print(f'hybrid_router listening on 0.0.0.0:{PORT}')
    print(f'DB: {DB_PATH}')
    print(f'Token budget: {TOKEN_BUDGET_PER_DAY:,}/day')
    print(f'Workers: {WORKER_COUNT}')
    server.serve_forever()
