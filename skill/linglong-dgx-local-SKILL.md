# DGX 灵龙操作本地 SKILL (v1)

**版本**: V1 (2026-09-02 12:52)
**作者**: Walt
**目的**: 把灵龙 SDK 操作**本地化**，不再每次查 API / 写脚本

---

## 1. 激活/退出机制

| 关键词 | 作用 |
|--------|------|
| **"开始灵龙操作"** | 激活映射表 + 进入快速响应模式 |
| **"结束灵龙操作"** | 退出灵龙模式 |

激活后我直接查映射表 + SSH exec，**不读 SKILL、不查 API、不写脚本**。

---

## 2. 指令映射表（核心）

所有脚本位于 DGX：`/data/linglong-project/scripts/linglong/`

| Leo 中文指令 | DGX 命令 | 说明 |
|--------------|----------|------|
| 灵龙使能 | `python3 /data/linglong-project/scripts/linglong/enable.py` | 上使能 + 自主 + L-shape |
| 灵龙归位 | `python3 /data/linglong-project/scripts/linglong/home.py` | 仅 L-shape 归位 |
| 握紧左手 | `python3 /data/linglong-project/scripts/linglong/cap.py L 1.0` | cap_l=1.0 |
| 松开左手 | `python3 /data/linglong-project/scripts/linglong/cap.py L 0.0` | cap_l=0.0 |
| 握紧右手 | `python3 /data/linglong-project/scripts/linglong/cap.py R 1.0` | cap_r=1.0 |
| 松开右手 | `python3 /data/linglong-project/scripts/linglong/cap.py R 0.0` | cap_r=0.0 |
| 下使能 | `python3 /data/linglong-project/scripts/linglong/disable.py` | 切操作模式 + 下使能 |
| 查状态 | `python3 /data/linglong-project/scripts/linglong/state.py` | L_EE/R_EE/waist/cap |
| 查左臂关节 | `python3 /data/linglong-project/scripts/linglong/state.py L` | 加左臂7关节 |
| 查右臂关节 | `python3 /data/linglong-project/scripts/linglong/state.py R` | 加右臂7关节 |
| 查全部 | `python3 /data/linglong-project/scripts/linglong/state.py all` | 加双臂+腰部 |

---

## 3. 执行模板（极致快）

激活后，对每个指令我做：

```python
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('10.86.51.122', username='kk', password='${DGX_PASSWORD}', timeout=10)

# 查映射表得到命令
cmd = '<从映射表查询到的 DGX 命令>'
stdin, stdout, stderr = c.exec_command(cmd, timeout=15)
print(stdout.read().decode())

c.close()
```

**优势**：
- 不用写 Python 脚本
- 不用 SFTP 上传
- 不用等长 stdout 读取
- token 消耗降到最低

---

## 4. 注意事项

1. **激活前** 还是要用通用 SKILL（linglong-sdk-debug-SKILL.md）思考
2. **激活后** 只走映射表，不重新思考
3. **关键词严格匹配**："开始灵龙操作" / "结束灵龙操作"
4. **不在映射表中的指令** → 退出灵龙模式，走通用 SKILL
5. **每次激活** 我会在心里"加载映射表"（一个 python dict）

---

## 5. 完整链路（参考）

```
Leo "开始灵龙操作"
   ↓
Walt 激活映射表
   ↓
Leo "握紧左手"
   ↓
Walt 查表 → cap.py L 1.0
   ↓
SSH 到 DGX → exec 脚本
   ↓
回复 Leo "已发握紧左手"
```

---

**SKILL 结束** | 版本 V1 | 2026-09-02 12:52