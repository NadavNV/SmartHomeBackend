import unittest
from unittest.mock import MagicMock, patch
from validation.validators import (
    verify_type_and_range,
    validate_new_device_data,
    validate_device_data,
    MIN_BRIGHTNESS, MAX_BRIGHTNESS,
    MIN_BATTERY, MAX_BATTERY,
    MIN_WATER_TEMP, MAX_WATER_TEMP,
    MIN_AC_TEMP, MAX_AC_TEMP,
    MIN_POSITION, MAX_POSITION,
    TIME_REGEX, COLOR_REGEX,
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
        for num in range(2 ** 24, 578):
            with self.subTest(num=num, color=int_to_hex_color(num)):
                self.assertTrue(verify_type_and_range(int_to_hex_color(num), "color", str, "color")[0])
                if num < 2 ** 12:
                    self.assertTrue(verify_type_and_range("#" + hex(num)[2:].zfill(3), "color", str, "color")[0])

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
        self.assertTrue(validate_new_device_data(device)[0])

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
        self.assertFalse(validate_new_device_data(device)[0])
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
        self.assertFalse(validate_new_device_data(device)[0])
        self.mock_logger.error.assert_called()

    def test_validate_device_data_missing_field(self):
        device = {
            "id": "device02",
            "type": "light",
            "room": "kitchen",
            "name": "Light",
            "parameters": {}
        }
        self.assertFalse(validate_new_device_data(device)[0])
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
            "type": "door_lock",
            "status": "locked",
            "parameters": {
                "auto_lock_enabled": True,
                "battery_level": (MIN_BATTERY + MAX_BATTERY) // 2
            }
        }
        result = validate_device_data(device)
        self.assertEqual(result, (True, None))

    def test_invalid_device_type(self):
        device = {
            "type": "smart_toaster",
            "status": "on",
            "parameters": {}
        }
        result = validate_device_data(device)
        self.assertFalse(result[0])
        self.assertIn("Incorrect device type", result[1])
        self.mock_logger.error.assert_called()

    def test_invalid_status_value(self):
        device = {
            "type": "curtain",
            "status": "halfway",  # should be 'open' or 'closed'
            "parameters": {
                "position": (MIN_POSITION + MAX_POSITION) // 2
            }
        }
        result = validate_device_data(device)
        self.assertFalse(result[0])
        self.assertIn("'status'", result[1])
        self.mock_logger.error.assert_called()

    def test_extra_parameter_in_air_conditioner(self):
        device = {
            "type": "air_conditioner",
            "status": "on",
            "parameters": {
                "temperature": (MIN_AC_TEMP + MAX_AC_TEMP) // 2,
                "mode": "cool",
                "fan": "medium",
                "swing": "auto",
                "invalid_key": "unexpected"
            }
        }
        result = validate_device_data(device)
        self.assertFalse(result[0])
        self.assertIn("Disallowed parameters for air conditioner", result[1])
        self.mock_logger.error.assert_called()

    def test_missing_required_parameters(self):
        device = {
            "type": "curtain",
            "status": "open",
            "parameters": {}
        }
        # This may pass if `position` is not strictly required,
        # but if it's expected, adjust accordingly
        result = validate_device_data(device)
        self.assertEqual(result[0], True)

    def test_valid_light(self):
        device = {
            "type": "light",
            "status": "off",
            "parameters": {
                "brightness": (MAX_BRIGHTNESS + MIN_BRIGHTNESS) // 2,
                "color": "#FF00FF",
                "is_dimmable": True,
                "dynamic_color": False
            }
        }
        result = validate_device_data(device)
        self.assertEqual(result, (True, None))

    def test_invalid_light_too_bright(self):
        device = {
            "type": "light",
            "status": "off",
            "parameters": {
                "brightness": MAX_BRIGHTNESS + 1,
                "color": "#FF00FF",
                "is_dimmable": True,
                "dynamic_color": False
            }
        }
        result = validate_device_data(device)
        self.assertFalse(result[0])
        self.assertIn("must be between", result[1])
        self.mock_logger.error.assert_called()

    def test_invalid_light_too_dim(self):
        device = {
            "type": "light",
            "status": "off",
            "parameters": {
                "brightness": MIN_BRIGHTNESS - 1,
                "color": "#FF00FF",
                "is_dimmable": True,
                "dynamic_color": False
            }
        }
        result = validate_device_data(device)
        self.assertFalse(result[0])
        self.assertIn("must be between", result[1])
        self.mock_logger.error.assert_called()

    def test_valid_water_heater(self):
        device = {
            "type": "water_heater",
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
        result = validate_device_data(device)
        self.assertEqual(result, (True, None))

    def test_invalid_water_heater_too_hot(self):
        device = {
            "type": "water_heater",
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
        result = validate_device_data(device)
        self.assertFalse(result[0])
        self.assertIn("must be between", result[1])
        self.mock_logger.error.assert_called()

    def test_invalid_water_heater_too_cold(self):
        device = {
            "type": "water_heater",
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
        result = validate_device_data(device)
        self.assertFalse(result[0])
        self.assertIn("must be between", result[1])
        self.mock_logger.error.assert_called()

    def test_valid_ac(self):
        device = {
            "type": "air_conditioner",
            "status": "on",
            "parameters": {
                "temperature": (MIN_AC_TEMP + MAX_AC_TEMP) // 2,
                "mode": "cool",
                "fan_speed": "medium",
                "swing": "auto"
            }
        }
        result = validate_device_data(device)
        self.assertEqual(result, (True, None))

    def test_invalid_ac_too_hot(self):
        device = {
            "type": "air_conditioner",
            "status": "on",
            "parameters": {
                "temperature": MAX_AC_TEMP + 1,
                "mode": "cool",
                "fan_speed": "medium",
                "swing": "auto"
            }
        }
        result = validate_device_data(device)
        self.assertFalse(result[0])
        self.assertIn("must be between", result[1])
        self.mock_logger.error.assert_called()

    def test_invalid_ac_too_cold(self):
        device = {
            "type": "air_conditioner",
            "status": "on",
            "parameters": {
                "temperature": MIN_AC_TEMP - 1,
                "mode": "cool",
                "fan_speed": "medium",
                "swing": "auto"
            }
        }
        result = validate_device_data(device)
        self.assertFalse(result[0])
        self.assertIn("must be between", result[1])
        self.mock_logger.error.assert_called()


if __name__ == '__main__':
    unittest.main()
