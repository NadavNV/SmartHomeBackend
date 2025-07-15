import random
import unittest
from unittest.mock import MagicMock, patch
from validation.validators import (
    verify_type_and_range,
    validate_device_data,
    DEVICE_PARAMETERS, DEVICE_TYPES,
    AC_PARAMETERS,
    MIN_BRIGHTNESS, MAX_BRIGHTNESS,
    MIN_BATTERY, MAX_BATTERY,
    MIN_WATER_TEMP, MAX_WATER_TEMP,
    MIN_AC_TEMP, MAX_AC_TEMP,
    MIN_POSITION, MAX_POSITION,
    TIME_REGEX, COLOR_REGEX, LIGHT_PARAMETERS,
)


def int_to_hex_color(num: int) -> str:
    return "#" + hex(num)[2:].zfill(6)


class TestValidation(unittest.TestCase):

    def setUp(self):
        # Patch the logger as used in validators.py
        self.logger_patcher = patch('validation.validators.logger')
        self.mock_logger = self.logger_patcher.start()
        self.mock_logger.error = MagicMock()

    def tearDown(self):
        self.logger_patcher.stop()

    def test_verify_type_and_range_int_valid(self):
        self.assertTrue(verify_type_and_range(50, "temp", int, (49, 60))[0])

    def test_verify_type_and_range_int_out_of_range(self):
        self.assertFalse(verify_type_and_range(70, "temp", int, (49, 60))[0])
        self.mock_logger.error.assert_called()

    def test_verify_type_and_range_int_invalid_type(self):
        self.assertFalse(verify_type_and_range("abc", "temp", int, (49, 60))[0])
        self.mock_logger.error.assert_called()

    def test_verify_type_and_range_str_enum_valid(self):
        self.assertTrue(verify_type_and_range("on", "status", str, {"on", "off"})[0])

    def test_verify_type_and_range_str_enum_invalid(self):
        self.assertFalse(verify_type_and_range("maybe", "status", str, {"on", "off"})[0])
        self.mock_logger.error.assert_called()

    def test_verify_type_and_range_time(self):
        self.assertTrue(verify_type_and_range("14:30", "scheduled_on", str, "time")[0])
        self.assertFalse(verify_type_and_range("25:00", "scheduled_on", str, "time")[0])

    def test_verify_type_and_range_color(self):
        self.assertTrue(verify_type_and_range("#FFF", "color", str, "color")[0])
        self.assertTrue(verify_type_and_range("#ffcc00", "color", str, "color")[0])
        self.assertFalse(verify_type_and_range("blue", "color", str, "color")[0])
        num = 0
        while num < 2 ** 24:
            with self.subTest(num=num, color=int_to_hex_color(num)):
                self.assertTrue(verify_type_and_range(int_to_hex_color(num), "color", str, "color")[0])
                if num < 2 ** 12:
                    self.assertTrue(verify_type_and_range("#" + hex(num)[2:].zfill(3), "color", str, "color")[0])
                # Random step to test many different numbers without running out of memory
                num += random.randint(400, 1000)

    def test_verify_type_and_range_wrong_type(self):
        self.assertFalse(verify_type_and_range(123, "status", str, {"on", "off"})[0])
        self.mock_logger.error.assert_called()

    def test_validate_device_data_valid_light(self):
        device = {
            "id": "light01",
            "type": "light",
            "room": "kitchen",
            "name": "Ceiling Light",
            "status": "on",
            "parameters": {
                "brightness": (MIN_BRIGHTNESS + MAX_BRIGHTNESS) // 2,
                "color": "#FFAA00",
                "is_dimmable": True,
                "dynamic_color": False
            }
        }
        self.assertTrue(validate_device_data(device, new_device=True)[0])

    def test_validate_device_data_invalid_light_extra_param(self):
        device = {
            "id": "light02",
            "type": "light",
            "room": "kitchen",
            "name": "Light",
            "status": "on",
            "parameters": {
                "brightness": (MIN_BRIGHTNESS + MAX_BRIGHTNESS) // 2,
                "color": "#000",
                "random_param": True
            }
        }
        result = validate_device_data(device, new_device=True)
        self.assertFalse(result[0])
        self.assertEqual(len(result[1]), 1, "Incorrect number of errors")
        self.assertIn(f"Disallowed parameters for light {{'random_param'}}, allowed parameters: {LIGHT_PARAMETERS}",
                      result[1], "Incorrect error message")
        self.mock_logger.error.assert_called()

    def test_validate_device_data_invalid_device_type(self):
        device = {
            "id": "device01",
            "type": "microwave",
            "room": "kitchen",
            "name": "My Microwave",
            "status": "on",
            "parameters": {}
        }
        result = validate_device_data(device, new_device=True)
        self.assertFalse(result[0])
        self.assertEqual(len(result[1]), 1, "Incorrect number of errors")
        self.assertIn(f"Incorrect device type microwave, must be one of {DEVICE_TYPES}.", result[1],
                      "Incorrect error message")
        self.mock_logger.error.assert_called()

    def test_validate_device_data_missing_field(self):
        device = {
            "id": "device02",
            "type": "light",
            "room": "kitchen",
            "name": "Light",
            "parameters": {}
        }
        result = validate_device_data(device, new_device=True)
        self.assertFalse(result[0])
        self.assertEqual(len(result[1]), 1, "Incorrect number of errors")
        self.assertIn((f"Incorrect field(s) in new device: {set(device.keys()) - DEVICE_PARAMETERS}, "
                       f"missing field(s) in new device: {DEVICE_PARAMETERS - set(device.keys())}, "
                       f"must be exactly these fields: {DEVICE_PARAMETERS}"), result[1], "Incorrect error message")
        self.mock_logger.error.assert_called()

    def test_time_regex(self):
        self.assertRegex("23:59", TIME_REGEX)
        self.assertNotRegex("24:00", TIME_REGEX)

    def test_color_regex(self):
        self.assertRegex("#abc", COLOR_REGEX)
        self.assertRegex("#A1B2C3", COLOR_REGEX)
        self.assertNotRegex("abc", COLOR_REGEX)

    def test_valid_door_lock(self):
        device = {
            "status": "locked",
            "parameters": {
                "battery_level": (MIN_BATTERY + MAX_BATTERY) // 2
            }
        }
        result = validate_device_data(device, device_type="door_lock")
        self.assertEqual(result, (True, []))

    def test_invalid_device_type(self):
        device = {
            "status": "on",
            "parameters": {}
        }
        result = validate_device_data(device, device_type="smart_toaster")
        self.assertFalse(result[0])
        self.assertEqual(len(result[1]), 1, "Incorrect number of errors")
        self.assertIn(f"Incorrect device type smart_toaster, must be one of {DEVICE_TYPES}.", result[1],
                      "Incorrect error message")
        self.mock_logger.error.assert_called()

    def test_invalid_status_value(self):
        device = {
            "status": "halfway",  # should be 'open' or 'closed'
            "parameters": {
                "position": (MIN_POSITION + MAX_POSITION) // 2
            }
        }
        result = validate_device_data(device, device_type="curtain")
        self.assertFalse(result[0])
        self.assertEqual(len(result[1]), 1, "Incorrect number of errors")
        self.assertIn(f"'halfway' is not a valid value for 'status'. Must be one of { {"open", "closed"} }.", result[1],
                      "Incorrect error message")
        self.mock_logger.error.assert_called()

    def test_extra_parameter_in_air_conditioner(self):
        device = {
            "status": "on",
            "parameters": {
                "temperature": (MIN_AC_TEMP + MAX_AC_TEMP) // 2,
                "mode": "cool",
                "fan_speed": "medium",
                "swing": "auto",
                "invalid_key": "unexpected"
            }
        }
        result = validate_device_data(device, device_type="air_conditioner")
        self.assertFalse(result[0])
        self.assertEqual(len(result[1]), 1, "Incorrect number of errors")
        self.assertIn(f"Disallowed parameters for air conditioner {set(device["parameters"].keys() - AC_PARAMETERS)}, "
                      f"allowed parameters: {AC_PARAMETERS}", result[1], "Incorrect error message")
        self.mock_logger.error.assert_called()

    def test_missing_required_parameters(self):
        device = {
            "status": "open",
            "parameters": {}
        }
        # This may pass if `position` is not strictly required,
        # but if it's expected, adjust accordingly
        result = validate_device_data(device, device_type="curtain")
        self.assertEqual(result[0], True)

    def test_valid_light(self):
        device = {
            "status": "off",
            "parameters": {
                "brightness": (MAX_BRIGHTNESS + MIN_BRIGHTNESS) // 2,
                "color": "#FF00FF",
            }
        }
        result = validate_device_data(device, device_type="light")
        self.assertEqual(result, (True, []))

    def test_invalid_light_too_bright(self):
        device = {
            "status": "off",
            "parameters": {
                "brightness": MAX_BRIGHTNESS + 1,
                "color": "#FF00FF",
            }
        }
        result = validate_device_data(device, device_type="light")
        self.assertFalse(result[0])
        self.assertEqual(len(result[1]), 1, "Incorrect number of errors")
        self.assertIn(
            f"'brightness' must be between {MIN_BRIGHTNESS} and {MAX_BRIGHTNESS}, got {MAX_BRIGHTNESS + 1} instead.",
            result[1], "Incorrect error message")
        self.mock_logger.error.assert_called()

    def test_invalid_light_too_dim(self):
        device = {
            "status": "off",
            "parameters": {
                "brightness": MIN_BRIGHTNESS - 1,
                "color": "#FF00FF",
            }
        }
        result = validate_device_data(device, device_type="light")
        self.assertFalse(result[0])
        self.assertEqual(len(result[1]), 1, "Incorrect number of errors")
        self.assertIn(
            f"'brightness' must be between {MIN_BRIGHTNESS} and {MAX_BRIGHTNESS}, got {MIN_BRIGHTNESS - 1} instead.",
            result[1], "Incorrect error message")
        self.mock_logger.error.assert_called()

    def test_invalid_light_read_only_parameters(self):
        device = {
            "status": "off",
            "parameters": {
                "brightness": (MAX_BRIGHTNESS + MIN_BRIGHTNESS) // 2,
                "color": "#FF00FF",
                "is_dimmable": True,
            }
        }
        result = validate_device_data(device, device_type="light")
        self.assertFalse(result[0])
        self.assertEqual(len(result[1]), 1, "Incorrect number of errors")
        self.assertIn(f"Cannot update read-only parameter 'is_dimmable'.", result[1], "Incorrect error message")
        self.mock_logger.error.assert_called()

    def test_valid_water_heater(self):
        device = {
            "status": "on",
            "parameters": {
                "temperature": (MIN_WATER_TEMP + MAX_WATER_TEMP) // 2,
                "target_temperature": (MIN_WATER_TEMP + MAX_WATER_TEMP) // 2,
                "is_heating": True,
                "timer_enabled": False,
                "scheduled_on": "08:00",
                "scheduled_off": "10:00"
            }
        }
        result = validate_device_data(device, device_type="water_heater")
        self.assertEqual(result, (True, []))

    def test_invalid_water_heater_too_hot(self):
        device = {
            "status": "on",
            "parameters": {
                "temperature": (MIN_WATER_TEMP + MAX_WATER_TEMP) // 2,
                "target_temperature": MAX_WATER_TEMP + 1,
                "is_heating": True,
                "timer_enabled": False,
                "scheduled_on": "08:00",
                "scheduled_off": "10:00"
            }
        }
        result = validate_device_data(device, device_type="water_heater")
        self.assertFalse(result[0])
        self.assertEqual(len(result[1]), 1, "Incorrect number of errors")
        self.assertIn(f"'target_temperature' must be between {MIN_WATER_TEMP} and {MAX_WATER_TEMP}, got "
                      f"{MAX_WATER_TEMP + 1} instead.", result[1], "Incorrect error message")
        self.mock_logger.error.assert_called()

    def test_invalid_water_heater_too_cold(self):
        device = {
            "status": "on",
            "parameters": {
                "temperature": (MIN_WATER_TEMP + MAX_WATER_TEMP) // 2,
                "target_temperature": MIN_WATER_TEMP - 1,
                "is_heating": True,
                "timer_enabled": False,
                "scheduled_on": "08:00",
                "scheduled_off": "10:00"
            }
        }
        result = validate_device_data(device, device_type="water_heater")
        self.assertFalse(result[0])
        self.assertEqual(len(result[1]), 1, "Incorrect number of errors")
        self.assertIn(f"'target_temperature' must be between {MIN_WATER_TEMP} and {MAX_WATER_TEMP}, got "
                      f"{MIN_WATER_TEMP - 1} instead.", result[1], "Incorrect error message")
        self.mock_logger.error.assert_called()

    def test_valid_ac(self):
        device = {
            "status": "on",
            "parameters": {
                "temperature": (MIN_AC_TEMP + MAX_AC_TEMP) // 2,
                "mode": "cool",
                "fan_speed": "medium",
                "swing": "auto"
            }
        }
        result = validate_device_data(device, device_type="air_conditioner")
        self.assertEqual(result, (True, []))

    def test_invalid_ac_too_hot(self):
        device = {
            "status": "on",
            "parameters": {
                "temperature": MAX_AC_TEMP + 1,
                "mode": "cool",
                "fan_speed": "medium",
                "swing": "auto"
            }
        }
        result = validate_device_data(device, device_type="air_conditioner")
        self.assertFalse(result[0])
        self.assertEqual(len(result[1]), 1, "Incorrect number of errors")
        self.assertIn(f"'temperature' must be between {MIN_AC_TEMP} and {MAX_AC_TEMP}, got "
                      f"{MAX_AC_TEMP + 1} instead.", result[1], "Incorrect error message")
        self.mock_logger.error.assert_called()

    def test_invalid_ac_too_cold(self):
        device = {
            "status": "on",
            "parameters": {
                "temperature": MIN_AC_TEMP - 1,
                "mode": "cool",
                "fan_speed": "medium",
                "swing": "auto"
            }
        }
        result = validate_device_data(device, device_type="air_conditioner")
        self.assertFalse(result[0])
        self.assertEqual(len(result[1]), 1, "Incorrect number of errors")
        self.assertIn(f"'temperature' must be between {MIN_AC_TEMP} and {MAX_AC_TEMP}, got "
                      f"{MIN_AC_TEMP - 1} instead.", result[1], "Incorrect error message")
        self.mock_logger.error.assert_called()

    def test_update_id_and_type(self):
        device = {
            "id": "test",
            "type": "light",
        }
        result = validate_device_data(device, device_type="light")
        self.assertFalse(result[0])
        self.assertEqual(len(result[1]), 2, "Incorrect number of errors")
        self.assertIn("Cannot update read-only parameter 'id'", result[1], "Incorrect error message")
        self.assertIn("Cannot update read-only parameter 'type'", result[1], "Incorrect error message")


if __name__ == '__main__':
    unittest.main()
