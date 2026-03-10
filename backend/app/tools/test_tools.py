from app.tools.docker_terminal import run_in_container

def run_tests(container, path=None):
    # basic test runner tool
    return run_in_container(container, "pytest")