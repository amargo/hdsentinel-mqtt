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


from math import ceil
from subprocess import check_output
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional
from yaml import safe_load

# from dotenv import load_dotenv

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
    "SensorConfig", [("topic", str), ("payload", Dict["str", Any])]
)

__FILE = Path(__file__)
_LOGGER = logging.getLogger(__FILE.name)
BASE_DIR = __FILE.parent

MQTT_CLIENT_ID = __FILE.name
MQTT_TOPIC = "hdsentinel"

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
    def value_types(self) -> Dict[str, callable]:
        return self.__value_types


class MqttClient:
    def __init__(
        self, broker_host: str, broker_port: int, broker_auth: Optional[dict] = None
    ):
        self.__connection_options = {
            "hostname": broker_host,
            "port": broker_port,
            "auth": broker_auth,
            "client_id": MQTT_CLIENT_ID,
        }

    def publish_multiple(self, payloads: List[Dict[str, Any]], **kwargs) -> None:
        publish.multiple(payloads, **self.__connection_options, **kwargs)

    def publish_single(self, topic: str, payload: str, **kwargs) -> None:
        publish.single(topic, payload, **self.__connection_options, **kwargs)


class HaCapableMqttClient(MqttClient):
    def __init__(self, base_topic: str, **kwargs):
        self.__base_topic = base_topic
        self.__status_topic = self.get_abs_topic("availability")

        self.__published_status = None

        super().__init__(**kwargs)

    @property
    def status_topic(self) -> str:
        return self.__status_topic

    def get_abs_topic(self, *relative_topic: str) -> str:
        return "/".join([self.__base_topic] + list(relative_topic))

    def __publish_status(self, status: str) -> None:
        if status == self.__published_status:
            return

        _LOGGER.info("Publish status {!r}".format(status))
        self.publish_single(self.__status_topic, status, retain=True)

        self.__published_status = status

    def publish_online_status(self) -> None:
        self.__publish_status("online")

    def publish_offline_status(self) -> None:
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


def get_disks():
    hdsentinel_output = os.getenv(
        "HDSENTINEL_XML_PATH", BASE_DIR.joinpath("hdsentinel_output.xml")
    )
    if "HDSENTINEL_XML_PATH" not in os.environ:
        _LOGGER.info("Generate xml with hdsentinel...")
        os.system(f"/usr/sbin/hdsentinel -solid -xml -r {hdsentinel_output}")
        # stdout = check_output(["/usr/sbin/hdsentinel", "-xml", "-r", "hdsentinel_output.xml"], encoding='UTF-8')
    else:
        _LOGGER.debug(f"hdsentinel_output: {hdsentinel_output}")

    hdsentinel_disk = {}
    _LOGGER.info("Parsing xml with hdsentinel...")
    hdsentinel_xml = ET.parse(hdsentinel_output)
    for disk_summary in hdsentinel_xml.findall(".//Hard_Disk_Summary"):
        disk_summary_str = ET.tostring(disk_summary, method="xml")
        disk_summary_str = disk_summary_str.replace(WINDOWS_LINE_ENDING, b"")
        disk_summary_str = disk_summary_str.replace(UNIX_LINE_ENDING, b"")
        disk_summary_dic = xmltodict.parse(disk_summary_str)
        hdsentinel_disk[
            disk_summary_dic["Hard_Disk_Summary"]["Hard_Disk_Serial_Number"]
        ] = disk_summary_dic["Hard_Disk_Summary"]

    return hdsentinel_disk


def to_snake_case(name):
    return "_".join(
        re.sub(
            "([A-Z][a-z]+)", r" \1", re.sub("([A-Z]+)", r" \1", name.replace("-", " "))
        ).split()
    ).lower()


def check_if_number(value, value_type):
    if value_type is int or value_type is float:
        return to_number(value)
    else:
        return value


def to_number(value):
    number_values = re.findall(r"\d+", value)
    return number_values[0]


def isfloat(value):
    try:
        float(value)
        return True
    except ValueError:
        return False


def main():
    global exiting_main_loop, update_interval
    # load_dotenv()

    debug_logging = os.getenv("DEBUG", "0") == "1"
    use_debugpy = os.getenv("USE_DEBUGPY", "0") == "1"
    debugpy_port = os.getenv("DEBUGPY_PORT", 5678)
    mqtt_port = int(os.getenv("MQTT_PORT", 1883))
    mqtt_host = os.getenv("MQTT_HOST")
    mqtt_user = os.getenv("MQTT_USER")
    mqtt_password = os.getenv("MQTT_PASSWORD")

    mqtt_auth = (
        {"username": mqtt_user, "password": mqtt_password}
        if mqtt_user and mqtt_password
        else None
    )

    update_interval = int(os.getenv("HDSENTINEL_INTERVAL", update_interval))

    _LOGGER.info("Configure logging...")
    configure_logging(debug_logging)

    _LOGGER.info("Get initial data from hdsentinel...")
    disks = get_disks()

    configs = {}

    for disk_serial_number, values in disks.items():
        _LOGGER.info(f"key: {disk_serial_number}, value: {disks[disk_serial_number]}")
        alias = to_snake_case(values["Hard_Disk_Model_ID"])
        _LOGGER.info(f"Get initial data from hdsentinel... {alias}")
        mqtt_client = HaCapableMqttClient(
            f"{MQTT_TOPIC}/{alias}",
            broker_host=mqtt_host,
            broker_port=mqtt_port,
            broker_auth=mqtt_auth,
        )

        mqtt_topic = mqtt_client.get_abs_topic("hdsentinel")
        config = Config(
            disk_serial_number,
            alias,
            values["Hard_Disk_Model_ID"],
            values["Firmware_Revision"],
            mqtt_topic,
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

        _LOGGER.info(
            "Publish sensor list to Home Assistant: {!r}".format(discovery_msgs)
        )
        mqtt_client.publish_multiple(discovery_msgs)
        configs[alias] = config

    signal.signal(signal.SIGINT, stop_main_loop)
    signal.signal(signal.SIGTERM, stop_main_loop)

    exiting_main_loop = False
    try:
        while True:
            disks = get_disks()

            for disk_serial_number, values in disks.items():
                alias = to_snake_case(values["Hard_Disk_Model_ID"])

                mqtt_client = HaCapableMqttClient(
                    f"{MQTT_TOPIC}/{alias}",
                    broker_host=mqtt_host,
                    broker_port=mqtt_port,
                    broker_auth=mqtt_auth,
                )
                mqtt_topic = mqtt_client.get_abs_topic("hdsentinel")
                config = configs[alias]

                main_loop(mqtt_client, mqtt_topic, config, values)

            for _ in range(update_interval * 2):
                time.sleep(0.5)

                if exiting_main_loop:
                    exit(0)

    finally:
        mqtt_client.publish_offline_status()


def stop_main_loop(*args) -> None:
    global exiting_main_loop
    exiting_main_loop = True
    _LOGGER.info("Exiting main loop...")


def main_loop(
    mqtt_client: HaCapableMqttClient, mqtt_topic: str, config: Config, values: any
) -> None:
    status = {
        key: config.value_types.get(key, VALUE_TYPES[DEFAULT_TYPE_NAME])(
            check_if_number(
                value, config.value_types.get(key, VALUE_TYPES[DEFAULT_TYPE_NAME])
            )
        )
        for key, value in [(key.lower(), value) for key, value in values.items()]
    }

    status_string = json.dumps(status, sort_keys=True)
    _LOGGER.debug(status_string)

    mqtt_client.publish_single(mqtt_topic, status_string)
    mqtt_client.publish_online_status()


if __name__ == "__main__":
    main()
