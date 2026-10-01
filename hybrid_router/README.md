# hybrid_router 服务 (Phase 1)

DGX 端口: 7785
功能: 本地+云端混合智能路由
设计: skills/linglong-hybrid-architecture-SKILL.md

## 启动

```bash
nohup /data/linglong-project/envs/vla/bin/python /data/linglong-project/scripts/hybrid_router.py > /tmp/hybrid_router.log 2>&1 &
```

## API

- `POST /decide` 决定任务本地/云端
- `POST /async_call` 异步云端调用
- `POST /result` 获取异步结果
- `GET /stats` 统计
- `GET /health` 健康检查

## 测试

```bash
# 健康检查
curl http://127.0.0.1:7785/health

# 决定任务
curl -X POST http://127.0.0.1:7785/decide \
  -H "Content-Type: application/json" \
  -d '{"type":"planning","task":"扫一下办公室"}'

# 统计
curl http://127.0.0.1:7785/stats
```

## 配置

- `TOKEN_BUDGET_PER_DAY`: 70M (Leo 9/30 14:15 更正)
- `DEFAULT_TTL_SEC`: 600 (10 分钟)
- `WORKER_COUNT`: 4 (后台 worker)

## 数据库

SQLite: `/data/linglong-project/hybrid_router.db`

两张表:
- `cache(signature, result, created_at, ttl_sec)` - 结果缓存
- `token_usage(date, tokens_used)` - 每日 token 用量

## 后续 Phase 2

需要：
- Walt/M3 云端 HTTP 接入（替换 mock）
- 真实路径规划（Frontier + A*）
- 物体识别多模态模型
