#!/usr/bin/env python3
"""hybrid_router 单元测试"""
import sys
sys.path.insert(0, r'C:\Users\DFET\.openclaw\workspace\scripts')
import requests

BASE = 'http://127.0.0.1:7785'

def test_health():
    r = requests.get(f'{BASE}/health', timeout=5)
    print('Health:', r.json())
    assert r.status_code == 200
    assert r.json()['status'] == 'ok'

def test_decide():
    r = requests.post(f'{BASE}/decide', json={'type': 'chassis_simple', 'task': '前进'}, timeout=5)
    print('Decide:', r.json())

def test_async():
    r = requests.post(f'{BASE}/async_call', json={'type': 'planning', 'task': 'test'}, timeout=5)
    print('Async:', r.json())
    req_id = r.json()['request_id']
    r = requests.post(f'{BASE}/result', json={'request_id': req_id, 'timeout_sec': 3}, timeout=10)
    print('Result:', r.json())

def test_stats():
    r = requests.get(f'{BASE}/stats', timeout=5)
    print('Stats:', r.json())

if __name__ == '__main__':
    test_health()
    test_decide()
    test_async()
    test_stats()
    print('\n✅ All tests passed')
