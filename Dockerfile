FROM python:alpine
ARG HDSENTINEL_VERSION="020c"
ENV HDSENTINEL_URL https://www.hdsentinel.com/hdslin/hdsentinel-$HDSENTINEL_VERSION-x64.zip

# Download and install hdsentinel
ADD ${HDSENTINEL_URL} /tmp/hdsentinel.zip 
RUN apk add --no-cache ca-certificates util-linux unzip && \
  unzip -p /tmp/hdsentinel.zip HDSentinel > "/usr/sbin/hdsentinel" && \
  chmod +x "/usr/sbin/hdsentinel" && \
  rm /tmp/hdsentinel.zip

# upgrade pip
RUN pip install --no-cache-dir --upgrade pip

# install requirements
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app  ./app

VOLUME /dev
ENTRYPOINT ["python", "app/hdsentinel-parser.py"]