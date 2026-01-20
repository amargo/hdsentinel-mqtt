#!/usr/bin/env python3
import unittest
import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open
import json
import time

# Import the module to test
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.hdsentinel_parser import (
    get_disks, to_snake_case, to_safe_id, build_disk_alias, check_if_number, to_number, isfloat,
    MqttClient, HaCapableMqttClient, Config, main_loop
)


class TestHdSentinelParser(unittest.TestCase):
    """Test cases for HDSentinel parser functions."""

    def setUp(self):
        """Set up test environment."""
        # Create a temporary XML file with test data
        self.test_xml_content = """<?xml version="1.0" encoding="UTF-8"?>
        <Hard_Disk_Sentinel>
          <Hard_Disk_Summary>
            <Hard_Disk_Number>1</Hard_Disk_Number>
            <Hard_Disk_Device>/dev/nvme0n1</Hard_Disk_Device>
            <Hard_Disk_Model_ID>Samsung SSD 970 EVO Plus 1TB</Hard_Disk_Model_ID>
            <Hard_Disk_Serial_Number>S4EWNF0M123456</Hard_Disk_Serial_Number>
            <Firmware_Revision>2B2QEXM7</Firmware_Revision>
            <Hard_Disk_Health>100</Hard_Disk_Health>
            <Performance>100</Performance>
            <Temperature>38</Temperature>
            <Power_On_Time>1234 hours</Power_On_Time>
            <Estimated_Lifetime>More than 1000 days</Estimated_Lifetime>
          </Hard_Disk_Summary>
          <Hard_Disk_Summary>
            <Hard_Disk_Number>2</Hard_Disk_Number>
            <Hard_Disk_Device>/dev/sda</Hard_Disk_Device>
            <Hard_Disk_Model_ID>WDC WD10EZEX-00WN4A0</Hard_Disk_Model_ID>
            <Hard_Disk_Serial_Number>WD-WCC6Y5ABCDEF</Hard_Disk_Serial_Number>
            <Firmware_Revision>1A01</Firmware_Revision>
            <Hard_Disk_Health>95</Hard_Disk_Health>
            <Performance>98</Performance>
            <Temperature>42</Temperature>
            <Power_On_Time>5678 hours</Power_On_Time>
            <Estimated_Lifetime>More than 500 days</Estimated_Lifetime>
          </Hard_Disk_Summary>
        </Hard_Disk_Sentinel>
        """
        self.temp_xml_file = tempfile.NamedTemporaryFile(delete=False, suffix='.xml')
        self.temp_xml_file.close()
        with open(self.temp_xml_file.name, 'w') as f:
            f.write(self.test_xml_content)

    def tearDown(self):
        """Clean up test environment."""
        for _ in range(10):
            try:
                os.unlink(self.temp_xml_file.name)
                break
            except PermissionError:
                time.sleep(0.05)

    @patch('app.hdsentinel_parser.os.getenv')
    @patch('app.hdsentinel_parser.subprocess.run')
    def test_get_disks_with_xml_path(self, mock_run, mock_getenv):
        """Test get_disks function when HDSENTINEL_XML_PATH is set."""
        def getenv_side_effect(key, default=None):
            if key == 'HDSENTINEL_XML_PATH':
                return self.temp_xml_file.name
            return default
        mock_getenv.side_effect = getenv_side_effect
        
        disks = get_disks()
        
        # Verify that subprocess.run was not called
        mock_run.assert_not_called()
        
        # Check that we got the expected disks
        self.assertEqual(len(disks), 2)
        self.assertIn('S4EWNF0M123456', disks)
        self.assertIn('WD-WCC6Y5ABCDEF', disks)
        
        # Check disk details
        self.assertEqual(disks['S4EWNF0M123456']['Hard_Disk_Model_ID'], 'Samsung SSD 970 EVO Plus 1TB')
        self.assertEqual(disks['WD-WCC6Y5ABCDEF']['Hard_Disk_Health'], '95')

    @patch('app.hdsentinel_parser.os.getenv')
    @patch('app.hdsentinel_parser.subprocess.run')
    @patch('app.hdsentinel_parser.os.path.getsize', return_value=1)
    @patch('app.hdsentinel_parser.os.path.exists', return_value=True)
    @patch('app.hdsentinel_parser.ET.parse')
    def test_get_disks_with_subprocess(self, mock_parse, mock_exists, mock_getsize, mock_run, mock_getenv):
        """Test get_disks function when HDSENTINEL_XML_PATH is not set."""
        # Set up the mock to return None for HDSENTINEL_XML_PATH env var
        def getenv_side_effect(key, default=None):
            if key == 'HDSENTINEL_XML_PATH':
                return None
            return default
        mock_getenv.side_effect = getenv_side_effect
        
        # Set up ET.parse to return our test data
        mock_tree = MagicMock()
        mock_tree.findall.return_value = ET.fromstring(self.test_xml_content).findall('.//Hard_Disk_Summary')
        mock_parse.return_value = mock_tree
        
        disks = get_disks()
        
        # Verify that subprocess.run was called
        mock_run.assert_called_once()
        
        # Check that we got the expected disks
        self.assertEqual(len(disks), 2)

    def test_to_snake_case(self):
        """Test to_snake_case function."""
        self.assertEqual(to_snake_case("Samsung SSD 970 EVO Plus"), "samsung_ssd_970_evo_plus")
        self.assertEqual(to_snake_case("WDC WD10EZEX-00WN4A0"), "wdc_wd10_ezex_00_wn4_a0")
        self.assertEqual(to_snake_case("CamelCaseTest"), "camel_case_test")

    def test_to_safe_id(self):
        """Test to_safe_id function."""
        self.assertEqual(to_safe_id("S13PJ90S113060"), "s13pj90s113060")
        self.assertEqual(to_safe_id("WD-WCC6Y5ABCDEF"), "wd_wcc6y5abcdef")
        self.assertEqual(to_safe_id("Serial-With_Special!Chars@123"), "serial_with_special_chars_123")
        self.assertEqual(to_safe_id(""), "")

    def test_build_disk_alias(self):
        """Test build_disk_alias function."""
        # Test with typical disk models and serials
        self.assertEqual(
            build_disk_alias("SAMSUNG HD103UJ", "S13PJ90S113060"),
            "samsung_hd103_uj_s13pj90s113060"
        )
        self.assertEqual(
            build_disk_alias("WDC WD10EFRX-68FYTN0", "WD-WCC4J5HL2R45"),
            "wdc_wd10_efrx_68_fytn0_wd_wcc4j5hl2r45"
        )
        # Test with short serial
        self.assertEqual(
            build_disk_alias("Test Model", "SHORT"),
            "test_model_short"
        )
        # Test with empty/unknown values
        self.assertEqual(
            build_disk_alias("", "S13PJ90S113060"),
            "unknown_s13pj90s113060"
        )

    def test_check_if_number(self):
        """Test check_if_number function."""
        self.assertEqual(check_if_number("100%", int), "100")
        self.assertEqual(check_if_number("38 C", float), "38")
        self.assertEqual(check_if_number("No numbers here", int), "0")
        self.assertEqual(check_if_number("Text value", str), "Text value")

    def test_to_number(self):
        """Test to_number function."""
        self.assertEqual(to_number("100%"), "100")
        self.assertEqual(to_number("Temperature: 38 C"), "38")
        self.assertEqual(to_number("No numbers here"), "0")

    def test_isfloat(self):
        """Test isfloat function."""
        self.assertTrue(isfloat("100"))
        self.assertTrue(isfloat("38.5"))
        self.assertFalse(isfloat("38 C"))
        self.assertFalse(isfloat(""))

    @patch('app.hdsentinel_parser.publish')
    def test_mqtt_client(self, mock_publish):
        """Test MqttClient class."""
        client = MqttClient("localhost", 1883, {"username": "user", "password": "pass"}, True)
        
        # Test publish_single
        client.publish_single("test/topic", "test_payload")
        mock_publish.single.assert_called_once()
        
        # Test publish_multiple
        payloads = [{"topic": "test/topic1", "payload": "payload1"}, {"topic": "test/topic2", "payload": "payload2"}]
        client.publish_multiple(payloads)
        mock_publish.multiple.assert_called_once()

    @patch('app.hdsentinel_parser.publish')
    def test_ha_capable_mqtt_client(self, mock_publish):
        """Test HaCapableMqttClient class."""
        client = HaCapableMqttClient("hdsentinel/test_disk", broker_host="localhost", broker_port=1883)
        
        # Test get_abs_topic
        topic = client.get_abs_topic("status")
        self.assertEqual(topic, "hdsentinel/test_disk/status")
        
        # Test status_topic property
        self.assertEqual(client.status_topic, "hdsentinel/test_disk/availability")
        
        # Test publish_online_status
        client.publish_online_status()
        mock_publish.single.assert_called_with(
            "hdsentinel/test_disk/availability", "online", 
            hostname="localhost", port=1883, auth=None, client_id="hdsentinel_parser.py", retain=True
        )

    @patch('app.hdsentinel_parser.safe_load')
    @patch('app.hdsentinel_parser.Path.open', new_callable=mock_open, read_data='{}')
    def test_config(self, mock_file, mock_safe_load):
        """Test Config class."""
        # Mock the config.yml content
        mock_safe_load.return_value = {
            "sensor": {
                "health": {
                    "device_class": "battery",
                    "unit_of_measurement": "%",
                    "_key": "hard_disk_health",
                    "_type": "int"
                },
                "temperature": {
                    "device_class": "temperature",
                    "unit_of_measurement": "°C",
                    "_key": "temperature",
                    "_type": "int"
                }
            }
        }
        
        config = Config(
            "S4EWNF0M123456",
            "samsung_ssd_970_evo_plus_m123456",
            "Samsung SSD 970 EVO Plus 1TB",
            "2B2QEXM7",
            "hdsentinel/samsung_ssd_970_evo_plus_m123456/hdsentinel",
            "hdsentinel/samsung_ssd_970_evo_plus_m123456/availability"
        )
        
        # Check that sensors were created correctly
        self.assertEqual(len(config.sensors), 2)
        
        # Check that value_types were set correctly
        self.assertEqual(config.value_types["hard_disk_health"], int)
        self.assertEqual(config.value_types["temperature"], int)

    @patch('app.hdsentinel_parser.MqttClient.publish_single')
    def test_main_loop(self, mock_publish_single):
        """Test main_loop function."""
        # Create mock objects
        mock_mqtt_client = MagicMock(spec=HaCapableMqttClient)
        mock_config = MagicMock(spec=Config)
        
        # Test data
        disk_values = {
            "Hard_Disk_Number": "1",
            "Hard_Disk_Device": "/dev/nvme0n1",
            "Hard_Disk_Model_ID": "Samsung SSD 970 EVO Plus 1TB",
            "Hard_Disk_Serial_Number": "S4EWNF0M123456",
            "Hard_Disk_Health": "100",
            "Temperature": "38"
        }
        
        # Call the function
        main_loop(mock_mqtt_client, "hdsentinel/test_disk/hdsentinel", mock_config, disk_values)
        
        # Check that publish_single was called with the correct arguments
        expected_status = {key.lower(): value for key, value in disk_values.items()}
        mock_mqtt_client.publish_single.assert_called_with(
            "hdsentinel/test_disk/hdsentinel", 
            json.dumps(expected_status, sort_keys=True)
        )
        mock_mqtt_client.publish_online_status.assert_called_once()

    def test_duplicate_disk_models_unique_aliases(self):
        """Test that duplicate disk models get unique aliases based on serial numbers."""
        # Simulate two identical disk models with different serials
        model1 = "SAMSUNG HD103UJ"
        serial1 = "S13PJ90S113060"
        model2 = "SAMSUNG HD103UJ"
        serial2 = "S13PJ90S113054"
        
        alias1 = build_disk_alias(model1, serial1)
        alias2 = build_disk_alias(model2, serial2)
        
        # Verify aliases are different
        self.assertNotEqual(alias1, alias2)
        
        # Verify both contain the model part
        self.assertTrue(alias1.startswith("samsung_hd103_uj"))
        self.assertTrue(alias2.startswith("samsung_hd103_uj"))
        
        # Verify they contain different serial suffixes
        self.assertTrue(alias1.endswith("s13pj90s113060"))
        self.assertTrue(alias2.endswith("s13pj90s113054"))
        
        # Expected values
        self.assertEqual(alias1, "samsung_hd103_uj_s13pj90s113060")
        self.assertEqual(alias2, "samsung_hd103_uj_s13pj90s113054")


if __name__ == '__main__':
    unittest.main()
