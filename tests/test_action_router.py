# -*- coding: utf-8 -*-
"""action_router 关键安全行为测试（V1.3 回归防线）

运行：python -m pytest tests/ -v   或   python tests/test_action_router.py
"""
import os
import pathlib
import subprocess
import sys

try:
    import requests  # noqa: F401
except ImportError:  # 无 requests 环境下也可运行（action_router 仅网络调用时使用）
    import types as _types
    _m = _types.ModuleType("requests")
    _m.post = lambda *a, **k: None
    sys.modules["requests"] = _m

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "dgx_scripts"))

from action_router import route, fuzzy_match, chinese_to_arabic, extract_params


def test_xiashineng_is_disable_not_enable():
    """P1-1 回归：'下使能' 绝不能命中 enable（历史上曾反向触发上使能）。"""
    r = route("下使能")
    assert r["matched"] and r["action_id"] == "disable", r


def test_shangshineng_is_enable():
    r = route("上使能")
    assert r["matched"] and r["action_id"] == "enable", r


def test_stop_keyword():
    r = route("停")
    assert r["matched"] and r["action_id"] == "chassis_stop", r


def test_distance_clamped():
    """P1-4 回归：'前进999米' 必须被 clamp 到上限。"""
    r = route("前进999米")
    assert r["matched"] and r["action_id"] == "chassis_fwd", r
    assert r["params"]["distance_m"] <= 2.0, r["params"]
    assert r["params"]["duration_s"] <= 60.0, r["params"]


def test_angle_clamped():
    r = route("右转720度")
    assert r["matched"] and r["action_id"] == "chassis_right", r
    assert r["params"]["angle_deg"] <= 180.0, r["params"]


def test_nav_no_false_positive():
    """P1-3 回归：日常对话不能被导航关键词劫持。"""
    for q in ["去海边玩", "回去吧", "我想去上学", "去年发生了什么"]:
        r = route(q)
        assert not r["matched"], f"'{q}' 不应命中动作: {r}"


def test_nav_to_station():
    r = route("去门口")
    assert r["matched"] and r["action_id"] == "nav_to_station", r
    assert r["params"]["id"] == "LM5", r["params"]


def test_nav_station_to_station():
    r = route("从工位1去门口")
    assert r["matched"] and r["action_id"] == "nav_station_to_station", r
    assert r["params"]["source_id"] == "LM3" and r["params"]["id"] == "LM5", r["params"]


def test_chinese_to_arabic():
    assert chinese_to_arabic("九百九十九点九") == "999.9"
    assert chinese_to_arabic("前进零点三米") == "前进0.3米"
    assert chinese_to_arabic("左转四十五度") == "左转45度"


def test_fuzzy_no_match_for_greeting():
    action, params = fuzzy_match("你好")
    assert action is None


def test_dryrun_gate():
    """P1-2 回归：ACTION_ROUTER_DRYRUN=1 时不发真实指令。"""
    env = dict(os.environ, ACTION_ROUTER_DRYRUN="1")
    code = (
        "import types,sys as s; _m=types.ModuleType('requests'); _m.post=lambda *a,**k:None; s.modules['requests']=_m; s.path.insert(0, r'%s');"
        "from action_router import route;"
        "r = route('前进0.3米');"
        "assert r['matched'] and r['result']['status'] == 'dryrun', r;"
        "print('DRYRUN OK')"
    ) % os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dgx_scripts")
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr or out.stdout
    assert "DRYRUN OK" in out.stdout


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"[PASS] {name}")
            except AssertionError as e:
                fails += 1
                print(f"[FAIL] {name}: {e}")
    sys.exit(1 if fails else 0)
