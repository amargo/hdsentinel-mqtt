import xmltodict
import json
import logging
import os
import signal
import re
import sys
import time
import paho.mqtt.publish as publish
import xml.etree.ElementTree as ET
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
SCRIPT_ENTRYPOINT = ["python", "app/hdsentinel-parser.py"]

update_interval = 600
exiting_main_loop = False


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
    ):

        self.__serial_no = serial_no
        self.__alias = alias
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
                "name": self.__alias,
                "model": self.__model,
                "sw_version": self.__firmware,
            },
            "expire_after": ceil(1.5 * update_interval),
            "unique_id": f"hdsentinel_{self.__serial_no}_{query_key}",
            "name": f"{self.__alias}_{name}",
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
    """MQTT client for publishing messages."""
    
    def __init__(
        self, broker_host: str, broker_port: int, broker_auth: Optional[dict] = None,
        use_tls: bool = False
    ):
        """Initialize MQTT client.
        
        Args:
            broker_host (str): MQTT broker hostname
            broker_port (int): MQTT broker port
            broker_auth (Optional[dict], optional): Authentication credentials. Defaults to None.
            use_tls (bool, optional): Whether to use TLS. Defaults to False.
        """
        self.__connection_options = {
            "hostname": broker_host,
            "port": broker_port,
            "auth": broker_auth,
            "client_id": MQTT_CLIENT_ID,
        }
        
        if use_tls:
            self.__connection_options["tls"] = {}

    def publish_multiple(self, payloads: List[Dict[str, Any]], **kwargs) -> None:
        """Publish multiple messages.
        
        Args:
            payloads (List[Dict[str, Any]]): List of payloads to publish
        """
        try:
            publish.multiple(payloads, **self.__connection_options, **kwargs)
        except Exception as e:
            _LOGGER.error(f"Failed to publish multiple messages: {e}")

    def publish_single(self, topic: str, payload: str, **kwargs) -> None:
        """Publish a single message.
        
        Args:
            topic (str): Topic to publish to
            payload (str): Payload to publish
        """
        try:
            publish.single(topic, payload, **self.__connection_options, **kwargs)
        except Exception as e:
            _LOGGER.error(f"Failed to publish message to {topic}: {e}")


class HaCapableMqttClient(MqttClient):
    """MQTT client with Home Assistant capabilities."""
    
    def __init__(self, base_topic: str, **kwargs):
        """Initialize Home Assistant capable MQTT client.
        
        Args:
            base_topic (str): Base topic
        """
        self.__base_topic = base_topic
        self.__status_topic = self.get_abs_topic("availability")
        self.__published_status = None

        super().__init__(**kwargs)

    @property
    def status_topic(self) -> str:
        """Get status topic.
        
        Returns:
            str: Status topic
        """
        return self.__status_topic

    def get_abs_topic(self, *relative_topic: str) -> str:
        """Get absolute topic.
        
        Args:
            *relative_topic (str): Relative topic parts
            
        Returns:
            str: Absolute topic
        """
        return "/".join([self.__base_topic] + list(relative_topic))

    def __publish_status(self, status: str) -> None:
        """Publish status.
        
        Args:
            status (str): Status to publish
        """
        if status == self.__published_status:
            return

        _LOGGER.info(f"Publishing status: {status}")
        try:
            self.publish_single(self.__status_topic, status, retain=True)
            self.__published_status = status
        except Exception as e:
            _LOGGER.error(f"Failed to publish status {status}: {e}")

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
    hdsentinel_output = os.getenv(
        "HDSENTINEL_XML_PATH", BASE_DIR.joinpath("hdsentinel_output.xml")
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
        hdsentinel_xml = ET.parse(hdsentinel_output)
        
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
    except (ET.ParseError, FileNotFoundError) as e:
        _LOGGER.error(f"Failed to parse XML: {e}")
        return {}

    return hdsentinel_disk


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
    global exiting_main_loop, update_interval

    # Configure environment variables
    debug_logging = os.getenv("DEBUG", "0") == "1"
    mqtt_port = int(os.getenv("MQTT_PORT", 1883))
    mqtt_host = os.getenv("MQTT_HOST")
    mqtt_user = os.getenv("MQTT_USER")
    mqtt_password = os.getenv("MQTT_PASSWORD")
    mqtt_use_tls = os.getenv("MQTT_USE_TLS", "0") == "1"
    mqtt_topic = os.getenv("MQTT_TOPIC", "hdsentinel")  # Default topic prefix

    mqtt_auth = (
        {"username": mqtt_user, "password": mqtt_password}
        if mqtt_user and mqtt_password
        else None
    )

    update_interval = int(os.getenv("HDSENTINEL_INTERVAL", update_interval))

    _LOGGER.info("Configure logging...")
    configure_logging(debug_logging)

    if not mqtt_host:
        _LOGGER.error("MQTT_HOST environment variable is not set. Exiting.")
        sys.exit(1)

    _LOGGER.info("Get initial data from hdsentinel...")
    disks = get_disks()
    
    if not disks:
        _LOGGER.error("No disks found or error getting disk data. Exiting.")
        sys.exit(1)

    configs = {}
    mqtt_clients = {}

    for disk_serial_number, values in disks.items():
        try:
            _LOGGER.info(f"Processing disk: {disk_serial_number}")
            alias = build_disk_alias(
                values.get("Hard_Disk_Model_ID", f"unknown_{disk_serial_number}"),
                disk_serial_number,
            )
            _LOGGER.info(f"Using alias: {alias}")
            
            mqtt_client = HaCapableMqttClient(
                f"{mqtt_topic}/{alias}",
                broker_host=mqtt_host,
                broker_port=mqtt_port,
                broker_auth=mqtt_auth,
                use_tls=mqtt_use_tls
            )
            mqtt_clients[disk_serial_number] = mqtt_client

            disk_state_topic = mqtt_client.get_abs_topic("hdsentinel")
            config = Config(
                disk_serial_number,
                alias,
                values.get("Hard_Disk_Model_ID", "Unknown"),
                values.get("Firmware_Revision", "Unknown"),
                disk_state_topic,
                mqtt_client.status_topic,
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
            mqtt_client.publish_multiple(discovery_msgs)
            configs[disk_serial_number] = config
        except Exception as e:
            _LOGGER.error(f"Error setting up disk {disk_serial_number}: {e}")
            continue

    signal.signal(signal.SIGINT, stop_main_loop)
    signal.signal(signal.SIGTERM, stop_main_loop)

    exiting_main_loop = False
    try:
        while True:
            try:
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

                        mqtt_client = mqtt_clients.get(disk_serial_number)
                        if not mqtt_client:
                            _LOGGER.warning(
                                f"No MQTT client for {disk_serial_number}, creating new one"
                            )
                            mqtt_client = HaCapableMqttClient(
                                f"{mqtt_topic}/{config.alias}",
                                broker_host=mqtt_host,
                                broker_port=mqtt_port,
                                broker_auth=mqtt_auth,
                                use_tls=mqtt_use_tls
                            )
                            mqtt_clients[disk_serial_number] = mqtt_client
                            
                        disk_state_topic = mqtt_client.get_abs_topic("hdsentinel")

                        main_loop(mqtt_client, disk_state_topic, config, values)
                    except Exception as e:
                        _LOGGER.error(f"Error processing disk {disk_serial_number}: {e}")
                        continue
            except Exception as e:
                _LOGGER.error(f"Error in main loop: {e}")

            # Sleep with interruption support
            for _ in range(update_interval * 2):
                time.sleep(0.5)

                if exiting_main_loop:
                    _LOGGER.info("Exiting main loop due to signal")
                    sys.exit(0)

    except Exception as e:
        _LOGGER.error(f"Unexpected error in main loop: {e}")
    finally:
        # Publish offline status for all clients
        for serial, client in mqtt_clients.items():
            try:
                _LOGGER.info(f"Publishing offline status for {serial}")
                client.publish_offline_status()
            except Exception as e:
                _LOGGER.error(f"Error publishing offline status for {serial}: {e}")


def stop_main_loop(*args) -> None:
    global exiting_main_loop
    exiting_main_loop = True
    _LOGGER.info("Exiting main loop...")


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
