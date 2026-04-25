# REUSED FROM (PATTERN): Q-Build-Manager/Dockerfile
FROM python:3.10-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    git-email \
    perl \
    sparse \
    coccinelle \
    device-tree-compiler \
    curl \
    wget \
    unzip \
    nodejs \
    npm \
    libssl-dev \
    libffi-dev \
    build-essential \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ARG REQUIREMENTS_FILE=requirements.txt
ARG PIP_EXTRA_INDEX_URL=https://devpi.qualcomm.com/qcom/dev/+simple
ARG PIP_TRUSTED_HOST=devpi.qualcomm.com
COPY requirements*.txt /tmp/
RUN python -m pip install --upgrade pip && \
    req_file="/tmp/${REQUIREMENTS_FILE}" && \
    if [ ! -f "$req_file" ]; then req_file="/tmp/requirements.txt"; fi && \
    if [ -n "$PIP_EXTRA_INDEX_URL" ]; then \
      pip install --no-cache-dir --extra-index-url "$PIP_EXTRA_INDEX_URL" --trusted-host "$PIP_TRUSTED_HOST" -r "$req_file"; \
    else \
      pip install --no-cache-dir -r "$req_file"; \
    fi

RUN mkdir -p /usr/local/share/linux && \
    curl -fsSL https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/plain/scripts/checkpatch.pl -o /usr/local/bin/checkpatch.pl && \
    chmod +x /usr/local/bin/checkpatch.pl && \
    curl -fsSL https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/plain/Documentation/process/coding-style.rst -o /usr/local/share/linux/codingstyle

COPY . /app
RUN chmod +x /app/scripts/entrypoint.sh /app/run.sh

EXPOSE 5001

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
