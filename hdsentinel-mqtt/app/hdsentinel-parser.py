import json
import configparser
import os
import re
import paho.mqtt.publish as publish

from subprocess import check_output
from blkinfo import BlkDiskInfo
from pathlib import Path
from datetime import datetime, timedelta

result = dict(message='')
def run_module():
    workdir = os.path.dirname(os.path.realpath(__file__))
    config = configparser.ConfigParser()
    mqtt_broker_cfg = init_mqtt(workdir)
    messages = []
    disk_messages = []

    try:
        disks = BlkDiskInfo().get_disks()
        # json_output = json.dumps(my_filtered_disks)
        # print(json_output)
        disk_list = []

        for disk in disks:
            try:
                disk_name = disk['name']
                information_list = {}
                disk_id = f"/dev/{disk_name}"
                stdout = check_output(["/usr/sbin/hdsentinel", "-dev", disk_id], encoding='UTF-8')
                print(stdout)
                information_list['name'] = disk_name
                information_list['id'] = disk_id
                model_id = append_information_from_stdout(information_list, "model_id", r"HDD Model ID\s*:\s*([^\n]*)", stdout)
                append_information_from_stdout(information_list, "serial_no", r"HDD Serial No\s*:\s*([^\n]*)", stdout)
                append_information_from_stdout(information_list, "revision", r"HDD Revision\s*:\s*([^\n]*)", stdout)
                append_information_from_stdout(information_list, "size", r"HDD Size\s*:\s*([^\n]*)", stdout)
                append_information_from_stdout(information_list, "interface", r"Interface\s*:\s*([^\n]*)", stdout)
                append_information_from_stdout(information_list, "temperature", r"Temperature\s*:\s*([^\n]*)", stdout)
                append_information_from_stdout(information_list, "highest_temp", r"Highest Temp.\s*:\s*([^\n]*)", stdout)
                append_information_from_stdout(information_list, "health", r"Health\s*:\s*([^\n]*)", stdout)
                append_information_from_stdout(information_list, "performance", r"Performance\s*:\s*([^\n]*)", stdout)
                append_information_from_stdout(information_list, "power_on_time", r"Power on time\s*:\s*([^\n]*)", stdout)
                append_information_from_stdout(information_list, "estimated_lifetime", r"Est. lifetime\s*:\s*([^\n]*)", stdout)
                append_information_from_stdout(information_list, "total_written", r"Total written\s*:\s*([^\n]*)", stdout)
                append_information_from_stdout(information_list, "status", r".*  (The.*)", stdout)
                append_information_from_stdout(information_list, "action", r"\.*    (.*)\.$", stdout)
                disk_list.append(information_list)
                information_data = json.dumps(information_list)
                snake_model_id = to_snake_case(model_id)
                disk_messages.append({'topic': f'sensors/{snake_model_id}/SENSOR', 'payload': information_data, 'retain': True})
                availability = 'Online'
            except Exception as ex:
                availability = 'Offline'
                print("Error retrive data from {0}.".format(str(ex)))
            finally:
                disk_messages.append({'topic': f'sensors/{snake_model_id}/availability', 'payload': availability, 'retain': True})

        # data = json.dumps(disk_list)
        # messages.append({'topic': 'sensors/disks/list', 'payload': data, 'retain': True})
        # availability = 'Online'
    except Exception as ex:
        # availability = 'Offline'
        print("Error retrive data from {0}.".format(str(ex)))
    finally:
        print("Done.")
        # messages.append({'topic': 'sensors/disks/availability', 'payload': availability, 'retain': True})
        # send_json(mqtt_broker_cfg, messages)
        send_json(mqtt_broker_cfg, disk_messages)

def to_snake_case(name):
      return '_'.join(
    re.sub('([A-Z][a-z]+)', r' \1',
    re.sub('([A-Z]+)', r' \1',
    name.replace('-', ' '))).split()).lower()

def append_information_from_stdout(information_list, key, pattern, stdout):
    match = re.search(pattern, stdout, re.MULTILINE)
    if match:
        information = match.group(1)
        information_list[key] = information
        return information
    return None

def init_mqtt(workdir):
    # Init MQTT
    mqtt_config = configparser.ConfigParser()
    mqtt_config.read("{0}/mqtt.ini".format(workdir))
    return mqtt_config["broker"]


def send_json(mqtt_broker_cfg, messages):
    # print(messages)
    try:
        auth = None
        mqtt_username = mqtt_broker_cfg.get("username")
        mqtt_password = mqtt_broker_cfg.get("password")

        if mqtt_username:
            auth = {"username": mqtt_username, "password": mqtt_password}

        publish.multiple(messages, hostname=mqtt_broker_cfg.get("host"), port=mqtt_broker_cfg.getint("port"), client_id=mqtt_broker_cfg.get("client"), auth=auth)
    except Exception as ex:
        print("Error publishing to MQTT: {0}".format(str(ex)))
    print("Data sent to mqtt server successfully")


def main():
    run_module()


if __name__ == '__main__':
    main()