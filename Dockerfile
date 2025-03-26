FROM python:alpine
ARG HDSENTINEL_VERSION="020c"
ARG GLIBC_VERSION=2.34-r0
RUN \
  apk add --no-cache ca-certificates util-linux && \
  \
  wget -qO "/etc/apk/keys/sgerrand.rsa.pub" "https://alpine-pkgs.sgerrand.com/sgerrand.rsa.pub" && \
  wget -O "/tmp/glibc.apk" "https://github.com/sgerrand/alpine-pkg-glibc/releases/download/$GLIBC_VERSION/glibc-$GLIBC_VERSION.apk" && \
  apk add "/tmp/glibc.apk" && rm "/tmp/glibc.apk" && \
  \
  wget -O - "https://www.hdsentinel.com/hdslin/hdsentinel-$HDSENTINEL_VERSION-x64.gz" | \
  gzip -dc > "/usr/sbin/hdsentinel" && \
  chmod +x "/usr/sbin/hdsentinel"

# upgrade pip
RUN pip install --no-cache-dir --upgrade pip

# install requirements
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app  ./app

VOLUME /dev
ENTRYPOINT ["python", "app/hdsentinel-parser.py"]