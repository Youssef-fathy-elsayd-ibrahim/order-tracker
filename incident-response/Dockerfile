FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /uvx /bin/
ARG COPILOT_VERSION=1.0.112
ARG COPILOT_LINUX_AMD64_SHA256=dfe7e59acf04f0b7f59b024ea9db101a9246503e9bf7152eb668fbb2a469b151
ARG COPILOT_LINUX_ARM64_SHA256=491d18303505935675dc20987ef0c5f3ae899f81e66c53d567ad97a8b03e504e
WORKDIR /workspace
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-package playwright
RUN python -c "import platform, urllib.request; arch={'x86_64':'x64','aarch64':'arm64'}.get(platform.machine()); assert arch, 'Unsupported Copilot CLI architecture'; url='https://github.com/github/copilot-cli/releases/download/v${COPILOT_VERSION}/copilot-linux-'+arch+'.tar.gz'; urllib.request.urlretrieve(url, '/tmp/copilot-cli.tar.gz')" \
    && case "$(uname -m)" in \
      x86_64) COPILOT_SHA256="$COPILOT_LINUX_AMD64_SHA256" ;; \
      aarch64) COPILOT_SHA256="$COPILOT_LINUX_ARM64_SHA256" ;; \
      *) echo "Unsupported Copilot CLI architecture: $(uname -m)" >&2; exit 1 ;; \
    esac \
    && echo "$COPILOT_SHA256  /tmp/copilot-cli.tar.gz" | sha256sum -c - \
    && mkdir /tmp/copilot-cli \
    && tar -xzf /tmp/copilot-cli.tar.gz -C /tmp/copilot-cli \
    && COPILOT_BINARY="$(find /tmp/copilot-cli -type f -name copilot -print -quit)" \
    && test -n "$COPILOT_BINARY" \
    && install -m 0755 "$COPILOT_BINARY" /usr/local/bin/copilot \
    && COPILOT_AUTO_UPDATE=false COPILOT_HOME=/tmp/copilot-build-home /usr/local/bin/copilot version | grep -F "$COPILOT_VERSION" \
    && rm -rf /tmp/copilot-cli /tmp/copilot-cli.tar.gz /tmp/copilot-build-home
COPY app ./app
COPY tests ./tests
COPY incident-response/app.py /service/app.py
WORKDIR /service
ENV INCIDENT_DIR=/incidents
ENV ORDER_TRACKER_REPO=/workspace
ENV COPILOT_AUTO_UPDATE=false
EXPOSE 8001
CMD ["uv", "run", "--project", "/workspace", "--no-sync", "uvicorn", "app:app", "--app-dir", "/service", "--host", "0.0.0.0", "--port", "8001"]
