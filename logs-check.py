# poll_logs.py
import sys
import time

import requests

task_id = sys.argv[1]

while True:
    r = requests.get(f"http://localhost:8000/tasks/{task_id}/logs")
    data = r.json()
    steps = data.get("steps", [])
    print(f"\n--- {len(steps)} steps so far ---")
    for s in steps:
        print(f"Step {s['step']} | Tool: {s['decision'].get('tool')} | Input: {s['decision'].get('input')}")
        result = s["result"]
        if isinstance(result, dict):
            print(f"  Result status: {result.get('status')}")
            print(f"  Stdout: {result.get('stdout', '')[:500]}")
        else:
            print(f"  Result: {result}")

    if data.get("status") in ("completed", "max_steps_reached", "error"):
        print(f"\nFinal status: {data.get('status')}")
        break

    time.sleep(3)
