# Orchestrator Module Reload 陷阱 SKILL（Python module 内存陷阱）

> 适用：任何 orchestrator / dispatcher / router 类长期运行的 HTTP 服务
> 目的：避免改了 .py 文件但服务还在跑旧代码
> 创建：2026-09-28（Leo 11:17 拍板方案 A 修复 chassis 角度 bug 时发现）

---

## 1. 血泪教训：9/22 → 9/28 期间 6 天未重启

**事件时间线**：
- 9/22 08:08：orchestrator 启动（PID 2120048）
- 9/22 14:47 ~ 9/24：Leo 在 action_router.py 里加 chassis_pulse + heartbeat 线程
- **9/24 12:51**：Leo 在 action_router.py 加 `chinese_to_arabic` 函数（修复"十五度/五十度/五百度" → 默认15度 bug）
- 9/24 12:52：action_router.py 最后修改
- 9/28 10:49：Leo 测试发现"灵龙左转五百度"实际只转 ~10 度
- 9/28 11:08：orchestrator log 显示 `action_router] query='左转五十度'` 处理了，但 duration=2.62s（默认 15 度）
- 9/28 11:17：Leo 拍板方案 A（kill + 重启 orchestrator）
- 9/28 11:18：新 orchestrator PID 2620071 启动，左转五百度 → duration=87.27s ✅

**根因**：orchestrator 9/22 启动时 `import action_router`，把磁盘上的 action_router.py 加载到 Python 进程内存。9/24 12:51 Leo 改了磁盘上的 .py，但**内存里的旧版本不会自动更新**。Python module 不会 hot reload，必须重启进程。

---

## 2. 三大致命陷阱

### 陷阱 1：Python module 一次性加载

```python
# orchestrator.py 启动时
sys.path.insert(0, '/data/linglong-project/scripts')
import action_router  # ← 加载到内存
```

之后磁盘上 `action_router.py` 改了 → **内存里还是旧版本**。
除非 `importlib.reload(action_router)` 或重启进程。

### 陷阱 2：每个 Python 进程独立加载

```
action_router.py (磁盘 9/24 12:52 修改)

↓ import 复制 ↓
├─ orchestrator.py 内存 (PID 2120048, 9/22 加载, 旧版本)
├─ action_router_server.py 内存 (PID 1533443, 9/24 加载, 新版本)
└─ 其他进程 (各自加载各自版本)
```

**只有重启进程，新代码才生效**。

### 陷阱 3：备份脚本本身可能是旧版本

action_router.py 有 `import os` 或 `import sys`，但**没有 importlib.reload**。
改了 .py 后**必须** kill 进程 + 重启。

---

## 3. 检测方法（怎么知道服务在跑旧代码）

### 方法 1：看磁盘 vs 内存 md5

```bash
# 磁盘文件 md5
ssh kk@10.86.51.122 md5sum /data/linglong-project/scripts/action_router.py

# 进程加载的 module（需要 importlib）
python3 -c "import action_router, hashlib; print(hashlib.md5(open(action_router.__file__,'rb').read()).hexdigest())"
```

如果 md5 不同 → 内存里是旧版本。

### 方法 2：看进程启动时间 vs 文件修改时间

```bash
# 看进程什么时候启动
ps -eo pid,etime,lstart,cmd | grep orchestrator

# 看 .py 文件什么时候修改
ls -la /data/linglong-project/scripts/action_router.py
```

如果 `lstart`（启动时间）早于 .py 修改时间 → 跑的是旧版本。

### 方法 3：功能测试（最直接）

改了 .py → 测核心功能 → 如果行为跟磁盘代码不一致 → 内存是旧的。

例：Leo 9/28 测试左转五百度 → 应该 87 秒 → 实际 2.6 秒 → 立刻知道旧版本在跑。

---

## 4. 重启安全流程（避免重启时服务中断）

### 4.1 单实例服务（orchestrator / chassis_server）

**关键约束**：服务重启期间 listener 调它会失败。

**安全流程**：
1. **备份当前 .py**：`cp action_router.py action_router.py.bak.20260928_111833`
2. **备份当前服务状态**：`ps aux | grep orchestrator > /tmp/before_restart.log`
3. **杀老进程**：`kill PID` 或 `pkill -f orchestrator`
4. **确认死透**：`sleep 2; ps aux | grep orchestrator`（应该没输出）
5. **启动新进程**：`setsid nohup python llm_orchestrator.py --port 7792 > /tmp/orch.log 2>&1 &`
6. **验证启动**：`tail -10 /tmp/orch.log`（应该有 "DGX LLM Orchestrator 启动"）
7. **功能测试**：发一个 query 验证 reply 正确

### 4.2 准备"原版回退"

重启前备份当前 .py + .pyc（编译缓存）：
```bash
cp action_router.py action_router.py.bak.before_restart.TIMESTAMP
cp action_router.pyc action_router.pyc.bak.before_restart.TIMESTAMP 2>/dev/null
# 也备份 __pycache__
cp -r __pycache__ __pycache__.bak.before_restart.TIMESTAMP
```

如果新代码有问题，立刻回退：
```bash
cp action_router.py.bak.before_restart.TIMESTAMP action_router.py
# 删 __pycache__ 让 Python 重读（防止 .pyc 缓存旧版本）
rm -rf __pycache__
# 重启
```

---

## 5. 长期监控机制

### 5.1 加文件 mtime 到 startup log

每次服务启动时 log 当前 .py 文件的 mtime 和 md5：

```python
import os, hashlib, logging
mod_path = '/data/linglong-project/scripts/action_router.py'
mtime = os.path.getmtime(mod_path)
md5 = hashlib.md5(open(mod_path, 'rb').read()).hexdigest()
logging.getLogger('orch').info(f"action_router loaded: mtime={mtime}, md5={md5}")
```

启动 log 立刻能看出"加载的是哪个时间点的版本"。

### 5.2 设个 reminder 提醒定期重启

不能频繁重启（服务中断），但**长期跑的服务应该定期重启**：

- orchestrator：每周重启一次（周一凌晨 4 点）
- action_router_server：每周一次
- chassis_server_v123：每月一次

可以用 cron 或 Leo 手动触发。

---

## 6. 当前服务的"最后重启时间"

| 服务 | PID | 启动时间 | 加载的 action_router 版本 |
|---|---|---|---|
| orchestrator | 2620071 | 2026-09-28 11:18 | 9/24（重启加载） |
| action_router_server | 1533443 | 2026-09-24 12:52 | 9/24（启动时） |
| chassis_server_v123 | 1492432 | 2026-09-24 12:13 | N/A |
| voice_cmd_server | 1179417 | 2026-09-10 | N/A |

**风险**：action_router_server 9/24 启动至今 4 天没重启。如果 Leo 改了 action_router.py（不加 audio_b64），服务不会自动加载。

---

## 7. 永久教训

**改 .py 文件 → 必须立刻重启相关进程**。否则 Leo 测试会以为是 bug，其实是内存版本问题。

**同步机制**：
- 改代码前 sync Leo（确认改动方向）
- 改完代码 sync Leo（备份 + 重启计划）
- 重启服务前 sync Leo（kill 老 + 启动新 + 验证）
- 重启后 sync Leo（功能测试通过）

---

_最后更新：2026-09-28 13:11_