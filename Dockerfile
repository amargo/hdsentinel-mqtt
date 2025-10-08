FROM python:3.14-alpine
ARG HDSENTINEL_VERSION="020c"
ENV HDSENTINEL_URL=https://www.hdsentinel.com/hdslin/hdsentinel-$HDSENTINEL_VERSION-x64.zip

# Create a non-root user with explicit UID/GID
ENV USER_ID=1000
ENV GROUP_ID=1000

# Download and install hdsentinel and set up environment
RUN apk add --no-cache ca-certificates util-linux unzip wget shadow && \
    wget ${HDSENTINEL_URL} -O /tmp/hdsentinel.zip && \
    unzip -p /tmp/hdsentinel.zip HDSentinel > "/usr/sbin/hdsentinel" && \
    chmod +x "/usr/sbin/hdsentinel" && \
    rm /tmp/hdsentinel.zip && \
    # Create user with specific ID and add to disk group
    addgroup -g ${GROUP_ID} hdsentinel && \
    adduser -D -u ${USER_ID} -G hdsentinel -G disk hdsentinel && \
    # Create app directory and set permissions
    mkdir -p /app && \
    chown -R hdsentinel:hdsentinel /app

# Set working directory
WORKDIR /app

# Switch to non-root user for pip operations
USER hdsentinel

# Copy requirements and install as non-root
COPY --chown=hdsentinel:hdsentinel requirements.txt /app/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code as non-root
COPY --chown=hdsentinel:hdsentinel app /app

# Define volume for device access
VOLUME /dev

# Health check
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
  CMD pgrep -f "python hdsentinel-parser.py" || exit 1

ENTRYPOINT ["python", "hdsentinel-parser.py"]