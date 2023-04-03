#!/bin/bash

IMAGE_VERSION=19c
TAG=gszoboszlai/hdsentinel-mqtt-ha:$IMAGE_VERSION

docker build --no-cache --rm -t $TAG .
docker push $TAG