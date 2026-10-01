#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DGX 仙工 AGV 底盘 HTTP 服务 v1.24（v1.23 + 移除2秒定时器限制）

核心设计（Leo 2026-09-08 12:57 指示）：
- B 方案：APP 按下 → start{...}；松开 → stop；服务端后台循环 vel
- 心跳机制：APP 每 500ms 发一次 heartbeat（带 session_id）
  - 漏 2 次（1 秒未收到）→ AUTO STOP（防失联/APP crash/网络中断）
- **v1.24 变更（Leo 2026-09-09 11:17）**：移除单次最长 2 秒限制，APP 按多久就走多久（松开发 stop 才停）
- session_id：APP 重启/换设备时，旧命令失效（防旧命令继续生效）

API:
  GET  /health                       - 健康检查（含心跳/单次时长 watchdog 状态）
  POST /chassis/query                - 查询底盘状态
  POST /chassis/heartbeat            - 心跳（必带 session_id），每 500ms 一次
  POST /chassis/start                - 开始持续运动 {vx, omega, session_id}
  POST /chassis/stop                 - 停止 {session_id}
  POST /chassis/vel                  - 单次 vel（保留兼容，v1.22 APP 不调用）
"""
import sys
import os
import json
import time
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 导入 SDK 路径
sys.path.insert(0, os.environ.get('DGX_SDK_DIR', '/data/linglong-project/sdk'))

from linglong_h_sdk import LinglongHSdkClass  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger('chassis_server')

# ============ 全局配置 ============
CHASSIS_IP = os.environ.get('AGV_HOST', os.environ.get('AGV_HOST', '192.168.1.204'))
CHASSIS_PORT = 19204
MAX_LINEAR = 0.1      # m/s（硬限速）
MAX_ANGULAR = 0.1     # rad/s（硬限速）

# ============ v1.22 新增：心跳 + 单次时长 watchdog ============
HEARTBEAT_INTERVAL = 0.5       # APP 心跳间隔（秒）
HEARTBEAT_TIMEOUT = 1.0        # 漏 2 次（1 秒未收到）→ AUTO STOP（Leo 2026-09-08 12:57）
MAX_START_DURATION = 86400.0   # 单次 start 最长 24 小时（v1.24 移除 2 秒限制，Leo 2026-09-09 11:17）。心跳 watchdog 仍是真正保险
VEL_LOOP_INTERVAL = 0.05       # 后台 vel 循环间隔（20Hz）
WATCHDOG_CHECK_INTERVAL = 0.1  # watchdog 线程检查频率
TCP_DISCONNECT_TIMEOUT = 5.0   # (decision recorded in git log)

# ============ 全局 SDK 实例 + 线程锁 ============
sdk_lock = threading.Lock()
sdk = None

# ============ v1.22 新增：状态变量 ============
state_lock = threading.Lock()
current_session_id = None       # 当前会话 ID（None 表示无活动）
current_vx = 0.0                # 当前下发速度
current_omega = 0.0
start_time = 0.0                # 最近一次 start 的时间戳
active = False                  # 是否在持续 vel 模式
last_heartbeat_time = 0.0       # 最后收到心跳的时间戳
heartbeat_session_id = None
voice_deadline = 0.0         # (decision recorded in git log)
last_tcp_success_time = 0.0   # (decision recorded in git log)
action_stop_timer = 0.0      # (decision recorded in git log)


def init_sdk():
    """初始化 SDK + 连接到车"""
    global sdk
    try:
        sdk = LinglongHSdkClass(CHASSIS_IP, chassis_tcp_on_send=True)
        sdk.configure_chassis_tcp(CHASSIS_IP, 19205, timeout_s=0.1)
        sdk.configure_chassis_state_tcp(CHASSIS_IP, 19204, timeout_s=5.0)
        logger.info(f'SDK initialized, chassis cmd={CHASSIS_IP}:19205 state={CHASSIS_IP}:19204')
        return True
    except Exception as e:
        logger.error(f'SDK init failed: {e}')
        return False


def clamp_speed(vx, omega):
    """硬限速 ±MAX_LINEAR / ±MAX_ANGULAR（双层保护）"""
    vx = max(-MAX_LINEAR, min(MAX_LINEAR, vx))
    omega = max(-MAX_ANGULAR, min(MAX_ANGULAR, omega))
    return vx, omega


def _do_set_vel_locked(vx, omega):
    """内部函数：调用方必须已持有 sdk_lock

    v1.22 修复：用 send_chassis_command 直接发 TCP（绕过 UDP 链路）
    原因：v1.21 测过 set_base_vel 不动，send_chassis_command 能动 0.12 m/s
    """
    global last_tcp_success_time
    # v1.23 阿克曼底盘反向修正（Leo 2026-09-09 10:53 物理分析）：
    # 前进时轮左转→底盘左移；后退时轮左转→底盘右移。
    # APP 发 omega（基于轮子转向直觉），后退时需要反转 rotation 让底盘正确
    rotation_sent = -omega if vx < 0 else omega
    n_sent = sdk.send_chassis_command(translation=vx, rotation=rotation_sent)
    # (decision recorded in git log)
    last_tcp_success_time = time.time()
    # v1.21 调试日志：发完 50ms 后 query 看 AGV 实际速度
    import time as _t
    _t.sleep(0.05)
    try:
        sp = sdk.query_chassis_speed_state()
        logger.info(f"vel: vx={vx:.3f} omega={omega:.3f} sent={n_sent}B → AGV actual: {sp}")
    except Exception as qe:
        logger.warning(f"vel query fail: {qe}")


def _do_stop_locked():
    """内部函数：调用方必须已持有 sdk_lock，立即发一次 stop"""
    global last_tcp_success_time
    n_sent = sdk.send_chassis_command(translation=0.0, rotation=0.0)
    # (decision recorded in git log)
    last_tcp_success_time = time.time()
    import time as _t
    _t.sleep(0.05)
    try:
        sp = sdk.query_chassis_speed_state()
        logger.info(f"STOP sent={n_sent}B → AGV actual: {sp}")
    except Exception as qe:
        logger.warning(f"stop query fail: {qe}")


# ============ v1.22 新增：start / stop / heartbeat 处理 ============
def do_heartbeat(session_id):
    """处理心跳：刷新 last_heartbeat_time + session_id"""
    global last_heartbeat_time, heartbeat_session_id
    with state_lock:
        # 检测 session_id 变化（APP 重启）
        if heartbeat_session_id is not None and heartbeat_session_id != session_id:
            logger.warning(f'session changed: {heartbeat_session_id[:8]} → {session_id[:8]}, AUTO STOP')
            # (decision recorded in git log)
        heartbeat_session_id = session_id
        last_heartbeat_time = time.time()
    return {'status': 'ok', 'session_id': session_id}


def do_start(vx, omega, session_id, duration=None):
    """开始持续运动：记录 vx/omega/start_time/active"""
    global current_vx, current_omega, start_time, active, current_session_id
    vx, omega = clamp_speed(vx, omega)
    with state_lock:
        # 如果是不同 session 启动，自动停掉旧的
        if current_session_id is not None and current_session_id != session_id:
            logger.info(f'session handover: {current_session_id[:8]} -> {session_id[:8]} (no AUTO STOP)')
        current_session_id = session_id
        current_vx = vx
        current_omega = omega
        start_time = time.time()
        active = True
        # 第一次立即下发 vel（不等 vel_loop）
        with sdk_lock:
            try:
                _do_set_vel_locked(vx, omega)
            except Exception as e:
                logger.error(f'start: first set_vel failed: {e}')
        # (decision recorded in git log)
        # 原因: voice 路径不发 heartbeat (只 start+stop), watchdog 看 last_heartbeat_time
        # 不重置 → 立即 AUTO STOP. APP 路径也受益 (touchstart 时 watchdog 重置)
        global last_heartbeat_time
        last_heartbeat_time = time.time()
        # (decision recorded in git log)
        # duration 是 action_router 算出的矢量执行时间（distance / linear_vel 或 angle / angular_vel）
        # 归0 → watchdog_loop 触发 AUTO STOP
        # 任何 /chassis/stop 调用都会立即把 timer 归0（见 do_stop）
        global action_stop_timer
        if duration is not None and duration > 0:
            action_stop_timer = time.time() + duration
            logger.info(f'ACTION STOP TIMER set: deadline={action_stop_timer:.3f} (now+{duration:.3f}s)')
        else:
            # 没传 duration → 不设定时器（APP 路径靠 heartbeat watchdog）
            action_stop_timer = 0.0
        logger.info(f'START session={session_id[:8]} vx={vx:.3f} omega={omega:.3f}')
    return {'status': 'ok', 'vx': vx, 'omega': omega, 'max_duration': MAX_START_DURATION}


def do_stop(session_id=None):
    global voice_deadline, action_stop_timer
    voice_deadline = 0.0
    # (decision recorded in git log)
    # 防止 race condition (timer 还没到, stop 已来, 之后 timer 误触发)
    action_stop_timer = 0.0
    """停止：清空 active + 立即发一次 stop"""
    with state_lock:
        _internal_stop_locked('explicit_stop', session_id)
    return {'status': 'ok', 'vx': 0.0, 'omega': 0.0}


def _internal_stop_locked(reason, session_id=None):
    """内部 stop（调用方必须已持有 state_lock），用于 watchdog 自动停止"""
    global active, current_vx, current_omega, current_session_id
    if not active:
        return
    active = False
    old_session = current_session_id
    current_vx = 0.0
    current_omega = 0.0
    current_session_id = None
    with sdk_lock:
        try:
            _do_stop_locked()
        except Exception as e:
            logger.error(f'internal stop: set_vel failed: {e}')
    logger.warning(f'AUTO STOP reason={reason} session={old_session[:8] if old_session else "?"} requested={session_id[:8] if session_id else "?"}')


def do_vel_once(vx, omega):
    """单次 vel（兼容接口，v1.22 APP 不调用，但保留）"""
    vx, omega = clamp_speed(vx, omega)
    with sdk_lock:
        try:
            _do_set_vel_locked(vx, omega)
            logger.info(f'vel once: vx={vx:.3f} omega={omega:.3f}')
            return {'status': 'ok', 'vx': vx, 'omega': omega}
        except Exception as e:
            logger.error(f'vel once failed: {e}')
            return {'status': 'error', 'msg': str(e)}


def do_query():
    """查询底盘状态"""
    with sdk_lock:
        try:
            speed_state = sdk.query_chassis_speed_state()
            battery = sdk.query_chassis_battery_level(simple=True)
            return {
                'status': 'ok',
                'translation': float(speed_state[0]) if speed_state else 0.0,
                'rotation': float(speed_state[1]) if speed_state else 0.0,
                'battery': float(battery) if battery is not None else None,
            }
        except Exception as e:
            logger.error(f'query failed: {e}')
            return {'status': 'error', 'msg': str(e)}


# ============ v1.22 新增：后台 vel 循环线程 ============
def vel_loop():
    """
    当 active=True 时，持续每 50ms 下发一次 vel。
    watchdog 在另一个线程检查心跳和单次时长。
    """
    logger.info(f'vel_loop started: interval={VEL_LOOP_INTERVAL}s')
    while True:
        time.sleep(VEL_LOOP_INTERVAL)
        with state_lock:
            if not active:
                continue
            vx, omega = current_vx, current_omega
        # 在锁外发 SDK 调用（减少持锁时间）
        with sdk_lock:
            try:
                _do_set_vel_locked(vx, omega)
            except Exception as e:
                logger.error(f'vel_loop set_vel failed: {e}')


# ============ v1.22 新增：watchdog 后台线程 ============
def watchdog_loop():
    """
    两个独立 watchdog：
    1. 心跳 watchdog：last_heartbeat 超过 HEARTBEAT_TIMEOUT（1秒）→ AUTO STOP（v1.24 唯一保险）
    2. 单次时长 watchdog：start 超过 MAX_START_DURATION（24小时兜底）→ AUTO STOP（v1.24 几乎不会触发）
    """
    global last_heartbeat_time, action_stop_timer, voice_deadline, last_tcp_success_time
    logger.info(f'watchdog started: heartbeat_timeout={HEARTBEAT_TIMEOUT}s max_duration=24h tcp_disconnect=5s (Leo 2026-09-24 10:20) action_stop_timer=action_router (Leo 2026-09-24 11:29)')
    while True:
        time.sleep(WATCHDOG_CHECK_INTERVAL)
        with state_lock:
            if not active:
                continue
            now = time.time()
            # (decision recorded in git log)
            # 优先级最高：在所有 duration 检查之前先验证 TCP 是否还活着
            if last_tcp_success_time > 0:
                tcp_age = now - last_tcp_success_time
                if tcp_age > TCP_DISCONNECT_TIMEOUT:
                    logger.warning(f'TCP disconnect {TCP_DISCONNECT_TIMEOUT}s ({tcp_age:.3f}s) → AUTO STOP')
                    _internal_stop_locked('tcp_disconnect_5s')
                    continue
            # (decision recorded in git log)
            # duration 是 action_router 算出的矢量执行时间, 归0 触发 stop
            if action_stop_timer > 0 and now >= action_stop_timer:
                logger.warning(f'action stop timer EXPIRED (timer={action_stop_timer:.3f}, now={now:.3f}) → AUTO STOP')
                action_stop_timer = 0.0
                _internal_stop_locked('action_stop_timer_expired')
                continue
            # (decision recorded in git log)
            if voice_deadline > 0 and now >= voice_deadline:
                logger.warning(f'voice duration complete, AUTO STOP (deadline={voice_deadline:.3f}, now={now:.3f})')
                voice_deadline = 0.0
                _internal_stop_locked('voice_duration_complete')
                continue
            # (decision recorded in git log)
            # voice 路径有 voice_deadline 或 action_stop_timer 接管 → 跳过 heartbeat 检查
            # Leo 9/22 17:30 拍板原则: heartbeat 是 APP 安全网, voice 路径不需要
            if action_stop_timer == 0 and voice_deadline == 0:
                if last_heartbeat_time > 0:
                    hb_age = now - last_heartbeat_time
                    if hb_age > HEARTBEAT_TIMEOUT:
                        logger.warning(f'heartbeat TIMEOUT ({hb_age:.3f}s > {HEARTBEAT_TIMEOUT}s) → AUTO STOP')
                        _internal_stop_locked('heartbeat_timeout')
                        continue
            # 单次时长 watchdog
            duration = now - start_time
            if duration > MAX_START_DURATION:
                logger.warning(f'max duration EXCEEDED ({duration:.3f}s > {MAX_START_DURATION}s) → AUTO STOP')
                _internal_stop_locked('max_duration_exceeded')


# ============ HTTP Handler ============
class ChassisHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        logger.info(f'HTTP {fmt % args}')

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
            with state_lock:
                hb_age = (time.time() - last_heartbeat_time) if last_heartbeat_time > 0 else None
                tcp_age = (time.time() - last_tcp_success_time) if last_tcp_success_time > 0 else None
                self._send_json({
                    'status': 'ok',
                    'service': 'chassis',
                    'sdk_loaded': sdk is not None,
                    'active': active,
                    'current_session': current_session_id[:8] if current_session_id else None,
                    'heartbeat_age_s': round(hb_age, 3) if hb_age is not None else None,
                    'tcp_age_s': round(tcp_age, 3) if tcp_age is not None else None,
                    'last_vel': {'vx': current_vx, 'omega': current_omega},
                    'config': {
                        'heartbeat_interval_s': HEARTBEAT_INTERVAL,
                        'heartbeat_timeout_s': HEARTBEAT_TIMEOUT,
                        'max_start_duration_s': MAX_START_DURATION,
                        'vel_loop_interval_s': VEL_LOOP_INTERVAL,
                        'tcp_disconnect_timeout_s': TCP_DISCONNECT_TIMEOUT,
                    }
                })
        else:
            self._send_json({'status': 'error', 'msg': 'not found'}, status=404)

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8') if length else '{}'
        try:
            req = json.loads(body) if body.strip() else {}
        except json.JSONDecodeError:
            self._send_json({'status': 'error', 'msg': 'invalid json'}, status=400)
            return

        if self.path == '/chassis/query':
            result = do_query()
            self._send_json(result)
        elif self.path == '/chassis/heartbeat':
            sid = req.get('session_id', '')
            if not sid:
                self._send_json({'status': 'error', 'msg': 'session_id required'}, status=400)
                return
            result = do_heartbeat(sid)
            self._send_json(result)
        elif self.path == '/chassis/start':
            sid = req.get('session_id', '')
            if not sid:
                self._send_json({'status': 'error', 'msg': 'session_id required'}, status=400)
                return
            vx = float(req.get('vx', 0.0))
            omega = float(req.get('omega', 0.0))
            # (decision recorded in git log)
            duration = req.get('duration', None)
            if duration is not None:
                duration = float(duration)
            result = do_start(vx, omega, sid, duration=duration)
            self._send_json(result)
        elif self.path == '/chassis/stop':
            sid = req.get('session_id', None)
            result = do_stop(sid)
            self._send_json(result)
        elif self.path == '/chassis/vel':
            # 兼容接口
            vx = float(req.get('vx', 0.0))
            omega = float(req.get('omega', 0.0))
            result = do_vel_once(vx, omega)
            self._send_json(result)
        else:
            self._send_json({'status': 'error', 'msg': 'not found'}, status=404)


def main():
    parser_args = sys.argv[1:]
    port = 7781
    for arg in parser_args:
        if arg.startswith('--port='):
            port = int(arg.split('=')[1])

    # 初始化 SDK
    if not init_sdk():
        logger.warning('SDK init failed, server will run anyway (queries will error)')

    # 启动后台线程
    vel_thread = threading.Thread(target=vel_loop, daemon=True, name='chassis-vel-loop')
    vel_thread.start()
    watchdog_thread = threading.Thread(target=watchdog_loop, daemon=True, name='chassis-watchdog')
    watchdog_thread.start()

    # 启动 HTTP
    srv = ThreadingHTTPServer(('0.0.0.0', port), ChassisHandler)
    logger.info(f'Chassis HTTP server v1.22: 0.0.0.0:{port} (B+heartbeat+max_duration enabled)')
    print(f'>>> Chassis service v1.22 ready on :{port}')
    print(f'>>> Chassis IP: {CHASSIS_IP}:{CHASSIS_PORT}')
    print(f'>>> Speed limits: linear ±{MAX_LINEAR} m/s, angular ±{MAX_ANGULAR} rad/s')
    print(f'>>> Heartbeat: interval={HEARTBEAT_INTERVAL}s timeout={HEARTBEAT_TIMEOUT}s')
    print(f'>>> Max start duration: {MAX_START_DURATION}s (v1.24: 24h 兜底)')
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        logger.info('stopped')


if __name__ == '__main__':
    main()
