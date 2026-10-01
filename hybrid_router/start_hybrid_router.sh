#!/bin/bash
# 启动 hybrid_router 服务
nohup /data/linglong-project/envs/vla/bin/python /data/linglong-project/scripts/hybrid_router.py > /tmp/hybrid_router.log 2>&1 &
echo "hybrid_router started, PID: $!"
sleep 2
curl -s http://127.0.0.1:7785/health || echo "health check failed"
