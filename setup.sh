#!/bin/bash

IMAGE_VERSION=19c
TAG=gszoboszlai/hdsentinel-mqtt:$IMAGE_VERSION

docker build --no-cache --rm -t $TAG hdsentinel-mqtt
docker push $TAG