# hdsentinel-mqtt

Dockerized HDSentinel with MQTT.

- [Home Assistant](https://www.home-assistant.io/) integration with auto-discovery (compatible with version 2021.11+)

## Summary

This project provides a Dockerized version of HDSentinel that publishes hard disk health and status information to an MQTT broker. It is designed to integrate seamlessly with Home Assistant, allowing for easy monitoring and alerting based on the health of your hard disks.

The main components of this project are:
- `hdsentinel-parser.py`: A Python script that parses HDSentinel XML output and publishes the data to an MQTT broker.
- `config.yml`: A configuration file that defines the sensors and their attributes.
- Dockerfile: A Dockerfile that builds the Docker image with all necessary dependencies.

## Usage

```yaml
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
      - HDSENTINEL_XML_PATH=/app/hdsentinel_output.xml # Path to the HDSentinel XML output file
      - HDSENTINEL_INTERVAL=600 # Interval in seconds between HDSentinel checks
      - DEBUG=0 # Debug mode (0 = off, 1 = on)
    volumes:
      - /dev:/dev
      - /srv/hdsentinel/hdsentinel_output.xml:/app/hdsentinel_output.xml
    restart: always
