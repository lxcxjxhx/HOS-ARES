#!/usr/bin/env python3
# HOS-ARES 安全研究技能 —— open-websearch 本地 daemon 搜索助手
#
# 职责：
#   1. 探测/拉起 open-websearch 本地 daemon（HTTP API, 无需 API Key）
#   2. POST /search 执行多引擎联网搜索
#   3. 将结构化结果格式化为可读文本，供 HOS-ARES UI 弹窗实时流式展示
#
# 设计原则：CVE / 漏洞情报一律实时联网检索，不做本地 CVE 库集成。
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

PORT = int(os.environ.get("HOS_SEARCH_PORT", "3210"))
BASE = os.environ.get("HOS_SEARCH_BASE", f"http://127.0.0.1:{PORT}")
QUERY = os.environ.get("HOS_SEARCH_QUERY", "").strip()
ENGINE = os.environ.get("HOS_SEARCH_ENGINE", "bing").strip()
LIMIT = int(os.environ.get("HOS_SEARCH_LIMIT", "10"))


def health_ok():
    try:
        with urllib.request.urlopen(BASE + "/health", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def ensure_daemon():
    """确保本地 daemon 已运行；未运行则后台拉起并等待就绪。"""
    if health_ok():
        return True
    print("[secresearch] 启动 open-websearch daemon ...")
    log = open("/tmp/secresearch-daemon.log", "a")
    subprocess.Popen(
        ["open-websearch", "serve", "--port", str(PORT)],
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    for _ in range(20):
        time.sleep(1)
        if health_ok():
            return True
    return False


def do_search():
    body = {"query": QUERY, "limit": LIMIT, "engines": [ENGINE]}
    req = urllib.request.Request(
        BASE + "/search",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def extract_items(data):
    """兼容多种返回结构：data 可能是数组，或 {results/items/list/data: [...]}。"""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("results", "items", "list", "data", "searchResults"):
            v = data.get(k)
            if isinstance(v, list):
                return v
    return []


def fmt(items):
    lines = []
    lines.append(f"[secresearch] 引擎: {ENGINE} · 查询: {QUERY}")
    if not items:
        lines.append("[secresearch] 未返回任何结果（可尝试切换引擎，如 baidu/sogou/duckduckgo）。")
        return lines
    for i, it in enumerate(items, 1):
        if not isinstance(it, dict):
            continue
        title = it.get("title") or it.get("name") or "(无标题)"
        url = it.get("url") or it.get("link") or ""
        desc = (
            it.get("description")
            or it.get("snippet")
            or it.get("content")
            or it.get("summary")
            or ""
        )
        desc = " ".join(str(desc).split())[:300]
        lines.append(f"\n[{i}] {title}")
        if url:
            lines.append(f"    URL: {url}")
        if desc:
            lines.append(f"    {desc}")
    return lines


def main():
    if not QUERY:
        print("[secresearch] 未提供搜索查询文本。")
        sys.exit(2)
    if not ensure_daemon():
        print("[secresearch] 无法连接 open-websearch daemon，请确认已联网安装 open-websearch。")
        sys.exit(4)
    try:
        payload = do_search()
    except Exception as e:
        print(f"[secresearch] 搜索失败: {e}")
        sys.exit(5)
    data = payload.get("data") if isinstance(payload, dict) else None
    items = extract_items(data)
    print("\n".join(fmt(items)))


if __name__ == "__main__":
    main()
