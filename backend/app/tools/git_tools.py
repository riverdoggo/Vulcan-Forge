from app.tools.docker_terminal import run_in_container

# basic git operations inside sandbox container

def git_diff(container, path=None):

    return run_in_container(container, "git diff")


def git_commit(container, message):

    run_in_container(container, "git add .")

    return run_in_container(container, f'git commit -m "{message}"')