"""
灵龙动作路由 action_router.py
================================
关键词模糊匹配 → 直调灵龙 SDK / 底盘服务端（不走大模型）

词典来源（2026-09-21 Leo 拍板）：
- 上身：上使能、下使能、归位、松左手、握左手、松右手、握右手
- 底盘：前进 N 米、后退 N 米、左转 N 度、右转 N 度、停
- 导航：从工位1去门口 / 从门口回到工位1（3051 路径导航）

部署：DGX `/data/linglong-project/scripts/action_router.py`
HTTP API: :7793/action {query: "..."} → {action_id, target, params, status}

不依赖 LLM，关键词命中直接调服务端 → 省 token + 实时
"""
import base64
import os, re
import json
import uuid
import time
import requests
import threading
import socket
import struct
import logging
from typing import Optional, Dict, Any, Tuple

logger = logging.getLogger('action_router')

# 灵龙硬件 IP（参考 SOUL.md + linglong-chassis-ackermann-SKILL.md）
ARM_IP = 'os.environ.get("LINGLONG_SDK_HOST", "192.168.1.28")'           # 手臂控制器
ARM_MODE_PORT = 4141               # robot_enable_up/down
ARM_SDK_PORT = 3333                # set_end_pose / set_cap
CHASSIS_IP = 'os.environ.get("AGV_HOST", "192.168.1.204")'       # 底盘 Ackermann（灵龙）
CHASSIS_CMD_PORT = int(os.environ.get("AGV_PORT_CMD", "19205"))            # send_chassis_command
CHASSIS_HTTP = f"http://{os.environ.get('DGX_HOST', '10.86.51.122')}:7781"  # chassis_server HTTP API（DGX）
TTS_HTTP = f"http://{os.environ.get('DGX_HOST', '10.86.51.122')}:9003"       # TTS VoiceDesign（DGX）

# 底盘速度（Leo 2026-09-22 06:54：限速就很好，保持 0.1 m/s）
LINEAR_VEL = 0.1   # m/s（前进/后退）
ANGULAR_VEL = 0.1  # rad/s（左/右转）≈ 5.73°/s

# 矢量默认值（Leo 2026-09-22 17:00 拍板）
DEFAULT_DISTANCE_M = 0.3  # 前进后退默认 0.3 米
DEFAULT_ANGLE_DEG = 45.0  # 左转右转默认 45 度

# 语音指令安全上限（ASR 误识别大数时按上限执行，可用环境变量调整）
MAX_DISTANCE_M = float(os.environ.get('ACTION_MAX_DISTANCE_M', '2.0'))
MAX_ANGLE_DEG_LIMIT = float(os.environ.get('ACTION_MAX_ANGLE_DEG', '180.0'))
MAX_DURATION_S = float(os.environ.get('ACTION_MAX_DURATION_S', '60.0'))

# dryrun 开关（Leo 2026-09-22 17:00 进 real 模式）
ACTION_ROUTER_DRYRUN = os.environ.get('ACTION_ROUTER_DRYRUN', '0') == '1'
# 默认 0 = real mode（Leo 17:00 拍板：灵龙已下电，safe to switch）
# 历史：1 = dryrun（占位）

# voice_cmd_server HTTP（arm 动作转发目标）
VOICE_CMD_HTTP = f"http://{os.environ.get('DGX_HOST', '10.86.51.122')}:7778"

logger_dryrun = logging.getLogger('action_router.dryrun')

# ===== 站点名映射（Leo 2026-09-22 06:34）=====
# 工位1 → LM3, 门口 → LM5
STATION_MAP = {
    '工位1': 'LM3',
    '工位一': 'LM3',
    '工位2': 'LM4',
    '工位二': 'LM4',
    '门口': 'LM5',
}

# AGV nav 端口 (3051 路径导航)
CHASSIS_NAV_PORT = int(os.environ.get("AGV_PORT_NAV", "19206"))
CMD_3051 = 3051

# ===== 动作字典（关键词 + 解析规则）=====
ACTION_DICT = [
    # ---- 上身 7 动作 ----
    {
        'id': 'enable',
        'keywords': [
            '上使能', '上屎能', '上时能', '上示能', '上台能',
            '启动', '上电', 'enable',
            '干活', '准备干活', '开工', '开始干活', '开机', '醒醒', '上工',
        ],
        'target': 'arm',
        'cmd': 'robot_enable_up',
    },
    {
        'id': 'disable',
        'keywords': [
            '下使能', '下屎能', '下时能', '下示能', '下台能',
            '关闭', '断电', 'disable',
            '下班', '休息', '关机', '停止工作', '收工', '回家', '下工',
        ],
        'target': 'arm',
        'cmd': 'robot_enable_down',
    },
    {
        'id': 'home',
        'keywords': [
            '归位', '贵位', '鬼位', '规位', '闺位',
            '复位', '回原', 'home', 'reset', '回初始',
        ],
        'target': 'arm',
        'cmd': 'set_end_pose',
        'args': {
            'left': (0.30, 0.25, 0.65),
            'right': (0.30, -0.25, 0.65),
        },
    },
    {
        'id': 'grip_L_open',
        'keywords': ['松左手', '左手松', '松开左手', 'open_left', 'open left', '左手张开'],
        'target': 'arm',
        'cmd': 'gripper_cap',
        'args': {'hand': 'L', 'value': 0.0},
    },
    {
        'id': 'grip_L_close',
        'keywords': ['握左手', '左手握', '握住左手', 'close_left', 'close left', '左手抓住', '抓左手'],
        'target': 'arm',
        'cmd': 'gripper_cap',
        'args': {'hand': 'L', 'value': 1.0},
    },
    {
        'id': 'grip_R_open',
        'keywords': ['松右手', '右手松', '松开右手', 'open_right', 'open right', '右手张开'],
        'target': 'arm',
        'cmd': 'gripper_cap',
        'args': {'hand': 'R', 'value': 0.0},
    },
    {
        'id': 'grip_R_close',
        'keywords': ['握右手', '右手握', '握住右手', 'close_right', 'close right', '右手抓住', '抓右手'],
        'target': 'arm',
        'cmd': 'gripper_cap',
        'args': {'hand': 'R', 'value': 1.0},
    },
    # ---- 底盘 5 动作（矢量运动，Leo 2026-09-22 06:47）----
    {
        'id': 'chassis_fwd',
        'keywords': ['前进', '前近', '钱进', '浅进', '往前走', '直走', '向前'],
        'target': 'chassis',
        'cmd': 'chassis_pulse',
        'args_template': 'distance',
        'param_pattern': r'(?:前进|前近|钱进|浅进|往前走|直走|向前)\s*(\d+(?:\.\d+)?)\s*(?:米|m|公尺)?',
        'direction': +1,
    },
    {
        'id': 'chassis_back',
        'keywords': ['后退', '后推', '吼退', '倒退', '向后'],
        'target': 'chassis',
        'cmd': 'chassis_pulse',
        'args_template': 'distance',
        'param_pattern': r'(?:后退|后推|吼退|倒退|向后)\s*(\d+(?:\.\d+)?)\s*(?:米|m|公尺)?',
        'direction': -1,
    },
    {
        'id': 'chassis_left',
        'keywords': ['左转', '左拐', '左砖', '左转'],
        'target': 'chassis',
        'cmd': 'chassis_pulse',
        'args_template': 'angle',
        'param_pattern': r'(?:左转|左拐|左砖)\s*(\d+(?:\.\d+)?)\s*(?:度|°|degrees?)?',
        'direction': +1,
    },
    {
        'id': 'chassis_right',
        'keywords': ['右转', '右拐', '右砖', '又转'],
        'target': 'chassis',
        'cmd': 'chassis_pulse',
        'args_template': 'angle',
        'param_pattern': r'(?:右转|右拐|右砖|又转)\s*(\d+(?:\.\d+)?)\s*(?:度|°|degrees?)?',
        'direction': -1,
    },
    {
        'id': 'chassis_stop',
        'keywords': ['停', '停止', '刹车', 'stop', 'halt'],
        'target': 'chassis',
        'cmd': 'chassis_stop',
    },
    # ---- 自主导航（站点 ↔ 站点, Leo 2026-09-22 06:34）----
    # 触发词必须是"从X到Y"或"从X去Y"或"从X回Y"格式
    {
        'id': 'nav_station_to_station',
        'keywords': ['从'],
        'target': 'chassis',
        'cmd': 'agv_3051_navigate',
        'station_pattern': r'从\s*(工位\s*[一二三四1-4]+|门口)\s*(?:去|到|回|回到)\s*(工位\s*[一二三四1-4]+|门口)',
    },
    # 触发词是"去Y"或"回Y"（单独目的地）
    {
        'id': 'nav_to_station',
        'keywords': ['去', '回', '回到'],
        'target': 'chassis',
        'cmd': 'agv_3051_navigate',
        # 站点名前面空格可有可无
        'station_pattern': r'(?:去|回|回到)\s*(工位\s*[一二三四1-4]+|门口)',
    },
]


# ===== 参数提取 =====
def extract_params(action: Dict, query: str) -> Dict[str, Any]:
    if action.get('args_template') == 'distance':
        m = re.search(action['param_pattern'], query)
        value = float(m.group(1)) if m else DEFAULT_DISTANCE_M
        value = max(0.0, min(value, MAX_DISTANCE_M))            # P1-4: 距离上限
        duration = min(value / LINEAR_VEL, MAX_DURATION_S)      # P1-4: 时长上限
        return {'distance_m': value, 'direction': action.get('direction', +1), 'duration_s': duration}

    if action.get('args_template') == 'angle':
        m = re.search(action['param_pattern'], query)
        value = float(m.group(1)) if m else DEFAULT_ANGLE_DEG
        value = max(0.0, min(value, MAX_ANGLE_DEG_LIMIT))       # P1-4: 角度上限
        deg_per_s = ANGULAR_VEL * 180 / 3.14159
        duration = min(value / deg_per_s, MAX_DURATION_S)
        return {'angle_deg': value, 'direction': action.get('direction', +1), 'duration_s': duration}

    if action.get('station_pattern'):
        m = re.search(action['station_pattern'], query)
        if m:
            groups = m.groups()
            if len(groups) == 2:
                src_cn, tgt_cn = groups[0].strip().replace(' ', ''), groups[1].strip().replace(' ', '')
            else:
                src_cn, tgt_cn = None, groups[0].strip().replace(' ', '')
            src_lm = STATION_MAP.get(src_cn) if src_cn else None
            tgt_lm = STATION_MAP.get(tgt_cn)
            if not tgt_lm:
                return {'unknown_station': tgt_cn}
            return {'source_id': src_lm, 'id': tgt_lm, 'task_id': f"voice_nav_{int(time.time()*1000)}"}
        return {}
    return {}


# ===== 3051 导航 =====
def build_3051_message(params: Dict) -> Dict[str, Any]:
    msg = {'id': params['id'], 'task_id': params['task_id']}
    if params.get('source_id'):
        msg['source_id'] = params['source_id']
    return msg


def send_3051_to_agv(message: Dict[str, Any], number: int = 1) -> Dict[str, Any]:
    """直接 TCP 发 3051 nav 命令到 AGV :19206"""
    try:
        body = json.dumps(message, ensure_ascii=False).encode('utf-8')
        # 16 字节头: 0x5A 0x01 [number uint16] [length uint32] [cmd_id uint16] [6 字节 reserved]
        header = struct.pack('>BBHIH', 0x5A, 0x01, number, len(body), CMD_3051) + b'\x00' * 6
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        sock.connect((CHASSIS_IP, CHASSIS_NAV_PORT))
        sock.send(header + body)
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
            # P2-4: log instead of silent pass
            logging.exception("caught error", exc_info=True)
            pass
        sock.close()
        if len(resp) >= 16:
            r_len = struct.unpack('>I', resp[4:8])[0]
            if r_len > 0 and len(resp) >= 16 + r_len:
                try:
                    return json.loads(resp[16:16+r_len])
                except:
                    return {'raw': resp[16:16+r_len].decode('utf-8', errors='replace')}
        return {'ret_code': -1, 'msg': 'no response'}
    except Exception as e:
        return {'ret_code': -1, 'msg': str(e)}


# ===== 底盘脉冲运动（模拟 APP 遥杆，Leo 2026-09-22 06:47）====
# 核心：chassis_server v1.22 的 start 需要每 500ms 发送一次 heartbeat
# 实现：发 /chassis/start → 起后台线程每 500ms heartbeat → 等 duration → 发 /chassis/stop

_SHOUDAO_PATH = os.environ.get('SHOUDAO_WAV', '/data/linglong-project/sounds/shoudao.wav')
_shoudao_cache = None


def say_shoudao() -> Optional[str]:
    """加载预录的"收到"音频并缓存（base64）。失败返回 None，不阻塞动作。"""
    global _shoudao_cache
    if _shoudao_cache is not None:
        return _shoudao_cache or None
    try:
        with open(_SHOUDAO_PATH, 'rb') as f:
            _shoudao_cache = base64.b64encode(f.read()).decode('ascii')
    except Exception as e:
        logger.warning(f'load shoudao.wav failed: {e}')
        _shoudao_cache = ''
    return _shoudao_cache or None


def chassis_pulse(action: Dict, params: Dict) -> Dict[str, Any]:
    """
    实现矢量运动 (Leo 2026-09-23 17:30 拍板 UDP 风格):
    - 发 start {vx, omega, duration}
    - chassis_server 内部 timer 到时间 AUTO STOP
    - 我们不后台线程, 不 sleep, 不 heartbeat
    - chassis_server 用 voice_deadline + watchdog 检查 (跟 APP heartbeat 机制解耦)
    """
    import requests as _req
    session_id = f'voice_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}'
    duration_s = params.get('duration_s', 1.0)
    direction = params.get('direction', +1)

    if action['id'] in ('chassis_fwd', 'chassis_back'):
        vx = direction * LINEAR_VEL
        omega = 0.0
    else:  # left / right
        vx = 0.0
        omega = direction * ANGULAR_VEL

    # TTS "收到" (同步, 立即返回)
    audio_b64 = say_shoudao()

    # 发 start 带 duration_s (让 chassis_server 设 voice_deadline)
    try:
        r = _req.post(f'{CHASSIS_HTTP}/chassis/start', json={
            'vx': vx, 'omega': omega, 'session_id': session_id, 'duration': duration_s
        }, timeout=2)
        if not r.ok:
            return {'status': 'http_error', 'msg': f'start failed: {r.status_code}'}
    except Exception as e:
        return {'status': 'error', 'msg': f'start except: {e}'}

    # 立即返回 (chassis_server 自己 timer 到时间)
    return {
        'status': 'ok',
        'action_id': action['id'],
        'vx': vx, 'omega': omega,
        'duration_s': round(duration_s, 2),
        'session_id': session_id,
        'audio_b64': audio_b64,
    }


def chassis_stop() -> Dict[str, Any]:
    """发 stop，清所有活跃 session"""
    session_id = f'voice_stop_{int(time.time()*1000)}'
    try:
        r = requests.post(f'{CHASSIS_HTTP}/chassis/stop', json={'session_id': session_id}, timeout=3)
        return {'status': 'ok' if r.ok else 'http_error', 'response': r.text[:100], 'audio_b64': say_shoudao()}
    except Exception as e:
        return {'status': 'error', 'msg': str(e), 'audio_b64': say_shoudao()}


# ===== 中文数字 → 阿拉伯数字（Leo 2026-09-24 12:51 拍板）=====
# 支持到百位（含小数点）：九百九十九点九 → 999.9
# ASR（Qwen3-ASR 0.6B）默认输出中文数字（"五"），不修改 ASR；
# 改在 query 预处理层做转换。
_CN_SINGLE = {'零': '0', '一': '1', '二': '2', '三': '3', '四': '4',
              '五': '5', '六': '6', '七': '7', '八': '8', '九': '9',
              '两': '2'}


def _parse_cn_int(s: str):
    """解析中文数字字符串到 int。支持到百位。
    规则：
    - 单字：零=0, 一=1, ..., 九=9
    - 十位："X十" = X*10（X 默认 1："十"=10, "十五"=15）
    - 百位："X百" = X*100
    - 复合：X百Y十Z = X*100 + Y*10 + Z（如"九百九十九"=999）
    """
    if not s:
        return None
    converted = ''.join(_CN_SINGLE.get(c, c) for c in s)
    if '百' in converted:
        parts = converted.split('百', 1)
        h = int(parts[0]) if parts[0] else 0
        rest = parts[1]
        t, u = 0, 0
        if '十' in rest:
            t_parts = rest.split('十', 1)
            t = int(t_parts[0]) if t_parts[0] else 1
            if t_parts[1]:
                u = int(t_parts[1])
        else:
            if rest:
                u = int(rest)
        return h * 100 + t * 10 + u
    elif '十' in converted:
        parts = converted.split('十', 1)
        t = int(parts[0]) if parts[0] else 1
        u = int(parts[1]) if parts[1] else 0
        return t * 10 + u
    else:
        if converted.isdigit():
            return int(converted)
        return None


def chinese_to_arabic(query: str) -> str:
    """把 query 里的中文数字替换为阿拉伯数字。支持到百位+小数点。
    例：九百九十九点九 → 999.9；前进零点三米 → 前进0.3米"""
    def parse_num(s):
        if '点' in s:
            int_part, dec_part = s.split('点', 1)
            int_val = _parse_cn_int(int_part)
            dec_str = ''.join(_CN_SINGLE.get(c, c) for c in dec_part)
            if int_val is None or not dec_str.isdigit():
                return None
            return int_val + float('0.' + dec_str)
        return _parse_cn_int(s)

    # 优先匹配百位复合段 → 十位段 → 小数段 → 单词字
    pattern = (
        r'[零一二三四五六七八九两]百(?:[零一二三四五六七八九两百十点])*'
        r'|(?:[零一二三四五六七八九两])?十[零一二三四五六七八九]?(?:点[零一二三四五六七八九]+)?'
        r'|[零一二三四五六七八九两]点[零一二三四五六七八九]+'
        r'|[零一二三四五六七八九两]'
    )

    def repl(m):
        s = m.group(0)
        val = parse_num(s)
        if val is None:
            return s
        return str(int(val)) if val == int(val) else str(val)

    return re.sub(pattern, repl, query)


# ===== 模糊匹配 =====
def fuzzy_match(query: str) -> Tuple[Optional[Dict], Dict[str, Any]]:
    q = chinese_to_arabic(query.strip())
    if not q:
        return None, {}

    def _match(action, kw_check):
        # 导航动作必须整体命中 station_pattern（P1-3: 避免"去海边玩"误触发）
        if action.get('station_pattern'):
            if not re.search(action['station_pattern'], q):
                return None
            params = extract_params(action, q)
            if 'id' not in params:
                return None
            return params
        return extract_params(action, q)

    # 1) 完全子串匹配：动作内关键词按长度降序（P1-1: 避免"下使能"命中裸词"使能"）
    for action in ACTION_DICT:
        for kw in sorted(action['keywords'], key=len, reverse=True):
            if kw in q:
                params = _match(action, True)
                if params is not None:
                    return action, params

    # 2) 模糊匹配（difflib，阈值 0.65）
    from difflib import SequenceMatcher
    best_action = None
    best_score = 0.0
    for action in ACTION_DICT:
        for kw in action['keywords']:
            score = SequenceMatcher(None, q, kw).ratio()
            if score > best_score and score >= 0.65:
                best_score = score
                best_action = action
    if best_action:
        params = _match(best_action, True)
        if params is not None:
            return best_action, params

    return None, {}


# ===== 分发 =====
def dispatch(action: Dict, params: Dict) -> Dict[str, Any]:
    # P1-2: dryrun 模式不下发真实指令（ACTION_ROUTER_DRYRUN=1 启用）
    if ACTION_ROUTER_DRYRUN:
        logger_dryrun.warning(f"[DRYRUN] {action['id']} params={params} -> not dispatched")
        return {'status': 'dryrun', 'action_id': action['id'], 'target': action.get('target'), 'params': params}

    target = action['target']

    if target == 'arm':
        # 真接 voice_cmd_server :7778 (Leo 15:23 拍板)
        try:
            import requests as _r
            # voice_cmd_server 接收 text 字段（不是 cmd）！Leo 15:31 测试发现
            cn_cmd_map = {
                'enable': '上使能',
                'disable': '下使能',
                'home': '归位',
                'grip_L_close': '握紧左手',
                'grip_L_open': '松开左手',
                'grip_R_close': '握紧右手',
                'grip_R_open': '松开右手',
            }
            cn_cmd = cn_cmd_map.get(action['id'], action['id'])
            r = _r.post('http://localhost:7778/cmd', json={
                'text': cn_cmd,  # 用 text 字段！
                'source': 'action_router',
            }, timeout=10)
            result_body = r.json() if r.text else {}
            return {
                'status': 'ok' if r.status_code == 200 else 'error',
                'http_status': r.status_code,
                'voice_cmd_response': result_body,
                'action_id': action['id'],
                'params': params,
                'audio_b64': say_shoudao(),
            }
        except Exception as e:
            return {
                'status': 'error',
                'error': str(e),
                'action_id': action['id'],
                'params': params,
                'audio_b64': say_shoudao(),
            }

    if target == 'chassis':
        cmd = action.get('cmd')

        if cmd == 'chassis_pulse':
            return chassis_pulse(action, params)

        if cmd == 'chassis_stop':
            return chassis_stop()

        if cmd == 'agv_3051_navigate':
            # 自主导航（站点 ↔ 站点）
            if not params or 'unknown_station' in params:
                return {
                    'status': 'error',
                    'msg': f"unknown station: {params.get('unknown_station', '?')}",
                    'known_stations': list(STATION_MAP.keys()),
                }
            message = build_3051_message(params)
            result = send_3051_to_agv(message, number=int(time.time()) & 0xFFFF)
            return {
                'status': 'ok' if result.get('ret_code') == 0 else 'error',
                'cmd_id': 3051,
                'message': message,
                'agv_response': result,
                'audio_b64': say_shoudao(),
            }

    return {'status': 'unknown_target', 'target': target}


def route(query: str) -> Dict[str, Any]:
    """主路由函数"""
    action, params = fuzzy_match(query)
    if action is None:
        return {
            'matched': False,
            'query': query,
            'message': 'no action matched - should fall through to LLM (7792)',
        }

    result = dispatch(action, params)
    return {
        'matched': True,
        'query': query,
        'action_id': action['id'],
        'target': action['target'],
        'cmd': action.get('cmd'),
        'params': params,
        'result': result,
    }


# ===== 测试 =====
if __name__ == '__main__':
    test_queries = [
        # 上身
        '上使能', '下使能', '归位', '松左手', '握左手', '松右手', '握右手',
        # 底盘矢量运动（Leo 2026-09-22 06:47）
        '前进0.3米',
        '前进0.5米',
        '前进',        # 默认 0.3m
        '后退0.2米',
        '左转15度',
        '右转30度',
        '停',
        # 自主导航（Leo 2026-09-22 06:34）
        '去门口',              # → LM5
        '回工位1',             # → LM3
        '从工位1去门口',       # LM3 → LM5
        '从门口回到工位1',     # LM5 → LM3
        '走到门口',            # → LM5
        '去工位三',            # 工位3 → LM1
        '去工位5',             # 未知站 → error
        # 不应匹配
        '你好',
        '今天深圳天气',
    ]
    print('=' * 70)
    print('ACTION ROUTER TEST (矢量运动 v2 + 3051 导航, Leo 2026-09-22)')
    print('=' * 70)
    for q in test_queries:
        result = route(q)
        print(f"\nQ: {q}")
        if result['matched']:
            print(f"  MATCHED: {result['action_id']} → {result['cmd']}")
            print(f"  PARAMS: {result['params']}")
            print(f"  RESULT: {result['result']}")
        else:
            print(f"  NO MATCH → {result['message']}")
