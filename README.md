# hdsentinel-mqtt

Dockerized HDSentinel with MQTT

- [Home Assistant](https://www.home-assistant.io/) integration via auto-discovery (compatible with version 2021.11+)

## Summary

This project provides a Dockerized version of HDSentinel that publishes hard disk health and status information to an MQTT broker. It is designed to integrate seamlessly with Home Assistant, enabling easy monitoring and alerting based on your hard disk's health.

The main components of this project are:
- **hdsentinel_parser.py**: A Python script that parses the HDSentinel XML output and publishes the data to an MQTT broker.
- **config.yml**: A configuration file that defines the sensors and their attributes.
- **Dockerfile**: A Dockerfile that builds the Docker image with all necessary dependencies.

## Features

- Monitors hard disk health metrics including temperature, health percentage, and performance
- Publishes data to MQTT with configurable topics
- Supports Home Assistant MQTT auto-discovery
- **Unique identification for identical disk models** - handles multiple disks with the same model name
- Secure TLS connection to MQTT broker (optional)
- Runs as a non-root user for improved security
- Container health monitoring
- Robust error handling and logging

## Usage

### Recommended: hardened, host-generated XML (Mode B)

No `/dev`, no `privileged`. The container only reads one XML file. Mount the **directory**
read-only (a single-file bind mount keeps a stale inode if the file is replaced by `mv`).

```yaml
services:
  hdsentinel-mqtt:
    image: ghcr.io/amargo/hdsentinel-mqtt:latest   # pin a version tag in production
    container_name: hdsentinel-mqtt-ha
    restart: unless-stopped
    read_only: true
    cap_drop: [ALL]
    security_opt:
      - no-new-privileges:true
    pids_limit: 64
    mem_limit: 128m
    cpus: 0.5
    tmpfs:
      - /tmp
    logging:
      driver: json-file
      options: { max-size: "5m", max-file: "3" }
    environment:
      - TZ=Europe/Budapest
      - MQTT_HOST=localhost
      - MQTT_USER=mqtt_user
      - MQTT_PASSWORD_FILE=/run/secrets/mqtt_password   # or MQTT_PASSWORD=...
      - MQTT_TOPIC=hdsentinel
      - HDSENTINEL_XML_PATH=/data/hdsentinel_output.xml
      - HDSENTINEL_INTERVAL=600
    volumes:
      - /srv/hdsentinel:/data:ro
    secrets:
      - mqtt_password

secrets:
  mqtt_password:
    file: ./mqtt_password.txt   # chmod 600
```

The host writes the XML atomically (cron, every 10 minutes):

```cron
*/10 * * * * /root/hdsentinel -solid -xml -r /srv/hdsentinel/hdsentinel_output.new.xml >/dev/null 2>&1 && mv -f /srv/hdsentinel/hdsentinel_output.new.xml /srv/hdsentinel/hdsentinel_output.xml
```

> HDSentinel appends `.xml` to any `-r` file name that does not already end in `.xml`, so the temporary name must end in `.xml` (`...new.xml`, not `...xml.tmp`).

If the XML is older than `2 x HDSENTINEL_INTERVAL + 60 s` (cron stopped), the disks are marked
`offline` in Home Assistant instead of showing frozen values.

### Mode A: container runs HDSentinel itself

Needs disk access (not possible inside unprivileged LXC / most VMs):

```yaml
services:
  hdsentinel-mqtt:
    image: ghcr.io/amargo/hdsentinel-mqtt:latest
    privileged: true          # or devices: [...] + group_add (see Security)
    environment:
      - MQTT_HOST=localhost
    volumes:
      - /dev:/dev
    tmpfs:
      - /tmp
    restart: unless-stopped
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
| `MQTT_PASSWORD_FILE` | Read the password from this file (Docker secret); wins over `MQTT_PASSWORD` | No | - |
| `MQTT_USE_TLS` | Enable TLS for MQTT connection (0 = off, 1 = on) | No | `0` |
| `MQTT_TLS_CA_CERT` | CA bundle path for the broker certificate (default: system CAs) | No | - |
| `MQTT_TLS_INSECURE` | Skip certificate verification (1 = on, **not recommended**) | No | `0` |
| `MQTT_NAME_PREFIX` | Display-name prefix (e.g. host name) for devices/entities. Does not change `unique_id`, topics or identifiers | No | - |
| `MQTT_TOPIC` | Base topic for MQTT messages | No | `hdsentinel` |
| `HDSENTINEL_XML_PATH` | Mode B: path of the host-generated XML (container does not run HDSentinel) | No | unset (Mode A, writes to `/tmp`) |
| `HDSENTINEL_ALIAS_MODE` | Topic/device alias: `auto` = model only, plus serial suffix only for models that occur more than once; `model` = always model only; `serial` = always model + serial | No | `auto` |
| `HDSENTINEL_INTERVAL` | Interval in seconds between checks (minimum 10) | No | `600` |
| `HDSENTINEL_HEARTBEAT_FILE` | Healthcheck heartbeat file | No | `/tmp/hdsentinel.heartbeat` |
| `DEBUG` | Enable debug logging (0 = off, 1 = on) | No | `0` |
| `TZ` | Timezone | No | - |

## Security Considerations

### Container Privileges

In **Mode B** (recommended, especially on Proxmox/LXC) the container needs no devices, no capabilities and no write access: use the hardened compose above.

In **Mode A** the container requires access to disk devices to read SMART data. There are several ways to configure this access:

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

1. **Enable TLS**: Set `MQTT_USE_TLS=1` (and `MQTT_TLS_CA_CERT` for a private CA) to encrypt MQTT traffic. The certificate is verified by default
2. **Use Authentication**: Always set `MQTT_USER` and `MQTT_PASSWORD` (prefer `MQTT_PASSWORD_FILE`)
3. **Restrict Topics**: The client needs write on `hdsentinel/#` and `homeassistant/+/hdsentinel_+/#`, and read on `homeassistant/status` (to re-publish discovery after a Home Assistant restart; optional)
4. **Availability**: each disk has a retained `online`/`offline` topic plus an MQTT last will, so a crashed container turns entities unavailable immediately

### Non-Root User

The container runs as a non-root user (`hdsentinel`, uid 1000). The HDSentinel download is verified with a pinned SHA256 at build time, and the image contains no build tools. The healthcheck passes while the main loop keeps running.

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

## Handling Multiple Identical Disks

### Problem: Duplicate Device Models

When you have multiple hard drives with identical model names (e.g., multiple `SAMSUNG_HD103UJ` or `WDC_WD10EFRX-68FYTN0` drives), older versions of this project would create colliding MQTT topics and Home Assistant entities. This caused:

- Only one device appearing in Home Assistant with incomplete/missing sensors
- Device attributes (like `/dev/sdX`) appearing to "swap" between disks
- Entity states being overwritten by different physical disks

### Solution: Serial-Based Identification

Starting from version **2.0.0**, the project uses a **stable, unique identifier** for each disk:

- **Device identification**: Uses the disk's **serial number** (e.g., `S13PJ90S113060`) as the stable key
- **Alias generation** (`HDSENTINEL_ALIAS_MODE=auto`, default): the model name only, so existing topics stay stable; models that occur more than once get the sanitized serial number appended
  - Example: `samsung_hd103uj_s13pj90s113060` and `samsung_hd103uj_s13pj90s113054`
- **MQTT topics**: Each disk gets unique topics like:
  - `hdsentinel/samsung_hd103uj_s13pj90s113060/hdsentinel`
  - `hdsentinel/samsung_hd103uj_s13pj90s113054/hdsentinel`
- **Home Assistant entities**: Each disk appears as a separate device with unique entity IDs

### Benefits

**Stable across reboots**: Even if Linux reassigns `/dev/sdb` ↔ `/dev/sdc`, entities remain correct  
**No collisions**: Each disk has unique MQTT topics and HA device identifiers  
**Clear identification**: Entity names include serial suffix for easy distinction

### Migration from Older Versions

**Breaking Change** only for setups with multiple identical disk models (single models keep their old topics in `auto` mode; use `HDSENTINEL_ALIAS_MODE=model` to force the legacy alias). If you're upgrading from a version before 2.0.0 and have multiple identical disk models:

1. **Backup your Home Assistant configuration** (optional but recommended)
2. **Remove old MQTT devices** in Home Assistant:
   - Go to **Settings** → **Devices & Services** → **MQTT**
   - Find the old disk devices (they'll have generic names like `samsung_hd103uj`)
   - Click on each device and select **Delete**
3. **Restart the container**:
   ```bash
   docker restart hdsentinel-mqtt-ha
   ```
4. **New devices will appear** with unique names (e.g., `samsung_hd103uj_s13pj90s113060`)

**Alternative**: Clear retained MQTT discovery topics on your broker:
```bash
# Example using mosquitto_pub
mosquitto_pub -h localhost -t 'homeassistant/sensor/hdsentinel_samsung_hd103uj/#' -n -r -d
```

### Technical Details

The implementation follows [Home Assistant MQTT Discovery best practices](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery):

- **`unique_id`**: `hdsentinel_{serial_number}_{sensor_name}` (e.g., `hdsentinel_S13PJ90S113060_health`)
- **Device `identifiers`**: `["hdsentinel_{serial_number}"]` (e.g., `["hdsentinel_S13PJ90S113060"]`)
- **Device `name`**: `{model}_{serial_number}` (sanitized) (e.g., `samsung_hd103uj_s13pj90s113060`)
- **State topics**: `hdsentinel/{alias}/hdsentinel`

This ensures each physical disk is tracked independently, regardless of model name duplication or `/dev/sdX` enumeration changes.

## Troubleshooting

### Common Issues

1. **No disks detected**
   - Mode A only: ensure the container has disk access (`privileged` or `devices`) and `/dev:/dev`
   - Mode B: check the XML exists as a **file** on the host before starting the container
   - Check logs with `docker logs hdsentinel-mqtt-ha`

2. **MQTT connection failures**
   - Verify MQTT broker address and credentials
   - Check if TLS is required by your broker
   - Ensure broker is reachable from the container

3. **Missing sensors in Home Assistant**
   - Verify Home Assistant MQTT integration is configured
   - Check if MQTT discovery is enabled in Home Assistant
   - Restart the container to republish discovery messages

4. **Duplicate or conflicting entities (after upgrade)**
   - Delete old MQTT devices in Home Assistant (see [Migration section](#migration-from-older-versions))
   - Clear retained MQTT topics for old device names
   - Restart the container to republish with new unique identifiers

5. **Disk serial number not found**
   - The script will log a warning and skip disks without serial numbers
   - Ensure HDSentinel can read SMART data from your disks
   - Check that the disk is properly connected and recognized by the system

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
