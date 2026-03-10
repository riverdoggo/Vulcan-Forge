import subprocess

def run_in_container(container, command):
    result = subprocess.run(
        ["docker", "exec", container, "bash", "-c", command],
        capture_output=True,
        text=True
    )

    return {
        "status": "success" if result.returncode == 0 else "error",
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.returncode
    }