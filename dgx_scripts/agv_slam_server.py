#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AGV 扫图与地图上传服务 (DGX :7794)
- 6100 开始扫图
- 6101 停止扫图
- 1025 轮询获取 resultmap
- 2025 上传并加载到 AGV
- send_agv() 直接用命令 ID (10进制)，跟 xiangong-agv-slam/SKILL 一致
"""
import socket, struct, json, time, base64, os, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AGV_IP = 'os.environ.get("AGV_HOST", "192.168.1.204")'
AGV_PORTS = {'state': int(os.environ.get("AGV_PORT_STATE", "19204")), 'cmd': int(os.environ.get("AGV_PORT_CMD", "19205")), 'nav': int(os.environ.get("AGV_PORT_NAV", "19206")), 'other': 19210}

# 命令 ID (跟 SKILL 一致：直接传 10 进制)
CMD_6100 = 6100  # 开始扫图
CMD_6101 = 6101  # 停止扫图
CMD_1025 = 1025  # 查询地图
CMD_2025 = 2025  # 上传加载

MAP_DIR = '/data/linglong-project/maps'

def send_agv(port, cmd_id, json_data, number=1, timeout=5):
    """AGV TCP 客户端 (跟 SKILL 一致: msg_type = cmd_id 10进制)"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect((AGV_IP, port))
    body = json.dumps(json_data, ensure_ascii=False).encode('utf-8')
    header = struct.pack('>BBHIH', 0x5A, 0x01, number, len(body), cmd_id) + b'\x00' * 6
    sock.sendall(header + body)
    resp = b''
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk: break
            resp += chunk
            if len(resp) >= 16:
                r_len = struct.unpack('>I', resp[4:8])[0]
                if len(resp) >= 16 + r_len: break
    except socket.timeout:
        pass  # 超时视为无响应
    sock.close()
    if len(resp) >= 16:
        r_len = struct.unpack('>I', resp[4:8])[0]
        if r_len > 0 and len(resp) >= 16 + r_len:
            body_resp = resp[16:16+r_len]
            try:
                obj = json.loads(body_resp)
                return obj
            except:
                return {'raw': body_resp.decode('utf-8', errors='replace')}
    return {'ret_code': -1, 'msg': 'no response'}

def start_slam():
    """6100 START SLAM - 2026-09-29 13:05 v3: STOP then START"""
    try:
        send_agv(AGV_PORTS["other"], CMD_6101, {}, number=99, timeout=5)
        import time as _t
        _t.sleep(2)
    except Exception as e:
        print(f"[start_slam] pre-stop failed (ignored): {e}")
    data = {"slam_type": 2, "real_time": True, "screen_width": 1280, "screen_height": 720}
    return send_agv(AGV_PORTS["other"], CMD_6100, data, number=1)

def stop_slam():
    """6101 停止扫图"""
    return send_agv(AGV_PORTS['other'], CMD_6101, {}, number=2)

def query_resultmap(max_wait=120, interval=2):
    """1025 轮询获取 resultmap (返回 base64 字符串)"""
    deadline = time.time() + max_wait
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        resp = send_agv(AGV_PORTS['state'], CMD_1025,
                         {'return_resultmap': True}, number=attempt)
        if resp.get('ret_code') == 0 and resp.get('resultmap'):
            return resp
        time.sleep(interval)
    return {'ret_code': -1, 'msg': f'timeout waiting for resultmap ({max_wait}s)'}

def upload_and_load(map_json, map_name):
    """2025 上传并加载"""
    return send_agv(AGV_PORTS['cmd'], CMD_2025, map_json, number=100)  # Leo 9/14: 直接发 smap dict, 不包字段

# ===== HTTP handlers =====

class SlamHandler(BaseHTTPRequestHandler):
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

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        if self.path == '/health':
            self._send_json({'status': 'ok', 'service': 'agv_slam', 'port': 7794})
        else:
            self._send_json({'status': 'error', 'msg': 'not found'}, status=404)

    def do_GET(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8') if length else '{}'
        try:
            req = json.loads(body)
        except:
            self._send_json({'status': 'error', 'msg': 'invalid json'}, status=400)
            return

        if self.path == '/agv/slam/start':
            result = start_slam()
            self._send_json({
                'status': 'ok' if result.get('ret_code') == 0 else 'error',
                'ret_code': result.get('ret_code'),
                'cmd': '6100',
                'msg': result.get('msg', 'started')
            })
        elif self.path == '/agv/slam/stop':
            result = stop_slam()
            self._send_json({
                'status': 'ok' if result.get('ret_code') == 0 else 'error',
                'ret_code': result.get('ret_code'),
                'cmd': '6101',
                'msg': result.get('msg', 'stopped')
            })
        elif self.path == '/agv/slam/upload':
            timeout_sec = req.get('timeout_sec', 300)
            map_name = req.get('map_name', 'office1.smap')
            load_to_agv = req.get('load_to_agv', True)

            print(f'[upload] polling 1025 for resultmap (timeout {timeout_sec}s)...')
            resp = query_resultmap(max_wait=timeout_sec)
            if resp.get('ret_code') != 0 or not resp.get('resultmap'):
                self._send_json({
                    'status': 'error',
                    'msg': f"1025 failed: {resp.get('msg', 'no resultmap')}",
                    'ret_code': resp.get('ret_code'),
                })
                return

            # 保存到 DGX（map_name 只允许纯文件名，防路径穿越）
            os.makedirs(MAP_DIR, exist_ok=True)
            map_name = os.path.basename(str(map_name))
            if not map_name or map_name in {'.', '..'} or '/' in map_name or chr(92) in map_name:
                self._send_json({'status': 'error', 'msg': 'invalid map_name'}, status=400)
                return
            map_path = os.path.join(MAP_DIR, map_name)
            try:
                map_data = resp['resultmap']  # Smap dict from AGV
                print(f'[UPLOAD] map_data type={type(map_data).__name__}, keys={list(map_data.keys()) if isinstance(map_data, dict) else None}', flush=True)
                with open(map_path, 'w', encoding='utf-8') as f:
                    f.write(json.dumps(map_data, ensure_ascii=False, indent=2))
                    f.flush()
                    import os as _os
                    print(f'[UPLOAD] saved to {map_path}, size={_os.path.getsize(map_path)}', flush=True)
            except Exception as e:
                import traceback as _tb
                print(f'[UPLOAD ERROR] {e}', flush=True)
                _tb.print_exc()
                self._send_json({'status': 'error', 'msg': f'smap save failed: {e}'})
                return

            map_size = os.path.getsize(map_path)

            if not load_to_agv:
                self._send_json({
                    'status': 'ok',
                    'msg': 'saved to DGX',
                    'map_size_bytes': map_size,
                    'map_saved_to': map_path,
                })
                return

            # 上传加载 (1025 返回的 resultmap 已经是 base64 字符串, 直接用)
            upload_resp = upload_and_load(resp['resultmap'], map_name)
            self._send_json({
                'status': 'ok' if upload_resp.get('ret_code') == 0 else 'error',
                'ret_code': upload_resp.get('ret_code'),
                'msg': upload_resp.get('msg', 'uploaded and loaded'),
                'map_size_bytes': map_size,
                'map_saved_to': map_path,
                'agv_upload_response': upload_resp,
            })
        else:
            self._send_json({'status': 'error', 'msg': 'not found'}, status=404)

if __name__ == '__main__':
    port = 7794
    server = ThreadingHTTPServer(('0.0.0.0', port), SlamHandler)
    print(f'AGV SLAM Server listening on 0.0.0.0:{port}')
    print(f'AGV: {AGV_IP}')
    print(f'Map dir: {MAP_DIR}')
    server.serve_forever()
