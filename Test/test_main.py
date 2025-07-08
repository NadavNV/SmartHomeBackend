import unittest
import logging
from unittest.mock import MagicMock, patch
from main import (
    verify_type_and_range,
    validate_device_data,
    MIN_BRIGHTNESS, MAX_BRIGHTNESS,
    MIN_BATTERY, MAX_BATTERY,
    MIN_WATER_TEMP, MAX_WATER_TEMP,
    MIN_POSITION, MAX_POSITION,
    TIME_REGEX, COLOR_REGEX,
)


class TestValidation(unittest.TestCase):

    def setUp(self):
        # Patch app.logger for all tests
        self.patcher = patch('main.app')
        self.mock_app = self.patcher.start()
        self.mock_logger = self.mock_app.logger
        self.mock_logger.error = MagicMock()

    def tearDown(self):
        self.patcher.stop()

    def test_verify_type_and_range_int_valid(self):
        self.assertTrue(verify_type_and_range(50, "temp", int, (49, 60)))

    def test_verify_type_and_range_int_out_of_range(self):
        self.assertFalse(verify_type_and_range(70, "temp", int, (49, 60)))
        self.mock_logger.error.assert_called()

    def test_verify_type_and_range_int_invalid_type(self):
        self.assertFalse(verify_type_and_range("abc", "temp", int, (49, 60)))
        self.mock_logger.error.assert_called()

    def test_verify_type_and_range_str_enum_valid(self):
        self.assertTrue(verify_type_and_range("on", "status", str, {"on", "off"}))

    def test_verify_type_and_range_str_enum_invalid(self):
        self.assertFalse(verify_type_and_range("maybe", "status", str, {"on", "off"}))
        self.mock_logger.error.assert_called()

    def test_verify_type_and_range_time(self):
        self.assertTrue(verify_type_and_range("14:30", "scheduled_on", str, "time"))
        self.assertFalse(verify_type_and_range("25:00", "scheduled_on", str, "time"))

    def test_verify_type_and_range_color(self):
        self.assertTrue(verify_type_and_range("#FFF", "color", str, "color"))
        self.assertTrue(verify_type_and_range("#ffcc00", "color", str, "color"))
        self.assertFalse(verify_type_and_range("blue", "color", str, "color"))

    def test_verify_type_and_range_wrong_type(self):
        self.assertFalse(verify_type_and_range(123, "status", str, {"on", "off"}))
        self.mock_logger.error.assert_called()

    def test_validate_device_data_valid_light(self):
        device = {
            "id": "light01",
            "type": "light",
            "room": "kitchen",
            "name": "Ceiling Light",
            "status": "on",
            "parameters": {
                "brightness": 75,
                "color": "#FFAA00",
                "is_dimmable": True,
                "dynamic_color": False
            }
        }
        self.assertTrue(validate_device_data(device))

    def test_validate_device_data_invalid_light_extra_param(self):
        device = {
            "id": "light02",
            "type": "light",
            "room": "kitchen",
            "name": "Light",
            "status": "on",
            "parameters": {
                "brightness": 80,
                "color": "#000",
                "random_param": True
            }
        }
        self.assertFalse(validate_device_data(device))

    def test_validate_device_data_invalid_device_type(self):
        device = {
            "id": "device01",
            "type": "microwave",
            "room": "kitchen",
            "name": "My Microwave",
            "status": "on",
            "parameters": {}
        }
        self.assertFalse(validate_device_data(device))

    def test_validate_device_data_missing_field(self):
        device = {
            "id": "device02",
            "type": "light",
            "room": "kitchen",
            "name": "Light",
            "parameters": {}
        }
        self.assertFalse(validate_device_data(device))

    def test_time_regex(self):
        self.assertRegex("23:59", TIME_REGEX)
        self.assertNotRegex("24:00", TIME_REGEX)

    def test_color_regex(self):
        self.assertRegex("#abc", COLOR_REGEX)
        self.assertRegex("#A1B2C3", COLOR_REGEX)
        self.assertNotRegex("abc", COLOR_REGEX)


if __name__ == '__main__':
    unittest.main()
