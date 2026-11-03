# syntax=docker/dockerfile:1

# ---- Stage 1: fetch and verify the HDSentinel binary ----
FROM python:3.14-alpine AS fetch
ARG HDSENTINEL_VERSION="020c"
# SHA256 of hdsentinel-${HDSENTINEL_VERSION}-x64.zip. Update together with the version.
ARG HDSENTINEL_SHA256="7f13dfa26110da80050fe7d538937b55ddbbae73fe077248f9cddcb6e857373b"
RUN apk add --no-cache ca-certificates unzip wget && \
    wget -q "https://www.hdsentinel.com/hdslin/hdsentinel-${HDSENTINEL_VERSION}-x64.zip" -O /tmp/hdsentinel.zip && \
    echo "${HDSENTINEL_SHA256}  /tmp/hdsentinel.zip" | sha256sum -c - && \
    unzip -p /tmp/hdsentinel.zip HDSentinel > /hdsentinel && \
    chmod 0555 /hdsentinel

# ---- Stage 2: runtime ----
FROM python:3.14-alpine

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Non-root user (primary group hdsentinel, supplementary group disk for Mode A)
RUN apk add --no-cache ca-certificates util-linux && \
    addgroup -g 1000 hdsentinel && \
    adduser -D -u 1000 -G hdsentinel hdsentinel && \
    addgroup hdsentinel disk

# Only used in Mode A (HDSENTINEL_XML_PATH not set)
COPY --from=fetch /hdsentinel /usr/sbin/hdsentinel

WORKDIR /app
USER hdsentinel

COPY --chown=hdsentinel:hdsentinel requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=hdsentinel:hdsentinel app /app

# Healthy while the main loop keeps touching the heartbeat file
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
  CMD ["python", "healthcheck.py"]

ENTRYPOINT ["python", "hdsentinel_parser.py"]
