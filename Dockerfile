# One container: the war room, the crew, and the Grafana MCP server.
#
# The MCP server is a Go binary fetched at build time rather than compiled, so
# the image needs no Go toolchain. It runs as a child process over stdio, which
# is why the hosted Grafana MCP endpoint is not used: that one needs OAuth 2.1
# browser authorization and cannot work from a headless container.
FROM python:3.12-slim

ARG MCP_GRAFANA_VERSION=v1.3.0

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN mkdir -p bin \
 && curl -sSL "https://github.com/grafana/mcp-grafana/releases/download/${MCP_GRAFANA_VERSION}/mcp-grafana_Linux_x86_64.tar.gz" \
    | tar xz -C bin/ mcp-grafana \
 && chmod +x bin/mcp-grafana

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY sim/ sim/
COPY agent/ agent/
COPY web/ web/
COPY journals/ journals/
COPY docs/ docs/

# Deployed builds use Vertex AI, which is what makes the runtime use of Google
# Cloud unambiguous. Credentials come from the Cloud Run service account.
ENV GOOGLE_GENAI_USE_VERTEXAI=TRUE \
    PYTHONUNBUFFERED=1 \
    PORT=8080

EXPOSE 8080
CMD exec uvicorn web.server:app --host 0.0.0.0 --port ${PORT}
