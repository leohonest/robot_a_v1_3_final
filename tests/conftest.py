# -*- coding: utf-8 -*-
"""pytest 配置：stub 掉 requests（action_router 顶层 import），并把 dgx_scripts 加入路径。"""
import pathlib
import sys
import types

try:
    import requests  # noqa: F401
except ImportError:
    _m = types.ModuleType("requests")
    _m.post = lambda *a, **k: None
    sys.modules["requests"] = _m

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "dgx_scripts"))
