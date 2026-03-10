from app.tools.docker_terminal import run_in_container

# read file inside container workspace
def read_file(container, path):

    return run_in_container(container, f"cat {path}")


# write file inside container workspace
def write_file(container, args):

    path = args.get("path")
    content = args.get("content")

    command = f'echo "{content}" > {path}'

    return run_in_container(container, command)