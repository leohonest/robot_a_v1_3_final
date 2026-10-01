#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包前检查闸门：语法编译 + 敏感信息扫描 + 违禁文件。任一 FAIL 退出码 1。

用法：python tools/prepackage_check.py （在仓库根目录执行）
"""
import pathlib
import py_compile
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

SECRET_PATTERNS = [
    (r"sshpass\s+-p\s+(?![\$\{])\S+", "SSH 密码明文（应使用 $LINGLONG_PASSWORD / {LINGLONG_PASS}）"),
    (r"sk-[A-Za-z0-9]{16,}", "疑似真实 API Key"),
    (r"(?i)(app_?secret|api_?key|password)\s*[=:]\s*['\"][A-Za-z0-9@#%^&*_-]{8,}['\"]", "疑似硬编码凭据"),
    (r"Sziit\d*|dfet2025|yffSm3|b0cf9e1e", "已知泄漏凭据指纹"),
]

FORBIDDEN_NAME_PATTERNS = [
    r"^logs/",
    r"\.bak",
    r"linglong_voice_v3\.py\.(scripts_old|windows)",
    r"\.apk$",
    r"memory_session_",
]


def main() -> int:
    failed = False

    # 1) 语法编译
    for p in sorted(ROOT.rglob("*.py")):
        if any(part in {".git", "__pycache__", "node_modules"} for part in p.parts):
            continue
        try:
            py_compile.compile(str(p), doraise=True)
            print(f"[PASS] syntax {p.relative_to(ROOT)}")
        except Exception as e:
            failed = True
            print(f"[FAIL] syntax {p.relative_to(ROOT)}: {e}")

    # 2) 敏感信息
    checker = pathlib.Path(__file__).resolve()
    text_files = [p for p in ROOT.rglob("*") if p.suffix.lower() in
                  {".py", ".md", ".yaml", ".yml", ".txt", ".sh", ".bat", ".ps1", ".example", ".html"}
                  and p.resolve() != checker]  # 检查器自身含指纹正则，跳过
    for p in text_files:
        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pat, desc in SECRET_PATTERNS:
            for m in re.finditer(pat, content):
                failed = True
                print(f"[FAIL] secret {p.relative_to(ROOT)}: {desc}: {m.group(0)[:24]}...")

    # 3) 违禁文件名
    for p in ROOT.rglob("*"):
        rel = p.relative_to(ROOT).as_posix()
        for pat in FORBIDDEN_NAME_PATTERNS:
            if re.search(pat, rel):
                failed = True
                print(f"[FAIL] forbidden file: {rel} (pattern {pat})")

    if failed:
        print("\n== 检查未通过，禁止打包 ==")
        return 1
    print("\n== 全部检查通过，可以打包 ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
