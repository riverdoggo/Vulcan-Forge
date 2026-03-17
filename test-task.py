import requests
import time
import sys

# START TASK
print("--- Starting Task ---")
response = requests.post(
    "http://localhost:8000/tasks",
    json={
        "goal": "You are working in /workspace. First list the files. Then run the tests and observe what fails. Read the failing source file. Fix the bug. Run tests again to verify they pass. Then commit the fix."
    }
)
task_data = response.json()
task_id = task_data.get("id")
print(f"Task ID: {task_id}")

# POLL FOR AWAITING_APPROVAL
print("--- Polling for awaiting_approval ---")
while True:
    time.sleep(2)
    t_resp = requests.get(f"http://localhost:8000/tasks/{task_id}")
    t_json = t_resp.json()
    status = t_json.get("status")
    print(f"Status: {status}")
    
    if status == "awaiting_approval":
        break
    elif status in ["completed", "rejected", "error", "max_steps_reached"]:
        print("Task finished unexpectedly without awaiting approval.")
        sys.exit(1)

# GET DIFF
print("\n--- Review Diff ---")
diff_resp = requests.get(f"http://localhost:8000/tasks/{task_id}/diff")
diff_json = diff_resp.json()
print(diff_json.get("diff", "No diff found."))

# APPROVE TASK
print("\n--- Approving Task ---")
app_resp = requests.post(f"http://localhost:8000/tasks/{task_id}/approve")
print(app_resp.json())

# WAIT FOR COMPLETED
print("--- Verifying Completion ---")
time.sleep(1)
t_resp = requests.get(f"http://localhost:8000/tasks/{task_id}")
print(f"Final Status: {t_resp.json().get('status')}")
