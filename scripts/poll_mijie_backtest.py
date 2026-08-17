# -*- coding: utf-8 -*-
"""轮询回测任务直至完成，然后抓取报告 Markdown。"""
import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000"
task_id = open("/tmp/mijie_task.txt").read().strip()
print(f"task_id={task_id}", flush=True)

t0 = time.time()
while True:
    r = httpx.get(f"{BASE}/api/backtest/status/{task_id}", timeout=15)
    st = r.json()
    status = st.get("status")
    prog = st.get("progress") or {}
    elapsed = int(time.time() - t0)
    print(f"[{elapsed}s] status={status} progress={json.dumps(prog, ensure_ascii=False)[:200]}", flush=True)
    if status == "done":
        result_file = st.get("result_file")
        print(f"RESULT_FILE={result_file}", flush=True)
        open("/tmp/mijie_result_file.txt", "w").write(result_file or "")
        break
    if status in ("failed", "stopped"):
        print(f"任务终止: {json.dumps(st, ensure_ascii=False)[:500]}", flush=True)
        sys.exit(1)
    time.sleep(20)

# 抓取报告
rf = open("/tmp/mijie_result_file.txt").read().strip()
rep = httpx.get(f"{BASE}/api/backtest/report", params={"file": rf, "with_llm": "true"}, timeout=120).json()
md = rep.get("markdown", "")
open("/tmp/mijie_report.md", "w", encoding="utf-8").write(md)
print(f"报告已保存: /tmp/mijie_report.md ({len(md)} 字符)", flush=True)
print("===MARKDOWN_START===")
print(md)
print("===MARKDOWN_END===")
