# hdsentinel-mqtt
Dockerized HDSentinel with MQTT.

- [Home Assistant](https://www.home-assistant.io/) integration with auto-discovery (compatible w/ version 2021.11+)

## Usage
    ```
    ---
    version: "3"
    services:
      hdsentinel-mqtt:
        image: gszoboszlai/hdsentinel-mqtt-ha
        container_name: hdsentinel-mqtt-ha
        privileged: true
        environment:
          - PUID=1000
          - PGID=1000
          - TZ=Europe/Budapest
          - MQTT_HOST=localhost
          - MQTT_USER=mqtt_user
          - MQTT_PASSWORD=mqtt_password
          - HDSENTINEL_INTERVAL=600
        volumes:
          - /dev:/dev
        restart: always
    ```
