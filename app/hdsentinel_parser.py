import xmltodict
import json
import logging
import os
import signal
import re
import ssl
import sys
import tempfile
import threading
import time
import paho.mqtt.client as mqtt
import xml.etree.ElementTree as ET
from defusedxml import ElementTree as SafeET
import subprocess
from typing import Any, Dict, List, NamedTuple, Optional, Union, Tuple

from math import ceil
from pathlib import Path
from yaml import safe_load

# replacement strings
WINDOWS_LINE_ENDING = b"\r\n"
UNIX_LINE_ENDING = b"\n"

DEFAULT_TYPE_NAME = "str"
VALUE_TYPES = {
    "float": float,
    "int": int,
    "str": str,
}

SensorConfig = NamedTuple(
    "SensorConfig", [("topic", str), ("payload", Dict[str, Any])]
)

__FILE = Path(__file__)
_LOGGER = logging.getLogger(__FILE.name)
BASE_DIR = __FILE.parent

MQTT_CLIENT_ID = __FILE.name
# Define the command used to run this script (used for documentation/reference)
SCRIPT_ENTRYPOINT = ["python", "app/hdsentinel_parser.py"]

HA_STATUS_TOPIC = "homeassistant/status"
MAX_XML_BYTES = 5 * 1024 * 1024
MIN_INTERVAL = 10
HEARTBEAT_FILE = Path(
    os.getenv("HDSENTINEL_HEARTBEAT_FILE")
    or Path(tempfile.gettempdir()).joinpath("hdsentinel.heartbeat")
)

update_interval = 600
stop_event = threading.Event()


class Config:
    SENSOR_TYPES = (
        "binary_sensor",
        "sensor",
    )

    def __init__(
        self,
        serial_no,
        alias,
        model,
        firmware,
        mqtt_state_topic: str,
        mqtt_availability_topic: str,
        name_prefix: str = "",
    ):

        self.__serial_no = serial_no
        self.__alias = alias
        # Display-only prefix; never touches unique_id / identifiers / topics
        self.__display_name = f"{name_prefix}_{alias}" if name_prefix else alias
        self.__model = model
        self.__firmware = firmware
        self.__mqtt_state_topic = mqtt_state_topic
        self.__availability_topic = mqtt_availability_topic

        with BASE_DIR.joinpath("config.yml").open() as fd:
            raw_config = safe_load(fd)

        self.__value_types = {}
        self.__sensors = []
        for sensor_type in self.__class__.SENSOR_TYPES:
            raw_sensors = raw_config.get(sensor_type) or {}
            sorted_raw_sensors = sorted(raw_sensors.items())
            _LOGGER.debug(
                f"raw_sensors len: {len(sorted_raw_sensors)}: {sorted_raw_sensors}"
            )

            for name, config in sorted_raw_sensors:
                if config is None:
                    config = {}

                internal_config = self.__pop_internal_config(config)

                query_key = internal_config.get("key", name)

                self.__value_types[query_key] = VALUE_TYPES[
                    internal_config.get("type", DEFAULT_TYPE_NAME)
                ]
                self.__sensors.append(
                    self.__get_device_descriptor(sensor_type, name, query_key, config)
                )

    def __pop_internal_config(self, config: dict) -> dict:
        return {
            key.lstrip("_").lower(): config.pop(key)
            for key in list(config)
            if key.startswith("_")
        }

    def __get_device_descriptor(
        self, sensor_type: str, name: str, query_key: str, config: dict
    ) -> SensorConfig:
        topic = (
            f"homeassistant/{sensor_type}/hdsentinel_{self.__alias}/{query_key}/config"
        )
        payload = {
            "device": {
                "identifiers": [
                    f"hdsentinel_{self.__serial_no}",
                ],
                "manufacturer": "hdsentinel",
                "name": self.__display_name,
                "model": self.__model,
                "sw_version": self.__firmware,
            },
            "expire_after": ceil(1.5 * update_interval),
            "unique_id": f"hdsentinel_{self.__serial_no}_{query_key}",
            "name": f"{self.__display_name}_{name}",
            "availability_topic": self.__availability_topic,
            "state_topic": self.__mqtt_state_topic,
            "json_attributes_topic": self.__mqtt_state_topic,
            "value_template": f"{{{{value_json.{query_key}}}}}",
        }

        _LOGGER.debug("Update payload config file {!r}".format(config))
        payload.update(config)

        return SensorConfig(topic, payload)

    @property
    def sensors(self) -> List[SensorConfig]:
        return self.__sensors

    @property
    def alias(self) -> str:
        return self.__alias

    @property
    def value_types(self) -> Dict[str, callable]:
        return self.__value_types


class MqttClient:
    """Persistent MQTT client (single connection, auto-reconnect, optional LWT)."""

    def __init__(
        self,
        broker_host: str,
        broker_port: int,
        broker_auth: Optional[dict] = None,
        use_tls: bool = False,
        tls_ca_cert: Optional[str] = None,
        tls_insecure: bool = False,
        client_id: str = MQTT_CLIENT_ID,
        will_topic: Optional[str] = None,
        will_payload: Optional[str] = None,
    ):
        """Initialize and start the MQTT client.

        Args:
            broker_host (str): MQTT broker hostname
            broker_port (int): MQTT broker port
            broker_auth (Optional[dict]): {"username": ..., "password": ...}
            use_tls (bool): Whether to use TLS
            tls_ca_cert (Optional[str]): CA bundle path (defaults to system CAs)
            tls_insecure (bool): Skip certificate verification (not recommended)
            client_id (str): Unique client id (one connection per id)
            will_topic (Optional[str]): Retained last-will topic
            will_payload (Optional[str]): Last-will payload
        """
        self.__host = broker_host
        self.__port = broker_port
        self.__last_info = None

        self.__client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=client_id
        )
        if broker_auth:
            self.__client.username_pw_set(
                broker_auth["username"], broker_auth["password"]
            )
        if use_tls:
            if tls_insecure:
                _LOGGER.warning("MQTT TLS certificate verification is DISABLED")
                self.__client.tls_set(cert_reqs=ssl.CERT_NONE)
                self.__client.tls_insecure_set(True)
            else:
                self.__client.tls_set(ca_certs=tls_ca_cert or None)
        if will_topic:
            self.__client.will_set(will_topic, will_payload, qos=1, retain=True)

        self.__client.reconnect_delay_set(min_delay=1, max_delay=120)
        # Bound the offline queue (sensors + discovery fit comfortably)
        self.__client.max_queued_messages_set(500)
        self.__client.on_connect = self.__handle_connect
        self.__client.on_disconnect = self.__handle_disconnect
        self.__client.on_message = self.__handle_message

        self.__client.connect_async(self.__host, self.__port, keepalive=60)
        self.__client.loop_start()

    # --- callbacks -----------------------------------------------------
    def __handle_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            _LOGGER.error(f"MQTT connect failed: {reason_code}")
            return
        _LOGGER.info(f"MQTT connected to {self.__host}:{self.__port}")
        try:
            self._on_connected()
        except Exception as e:
            _LOGGER.error(f"Error in on_connected hook: {e}")

    def __handle_disconnect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            _LOGGER.warning(f"MQTT disconnected unexpectedly: {reason_code}")

    def __handle_message(self, client, userdata, message):
        try:
            self._on_message(message.topic, message.payload)
        except Exception as e:
            _LOGGER.error(f"Error handling message on {message.topic}: {e}")

    def _on_connected(self) -> None:
        """Hook: called after a successful (re)connect."""

    def _on_message(self, topic: str, payload: bytes) -> None:
        """Hook: called for subscribed messages."""

    # --- API -----------------------------------------------------------
    def subscribe(self, topic: str) -> None:
        self.__client.subscribe(topic, qos=1)

    def publish_single(
        self, topic: str, payload: str, retain: bool = False, qos: int = 1
    ) -> None:
        """Queue a message; the network loop delivers it (and re-sends on reconnect)."""
        try:
            info = self.__client.publish(topic, payload, qos=qos, retain=retain)
            if info.rc == mqtt.MQTT_ERR_NO_CONN:
                # paho queues QoS>0 messages while offline and sends on reconnect
                _LOGGER.debug(f"Not connected, queued message for {topic}")
            elif info.rc != mqtt.MQTT_ERR_SUCCESS:
                _LOGGER.error(f"Failed to publish message to {topic}: rc={info.rc}")
            self.__last_info = info
        except Exception as e:
            _LOGGER.error(f"Failed to publish message to {topic}: {e}")

    def publish_multiple(self, payloads: List[Dict[str, Any]], **kwargs) -> None:
        """Publish multiple messages (dicts with topic/payload/retain)."""
        for msg in payloads:
            self.publish_single(
                msg["topic"],
                msg["payload"],
                retain=msg.get("retain", False),
                qos=msg.get("qos", 1),
            )

    def stop(self, timeout: float = 5.0) -> None:
        """Flush the last queued message, then disconnect cleanly."""
        try:
            if self.__last_info is not None:
                self.__last_info.wait_for_publish(timeout=timeout)
        except Exception as e:
            _LOGGER.debug(f"Flush on stop failed: {e}")
        finally:
            self.__client.disconnect()
            self.__client.loop_stop()


class HaCapableMqttClient(MqttClient):
    """MQTT client with Home Assistant availability (retained + LWT) and discovery."""

    def __init__(self, base_topic: str, **kwargs):
        """Initialize Home Assistant capable MQTT client.

        Args:
            base_topic (str): Base topic
        """
        self.__base_topic = base_topic
        self.__status_topic = self.get_abs_topic("availability")
        self.__published_status = None
        self.__discovery_msgs: List[Dict[str, Any]] = []

        super().__init__(
            will_topic=self.__status_topic,
            will_payload="offline",
            **kwargs,
        )

    @property
    def status_topic(self) -> str:
        """Get status topic."""
        return self.__status_topic

    def get_abs_topic(self, *relative_topic: str) -> str:
        """Get absolute topic."""
        return "/".join([self.__base_topic] + list(relative_topic))

    def set_discovery_messages(self, msgs: List[Dict[str, Any]]) -> None:
        """Store discovery configs; re-sent on every reconnect / HA restart."""
        self.__discovery_msgs = msgs
        self.publish_multiple(msgs)

    def _on_connected(self) -> None:
        self.subscribe(HA_STATUS_TOPIC)
        self.__republish()

    def _on_message(self, topic: str, payload: bytes) -> None:
        if topic == HA_STATUS_TOPIC and payload.strip() == b"online":
            _LOGGER.info("Home Assistant came online, re-publishing discovery")
            self.__republish()

    def __republish(self) -> None:
        if self.__discovery_msgs:
            self.publish_multiple(self.__discovery_msgs)
        status, self.__published_status = self.__published_status, None
        if status:
            self.__publish_status(status)

    def __publish_status(self, status: str) -> None:
        """Publish retained availability status (deduplicated)."""
        if status == self.__published_status:
            return

        _LOGGER.info(f"Publishing status: {status}")
        self.publish_single(self.__status_topic, status, retain=True)
        self.__published_status = status

    def publish_online_status(self) -> None:
        """Publish online status."""
        self.__publish_status("online")

    def publish_offline_status(self) -> None:
        """Publish offline status."""
        self.__publish_status("offline")


class LevelFilter(logging.Filter):
    def __init__(self, filtered_level: int, **kwargs):
        self.__filtered_level = filtered_level

        super().__init__(**kwargs)

    def filter(self, record: logging.LogRecord):
        return record.levelno == self.__filtered_level


def configure_logging(debug_logging: bool) -> None:
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.DEBUG)
    stderr_handler.addFilter(LevelFilter(logging.DEBUG))

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        level=logging.DEBUG if debug_logging else logging.INFO,
        handlers=[stdout_handler, stderr_handler],
    )


def get_disks() -> Dict[str, Dict[str, Any]]:
    """Get disk information from HDSentinel.
    
    Returns:
        Dict[str, Dict[str, Any]]: Dictionary of disk information, keyed by serial number
    """
    # Mode A writes into the temp dir so the root filesystem can be read-only.
    hdsentinel_output = os.getenv(
        "HDSENTINEL_XML_PATH",
        Path(tempfile.gettempdir()).joinpath("hdsentinel_output.xml"),
    )

    if os.getenv("HDSENTINEL_XML_PATH") is None:
        _LOGGER.info("Generate xml with hdsentinel...")
        try:
            subprocess.run(
                ["/usr/sbin/hdsentinel", "-solid", "-xml", "-r", str(hdsentinel_output)],
                check=True, capture_output=True
            )
        except subprocess.CalledProcessError as e:
            _LOGGER.error(f"Failed to run HDSentinel: {e}")
            return {}
    else:
        _LOGGER.debug(f"hdsentinel_output: {hdsentinel_output}")

    hdsentinel_disk = {}
    _LOGGER.info("Parsing xml with hdsentinel...")
    
    try:
        if os.path.getsize(hdsentinel_output) > MAX_XML_BYTES:
            _LOGGER.error(f"XML larger than {MAX_XML_BYTES} bytes, refusing to parse")
            return {}
        hdsentinel_xml = SafeET.parse(hdsentinel_output)

        for disk_summary in hdsentinel_xml.findall(".//Hard_Disk_Summary"):
            try:
                disk_summary_str = ET.tostring(disk_summary, method="xml")
                disk_summary_str = disk_summary_str.replace(WINDOWS_LINE_ENDING, b"")
                disk_summary_str = disk_summary_str.replace(UNIX_LINE_ENDING, b"")
                disk_summary_dic = xmltodict.parse(disk_summary_str)
                
                serial_number = disk_summary_dic["Hard_Disk_Summary"].get("Hard_Disk_Serial_Number")
                if serial_number:
                    hdsentinel_disk[serial_number] = disk_summary_dic["Hard_Disk_Summary"]
                else:
                    _LOGGER.warning("Found disk without serial number, skipping")
            except Exception as e:
                _LOGGER.error(f"Error processing disk summary: {e}")
                continue
    except (ET.ParseError, OSError) as e:
        _LOGGER.error(f"Failed to parse XML: {e}")
        return {}

    return hdsentinel_disk


def xml_age_seconds() -> Optional[float]:
    """Age of the externally generated XML (Mode B), None in Mode A / unreadable."""
    path = os.getenv("HDSENTINEL_XML_PATH")
    if not path:
        return None
    try:
        return max(0.0, time.time() - os.path.getmtime(path))
    except OSError:
        return None


def env_int(name: str, default: int, minimum: int = 0) -> int:
    """Read an int env var, falling back to default when missing/invalid/too small."""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        _LOGGER.warning(f"Invalid {name}={raw!r}, using {default}")
        return default
    if value < minimum:
        _LOGGER.warning(f"{name}={value} below minimum {minimum}, using {minimum}")
        return minimum
    return value


def read_secret(name: str) -> Optional[str]:
    """Read NAME, or the file named by NAME_FILE (Docker/K8s secrets)."""
    file_path = os.getenv(f"{name}_FILE")
    if file_path:
        try:
            return Path(file_path).read_text(encoding="utf-8").strip()
        except OSError as e:
            _LOGGER.error(f"Cannot read {name}_FILE={file_path}: {e}")
            return None
    return os.getenv(name)


def touch_heartbeat() -> None:
    """Mark the main loop as alive for the container healthcheck."""
    try:
        HEARTBEAT_FILE.touch()
    except OSError as e:
        _LOGGER.debug(f"Cannot touch heartbeat file: {e}")


def to_snake_case(name: str) -> str:
    """Convert a string to snake_case.
    
    Args:
        name (str): String to convert
        
    Returns:
        str: Converted string in snake_case
    """
    return "_".join(
        re.sub(
            "([A-Z][a-z]+)", r" \1", re.sub("([A-Z]+)", r" \1", name.replace("-", " "))
        ).split()
    ).lower()


def to_safe_id(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", (value or "")).strip("_").lower()


def build_disk_alias(model_id: str, serial_number: str) -> str:
    model_part = to_snake_case(model_id or "unknown")
    serial_suffix = to_safe_id(serial_number) if serial_number else "unknown"
    return f"{model_part}_{serial_suffix}"


def check_if_number(value: str, value_type: type) -> Union[str, int, float]:
    """Check if a value should be converted to a number based on its type.
    
    Args:
        value (str): Value to check
        value_type (type): Expected type of the value
        
    Returns:
        Union[str, int, float]: Converted value if applicable, otherwise original value
    """
    if value_type is int or value_type is float:
        return to_number(value)
    else:
        return value


def to_number(value: str) -> str:
    """Extract the first number from a string.
    
    Args:
        value (str): String containing numbers
        
    Returns:
        str: First number found in the string, or "0" if none found
    """
    number_values = re.findall(r"\d+", value)
    return number_values[0] if number_values else "0"


def isfloat(value: str) -> bool:
    """Check if a string can be converted to float.
    
    Args:
        value (str): String to check
        
    Returns:
        bool: True if string can be converted to float, False otherwise
    """
    try:
        float(value)
        return True
    except ValueError:
        return False


def main() -> None:
    """Main function."""
    global update_interval

    # Configure environment variables
    debug_logging = os.getenv("DEBUG", "0") == "1"
    mqtt_port = env_int("MQTT_PORT", 1883, minimum=1)
    mqtt_host = os.getenv("MQTT_HOST")
    mqtt_user = os.getenv("MQTT_USER")
    mqtt_password = read_secret("MQTT_PASSWORD")
    mqtt_use_tls = os.getenv("MQTT_USE_TLS", "0") == "1"
    mqtt_tls_ca_cert = os.getenv("MQTT_TLS_CA_CERT") or None
    mqtt_tls_insecure = os.getenv("MQTT_TLS_INSECURE", "0") == "1"
    mqtt_topic = os.getenv("MQTT_TOPIC", "hdsentinel")  # Default topic prefix
    name_prefix = to_safe_id(os.getenv("MQTT_NAME_PREFIX", ""))

    mqtt_auth = (
        {"username": mqtt_user, "password": mqtt_password}
        if mqtt_user and mqtt_password
        else None
    )

    update_interval = env_int("HDSENTINEL_INTERVAL", update_interval, MIN_INTERVAL)
    stale_after = 2 * update_interval + 60

    configure_logging(debug_logging)

    if not mqtt_host:
        _LOGGER.error("MQTT_HOST environment variable is not set. Exiting.")
        sys.exit(1)

    _LOGGER.info("Get initial data from hdsentinel...")
    disks = get_disks()

    if not disks:
        _LOGGER.error("No disks found or error getting disk data. Exiting.")
        sys.exit(1)

    configs: Dict[str, Config] = {}
    mqtt_clients: Dict[str, HaCapableMqttClient] = {}

    def make_client(alias: str) -> HaCapableMqttClient:
        return HaCapableMqttClient(
            f"{mqtt_topic}/{alias}",
            broker_host=mqtt_host,
            broker_port=mqtt_port,
            broker_auth=mqtt_auth,
            use_tls=mqtt_use_tls,
            tls_ca_cert=mqtt_tls_ca_cert,
            tls_insecure=mqtt_tls_insecure,
            client_id=f"{MQTT_CLIENT_ID}-{alias}"[:128],
        )

    for disk_serial_number, values in disks.items():
        try:
            _LOGGER.info(f"Processing disk: {disk_serial_number}")
            alias = build_disk_alias(
                values.get("Hard_Disk_Model_ID", f"unknown_{disk_serial_number}"),
                disk_serial_number,
            )
            _LOGGER.info(f"Using alias: {alias}")

            mqtt_client = make_client(alias)
            mqtt_clients[disk_serial_number] = mqtt_client

            disk_state_topic = mqtt_client.get_abs_topic("hdsentinel")
            config = Config(
                disk_serial_number,
                alias,
                values.get("Hard_Disk_Model_ID", "Unknown"),
                values.get("Firmware_Revision", "Unknown"),
                disk_state_topic,
                mqtt_client.status_topic,
                name_prefix,
            )
            _LOGGER.info(
                f"Configuring Home Assistant via MQTT Discovery... {mqtt_host}:{mqtt_port}-{alias}"
            )

            discovery_msgs = [
                {
                    "topic": sensor.topic,
                    "payload": json.dumps(sensor.payload, sort_keys=True),
                    "retain": True,
                }
                for sensor in config.sensors
            ]

            _LOGGER.info(f"Publishing {len(discovery_msgs)} sensors for {alias}")
            mqtt_client.set_discovery_messages(discovery_msgs)
            configs[disk_serial_number] = config
        except Exception as e:
            _LOGGER.error(f"Error setting up disk {disk_serial_number}: {e}")
            continue

    signal.signal(signal.SIGINT, stop_main_loop)
    signal.signal(signal.SIGTERM, stop_main_loop)

    try:
        while not stop_event.is_set():
            try:
                age = xml_age_seconds()
                if age is not None and age > stale_after:
                    _LOGGER.warning(
                        f"HDSentinel XML is stale ({int(age)}s old), marking disks offline"
                    )
                    for client in mqtt_clients.values():
                        client.publish_offline_status()
                else:
                    disks = get_disks()

                    for disk_serial_number, values in disks.items():
                        try:
                            config = configs.get(disk_serial_number)

                            # Skip disks that weren't in the initial configuration
                            if not config:
                                _LOGGER.warning(
                                    f"Skipping new disk {disk_serial_number} that wasn't in initial configuration"
                                )
                                continue

                            mqtt_client = mqtt_clients[disk_serial_number]
                            disk_state_topic = mqtt_client.get_abs_topic("hdsentinel")

                            main_loop(mqtt_client, disk_state_topic, config, values)
                        except Exception as e:
                            _LOGGER.error(
                                f"Error processing disk {disk_serial_number}: {e}"
                            )
                            continue
            except Exception as e:
                _LOGGER.error(f"Error in main loop: {e}")

            touch_heartbeat()

            # Wakes up immediately on SIGTERM/SIGINT
            if stop_event.wait(update_interval):
                _LOGGER.info("Exiting main loop due to signal")
                break
    finally:
        # Publish offline status, flush, disconnect cleanly
        for serial, client in mqtt_clients.items():
            try:
                _LOGGER.info(f"Publishing offline status for {serial}")
                client.publish_offline_status()
                client.stop()
            except Exception as e:
                _LOGGER.error(f"Error publishing offline status for {serial}: {e}")


def stop_main_loop(*args) -> None:
    _LOGGER.info("Exiting main loop...")
    stop_event.set()


def main_loop(
    mqtt_client: HaCapableMqttClient, disk_state_topic: str, config: Config, values: Dict[str, Any]
) -> None:
    """Main processing loop for a single disk.
    
    Args:
        mqtt_client (HaCapableMqttClient): MQTT client
        disk_state_topic (str): MQTT topic for disk state
        config (Config): Configuration
        values (Dict[str, Any]): Disk values
    """
    # Convert all keys to lowercase
    status = {
        key.lower(): value
        for key, value in values.items()
    }

    try:
        status_string = json.dumps(status, sort_keys=True)
        _LOGGER.debug(f"Publishing status for {disk_state_topic}: {status_string[:100]}...")

        mqtt_client.publish_single(disk_state_topic, status_string)
        mqtt_client.publish_online_status()
    except Exception as e:
        _LOGGER.error(f"Error in main_loop: {e}")


if __name__ == "__main__":
    main()
