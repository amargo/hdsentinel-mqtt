# hdsentinel-mqtt
Dockerized HDSentinel with MQTT.

- [Home Assistant](https://www.home-assistant.io/) integration with auto-discovery (compatible w/ version 2021.11+)

## Usage
    ```
    ---
    docker run -d \
    --name hdsentinel-mqtt-ha \
    --restart unless-stopped \
    -e PUID=1000 \
    -e PGID=1000 \
    -e TZ=Europe/Budapest \
    -e MQTT_HOST=ip \
    -e MQTT_USER=user \
    -e MQTT_PASSWORD=password \
    -e HDSENTINEL_INTERVAL=600 \
    -v /dev:/dev \
    --privileged \
    gerykapitany/hdsentinel-mqtt-ha        
    ```
