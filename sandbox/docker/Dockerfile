FROM python:3.11

RUN apt-get update && apt-get install -y \
    git \
    nodejs \
    npm

RUN pip install pytest

# configure git identity for agent commits
RUN git config --global user.email "agent@orchestrator.local" && \
    git config --global user.name "AI Agent"

WORKDIR /workspace

CMD ["sleep","infinity"]
