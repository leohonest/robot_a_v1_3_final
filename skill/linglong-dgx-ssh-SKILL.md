# 灵龙 DGX SSH 铁律 SKILL (linglong-dgx-ssh-SKILL.md)

> 任何 DGX / 灵龙 SSH 操作必须用 dgx_run()，禁止手写 plink / ssh / sshpass
> 版本：v1.0  2026-09-22 16:08 Leo 强化

## 一、为什么要用 dgx_run()

### 1.1 背景：手动 SSH 必踩坑（2026-09-22 真实事故）

Leo 让 Walt 把 v3 listener 切成流式 TTS。Walt 写完补丁后，需要把补丁传到灵龙并重启。手动操作了 5 次：

| 尝试 | 命令 | 错误 |
|------|------|------|
| 1 | `subprocess.run(['plink', '-ssh', ...])` | "host key not cached" |
| 2 | 加 `-o StrictHostKeyChecking=no` | "Password authentication failed" |
| 3 | 加 `-pw admin` | "Unable to use key file" |
| 4 | 加 `-batch` | "Access denied" |
| 5 | 换成 `wsl sshpass -p $LINGLONG_PASSWORD ssh ...` | "Connection refused" （不通灵龙网） |

**浪费 7 分钟**。正确做法：用 `scripts/dgx_ssh.py:dgx_run()` 30 秒搞定。

### 1.2 dgx_run() 已内置的配置

| 配置项 | 值 | 来源 |
|--------|---|------|
| plink 路径 | `C:\Users\DFET\AppData\Local\Temp\plink.exe` | Windows 环境 |
| DGX 主机 | `kk@10.86.51.122` | Leo 私网 |
| DGX 密码 | `${DGX_PASSWORD}` | TOOLS.md |
| 灵龙主机 | `$LINGLONG_USER@$LINGLONG_HOST` | 灵龙内部网 |
| 灵龙密码 | `admin` | TOOLS.md |
| SSH 端口 | 22 | DGX 跳板到灵龙 |
| 默认超时 | 30 秒 | 灵龙端编译用 60 秒 |
| 中文 encoding | utf-8 | Windows PowerShell 默认 GBK 会乱码 |

## 二、正确用法（30 秒模板）

### 2.1 DGX 直接命令

```python
import sys
sys.path.insert(0, r'C:\Users\DFET\.openclaw\workspace\scripts')
from dgx_ssh import dgx_run

# 读文件
content = dgx_run("cat /data/linglong-project/scripts/llm_orchestrator.py")

# 写文件（base64 避免引号转义）
import base64
script = "print('hello')"
b64 = base64.b64encode(script.encode()).decode()
dgx_run(f"echo {b64} | base64 -d > /tmp/x.py && python3 /tmp/x.py")

# 跑命令
result = dgx_run("systemctl status linglong-monitor")
```

### 2.2 DGX 跳板到灵龙（核心场景）

```python
# 读灵龙文件
content = dgx_run("sshpass -p $LINGLONG_PASSWORD ssh -o StrictHostKeyChecking=no $LINGLONG_USER@$LINGLONG_HOST 'cat /home/user/linglong_voice_v3.py'")

# 重启灵龙脚本
dgx_run("sshpass -p $LINGLONG_PASSWORD ssh $LINGLONG_USER@$LINGLONG_HOST 'pkill -f linglong_voice_v3.py; sleep 2; setsid nohup /usr/bin/python3 -u /home/user/linglong_voice_v3.py > /tmp/v3.log 2>&1 < /dev/null &'")

# 灵龙端 ls
out = dgx_run("sshpass -p $LINGLONG_PASSWORD ssh $LINGLONG_USER@$LINGLONG_HOST 'ls /home/user/'")
```

### 2.3 完整补丁流程模板

```python
# scripts/_patch_xxx.py 标准模板
import sys, time, base64
sys.path.insert(0, r'C:\Users\DFET\.openclaw\workspace\scripts')
from dgx_ssh import dgx_run

TARGET_FILE = '/home/user/linglong_voice_v3.py'  # 灵龙文件
LOCAL_PATCH = r'C:\Users\DFET\.openclaw\workspace\scripts\_patch_inner.py'

# 1. 备份
ts = time.strftime('%Y%m%d_%H%M%S')
dgx_run(f'sshpass -p $LINGLONG_PASSWORD ssh $LINGLONG_USER@$LINGLONG_HOST '
        f'"cp {TARGET_FILE} {TARGET_FILE}.bak.{ts}"')

# 2. 上传补丁（base64 避免转义）
with open(LOCAL_PATCH, 'rb') as f:
    patch_bytes = f.read()
b64 = base64.b64encode(patch_bytes).decode()
dgx_run(f'echo {b64} | base64 -d > /tmp/_patch.py')
dgx_run('sshpass -p $LINGLONG_PASSWORD scp -o StrictHostKeyChecking=no /tmp/_patch.py $LINGLONG_USER@$LINGLONG_HOST:/tmp/_patch.py')

# 3. 执行补丁
dgx_run("sshpass -p $LINGLONG_PASSWORD ssh $LINGLONG_USER@$LINGLONG_HOST 'python3 /tmp/_patch.py'")

# 4. 重启目标
dgx_run(f'sshpass -p $LINGLONG_PASSWORD ssh $LINGLONG_USER@$LINGLONG_HOST '
        f'"pkill -f linglong_voice_v3.py; sleep 2; '
        f'setsid nohup /usr/bin/python3 -u {TARGET_FILE} '
        f' > /tmp/v3.log 2>&1 < /dev/null &"')

# 5. 验证
dgx_run("sshpass -p $LINGLONG_PASSWORD ssh $LINGLONG_USER@$LINGLONG_HOST 'tail -20 /tmp/v3.log'")
```

## 三、禁止写法（永久）

### 3.1 禁止手写 plink

```python
# OK NEVER DO THIS
import subprocess
subprocess.run([r'C:\Users\DFET\AppData\Local\Temp\plink.exe',
               '-batch', 'kk@10.86.51.122', '...'])
```

### 3.2 禁止手写 ssh

```python
# OK NEVER DO THIS
subprocess.run(['ssh', 'kk@10.86.51.122', '...'])
```

### 3.3 禁止 WSL 直连灵龙

```python
# OK NEVER DO THIS
subprocess.run(['wsl', 'sshpass', '-p', '${LANGSHOST_PASSWORD}',
                'ssh', '$LINGLONG_USER@$LINGLONG_HOST', '...'])
# 失败原因：Windows 校园网 192.168.4.x 跟灵龙 192.168.1.x 物理隔离
# 必须从 DGX (Leo 私网 10.86.51.122) 跳到灵龙 192.168.1.12
```

### 3.4 禁止 Python subprocess 写命令（含 base64）

```python
# OK NEVER DO THIS
subprocess.run(['sshpass', '-p', '${LANGSHOST_PASSWORD}', 'ssh', '...'],
               capture_output=True, text=True)
# 等于重复实现 dgx_run()，但少了：hostkey、超时、encoding
```

## 四、配置文件

### 4.1 dgx_ssh.py 模块位置

`C:\Users\DFET\.openclaw\workspace\scripts\dgx_ssh.py`

### 4.2 调用前 sys.path

```python
import sys
sys.path.insert(0, r'C:\Users\DFET\.openclaw\workspace\scripts')
```

### 4.3 dgx_run() 函数签名

```python
def dgx_run(cmd: str, timeout: int = 30, use_sudo: bool = False) -> str:
    """Run cmd on DGX (kk@10.86.51.122) via plink, return stdout."""
```

## 五、故障排查

| 症状 | 检查 |
|--------|------|
| dgx_run() 报 "timeout" | 网络问题，`ping 10.86.51.122` 看通没 |
| dgx_run() 报 "Permission denied" | 检查 `${DGX_PASSWORD}` 密码（可能 Leo 改过）|
| 灵龙 SSH 报 "Connection refused" | 检查灵龙 WiFi（192.168.1.12 vs 校园网 4-509）|
| 中文乱码 | dgx_run() 已经处理，如有问题查 .dgx_ssh 模块 |
| 中文乱码（plink 自身） | DGX 端 `export LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8` |

## 六、相关 SKILL

- `skills/linglong-voice-qa-SKILL.md` — v3 listener SSH 调用
- `skills/linglong-sdk-debug-SKILL.md` — 灵龙 SDK SSH 调用
- `skills/linglong-disable-SKILL.md` — disable 脚本 SSH 调用

---

_最后更新_2026-09-22 16:08_