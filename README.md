# hdsentinel-mqtt

Dockerized HDSentinel with MQTT

- [Home Assistant](https://www.home-assistant.io/) integration via auto-discovery (compatible with version 2021.11+)

## Summary

This project provides a Dockerized version of HDSentinel that publishes hard disk health and status information to an MQTT broker. It is designed to integrate seamlessly with Home Assistant, enabling easy monitoring and alerting based on your hard disk's health.

The main components of this project are:
- **hdsentinel-parser.py**: A Python script that parses the HDSentinel XML output and publishes the data to an MQTT broker.
- **config.yml**: A configuration file that defines the sensors and their attributes.
- **Dockerfile**: A Dockerfile that builds the Docker image with all necessary dependencies.

## Features

- Monitors hard disk health metrics including temperature, health percentage, and performance
- Publishes data to MQTT with configurable topics
- Supports Home Assistant MQTT auto-discovery
- Secure TLS connection to MQTT broker (optional)
- Runs as a non-root user for improved security
- Container health monitoring
- Robust error handling and logging

## Usage

You can run the Docker image using the following Docker Compose configuration:

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
      - MQTT_PORT=1883                               # Optional, defaults to 1883
      - MQTT_USER=mqtt_user                          # Optional
      - MQTT_PASSWORD=mqtt_password                  # Optional
      - MQTT_USE_TLS=0                               # Optional, enable TLS (0 = off, 1 = on)
      - MQTT_TOPIC=hdsentinel                        # Optional, base topic for MQTT messages
      - HDSENTINEL_XML_PATH=/app/hdsentinel_output.xml  # Path to the HDSentinel XML output file
      - HDSENTINEL_INTERVAL=600                         # Interval in seconds between HDSentinel checks
      - DEBUG=0                                         # Debug mode (0 = off, 1 = on)
    volumes:
      - /dev:/dev
      - /srv/hdsentinel/hdsentinel_output.xml:/app/hdsentinel_output.xml
    restart: always
```

## HDSentinel XML generation modes

This project can work in two different modes depending on whether `HDSENTINEL_XML_PATH` is set.

- **Mode A: Container generates the XML (default)**
  - **How it works**: If `HDSENTINEL_XML_PATH` is **not** set, the container runs HDSentinel internally and writes the XML to the default path (`/app/hdsentinel_output.xml`), then parses it.
  - **When to use**: Works best when the container has reliable access to disk devices (for example with `privileged: true` and `/dev:/dev`).

- **Mode B: Host generates the XML, container only reads/parses it**
  - **How it works**: If `HDSENTINEL_XML_PATH` **is** set, the container will **not** run HDSentinel. It will only read and parse the XML file at the provided path.
  - **When to use**: Recommended when disk / SMART access from inside the container is restricted or unreliable.

### Important note for Proxmox (PVE) users

When running on Proxmox (PVE), direct disk / SMART access from inside Docker containers can be unreliable or unavailable depending on how disks are passed through and what device permissions are available.

In that case, **Mode B (host-generated XML)** is often the most reliable setup:

#### Generate the XML on the Proxmox host

Example using native HDSentinel on the Proxmox host:

```bash
/root/hdsentinel -solid -xml -r /srv/hdsentinel/hdsentinel_output.xml
```

Typical cron setup:

```cron
*/10 * * * * /root/hdsentinel -solid -xml -r /srv/hdsentinel/hdsentinel_output.xml
```

#### Ensure the XML file exists before starting the container

If the host path does not exist, Docker may create it as a directory during bind-mounting, which will cause XML parsing errors.

#### Bind-mount the pre-generated file into the container

```yaml
volumes:
  - /srv/hdsentinel/hdsentinel_output.xml:/app/hdsentinel_output.xml
```

#### Summary

- **Proxmox host**: generates `hdsentinel_output.xml`
- **Container**: reads/parses the XML and publishes to MQTT

This approach avoids disk access and permission issues inside containers on PVE systems.

## Environment Variables

| Variable | Description | Required | Default |
|----------|-------------|----------|--------|
| `MQTT_HOST` | MQTT broker hostname or IP address | Yes | - |
| `MQTT_PORT` | MQTT broker port | No | `1883` |
| `MQTT_USER` | MQTT username for authentication | No | - |
| `MQTT_PASSWORD` | MQTT password for authentication | No | - |
| `MQTT_USE_TLS` | Enable TLS for MQTT connection (0 = off, 1 = on) | No | `0` |
| `MQTT_TOPIC` | Base topic for MQTT messages | No | `hdsentinel` |
| `HDSENTINEL_XML_PATH` | Path to the HDSentinel XML output file | No | `/app/hdsentinel_output.xml` |
| `HDSENTINEL_INTERVAL` | Interval in seconds between HDSentinel checks | No | `600` |
| `DEBUG` | Enable debug logging (0 = off, 1 = on) | No | `0` |
| `TZ` | Timezone | No | - |
| `PUID` | User ID for permissions | No | - |
| `PGID` | Group ID for permissions | No | - |

## Security Considerations

### Container Privileges

The container requires access to disk devices to read SMART data. There are several ways to configure this access:

#### Option 1: Privileged Mode (easiest but least secure)

```yaml
services:
  hdsentinel-mqtt:
    # ...
    privileged: true
    volumes:
      - /dev:/dev
```

Security implications:
- The container has full access to host devices
- Only deploy on trusted networks
- Consider isolating this container on its own network if possible

#### Option 2: Specific Device Access (more secure)

```yaml
services:
  hdsentinel-mqtt:
    # ...
    devices:
      - "/dev/sda:/dev/sda"
      - "/dev/nvme0n1:/dev/nvme0n1"
    volumes:
      - /dev:/dev
```

This approach only grants access to specific devices rather than all devices.

#### Option 3: Using Device Groups

The Docker image is configured to run as a non-root user (`hdsentinel`) that belongs to the `disk` group. This allows reading disk information without full privileged mode if your host is properly configured.

For this to work, you may need to ensure the container's disk group ID matches your host's disk group ID:

```yaml
services:
  hdsentinel-mqtt:
    # ...
    group_add:
      - "6" # Replace with your host's disk group ID (usually 6)
    volumes:
      - /dev:/dev
```

To find your host's disk group ID, run: `getent group disk | cut -d: -f3`

### MQTT Security

To improve MQTT security:

1. **Enable TLS**: Set `MQTT_USE_TLS=1` to encrypt MQTT traffic
2. **Use Authentication**: Always set `MQTT_USER` and `MQTT_PASSWORD`
3. **Restrict Topics**: Configure your MQTT broker to restrict publishing/subscribing to only necessary topics

### Non-Root User

The container now runs as a non-root user (`hdsentinel`) for improved security, while still maintaining the ability to access disk devices through the privileged container mode.

## Sensor Explanations

HDSentinel provides various disk health metrics that are published to MQTT. Here are explanations of the key sensors:

| Sensor | Description | Unit | Normal Range |
|--------|-------------|------|-------------|
| `health` | Overall health percentage of the disk | % | 100% is ideal, <80% indicates problems |
| `temperature` | Current disk temperature | °C | Varies by disk, typically 30-45°C is normal |
| `performance` | Disk performance percentage | % | 100% is ideal |
| `poweron_hours` | Total hours the disk has been powered on | hours | Informational |
| `estimated_remaining_lifetime` | Estimated remaining lifetime | % | Higher is better |
| `status` | Current disk status | text | "Perfect", "Good", etc. |

Additional sensors may be available depending on what your specific disk model reports.

## Troubleshooting

### Common Issues

1. **No disks detected**
   - Ensure the container has privileged access
   - Verify volume mapping `/dev:/dev` is correct
   - Check logs with `docker logs hdsentinel-mqtt-ha`

2. **MQTT connection failures**
   - Verify MQTT broker address and credentials
   - Check if TLS is required by your broker
   - Ensure broker is reachable from the container

3. **Missing sensors in Home Assistant**
   - Verify Home Assistant MQTT integration is configured
   - Check if MQTT discovery is enabled in Home Assistant
   - Restart the container to republish discovery messages

### Debugging

Enable debug logging by setting `DEBUG=1` in your environment variables. This will provide more detailed logs about:
- HDSentinel execution
- XML parsing
- MQTT connections and publishing
- Home Assistant discovery messages

## Advanced Configuration

### Custom Sensor Configuration

The `config.yml` file defines which sensors are published and their attributes. You can customize this file to add or remove sensors based on your needs.

### Multiple MQTT Brokers

The application currently supports a single MQTT broker. If you need to publish to multiple brokers, you can use an MQTT bridge or run multiple instances of the container with different configurations.

## Contributing

Contributions to improve hdsentinel-mqtt are welcome! Here are some ways you can contribute:

1. **Report bugs**: Open an issue describing the bug and how to reproduce it
2. **Suggest features**: Open an issue describing the new feature and its benefits
3. **Submit pull requests**: Implement bug fixes or new features

### Development Setup

1. Clone the repository
2. Make your changes to the code
3. Test your changes locally using Docker
4. Submit a pull request with a clear description of your changes
