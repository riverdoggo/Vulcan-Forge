import requests

response = requests.post(
    "http://localhost:8000/tasks",
    json={
        "goal": "You are working in /workspace. First list the files. Then run the tests and observe what fails. Read the failing source file. Fix the bug. Run tests again to verify they pass. Then commit the fix."
    }
)

print(response.json())
