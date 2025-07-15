import config.env  # noqa: F401  # load_dotenv side effect
import re
import logging
import os
import json
from typing import Any, Mapping

logger = logging.getLogger("smart_home.validation.validators")

# Minimum temperature (Celsius) for water heater
MIN_WATER_TEMP = int(os.getenv('VITE_MIN_WATER_TEMP', 49))
# Maximum temperature (Celsius) for water heater
MAX_WATER_TEMP = int(os.getenv('VITE_MAX_WATER_TEMP', 60))
# Minimum temperature (Celsius) for air conditioner
MIN_AC_TEMP = int(os.getenv('VITE_MIN_AC_TEMP', 16))
# Maximum temperature (Celsius) for air conditioner
MAX_AC_TEMP = int(os.getenv('VITE_MAX_AC_TEMP', 30))
# Minimum brightness for dimmable light
MIN_BRIGHTNESS = int(os.getenv('VITE_MIN_BRIGHTNESS', 0))
# Maximum brightness for dimmable light
MAX_BRIGHTNESS = int(os.getenv("VITE_MAX_BRIGHTNESS", 100))
# Minimum position for curtain
MIN_POSITION = int(os.getenv("MIN_POSITION", 0))
# Maximum position for curtain
MAX_POSITION = int(os.getenv("MAX_POSITION", 100))
# Minimum value for battery level
MIN_BATTERY = int(os.getenv("MIN_BATTERY", 0))
# Maximum value for battery level
MAX_BATTERY = int(os.getenv("MAX_BATTERY", 100))

DEVICE_TYPES = set(json.loads(os.getenv("DEVICE_TYPES", '["light","water_heater","air_conditioner","door_lock",'
                                                        '"curtain"]')))
DEVICE_PARAMETERS = set(
    json.loads(os.getenv("DEVICE_PARAMETERS", '["id","type","room","name","status","parameters"]')))
WATER_HEATER_PARAMETERS = set(json.loads(os.getenv("WATER_HEATER_PARAMETERS", '["temperature","target_temperature",'
                                                                              '"is_heating","timer_enabled",'
                                                                              '"scheduled_on","scheduled_off"]')))
LIGHT_PARAMETERS = set(
    json.loads(os.getenv("LIGHT_PARAMETERS", '["brightness","color","is_dimmable","dynamic_color"]'))
)
AC_PARAMETERS = set(json.loads(os.getenv("AC_PARAMETERS", '["temperature","mode","fan_speed","swing"]')))
AC_MODES = set(json.loads(os.getenv("AC_MODES", '["cool","heat","fan"]')))
AC_FAN_SETTINGS = set(json.loads(os.getenv("AC_FAN_SETTINGS", '["off","low","medium","high"]')))
AC_SWING_MODES = set(json.loads(os.getenv("AC_SWING_MODES", '["off","on","auto"]')))
LOCK_PARAMETERS = set(json.loads(os.getenv("LOCK_PARAMETERS", '["auto_lock_enabled","battery_level"]')))
CURTAIN_PARAMETERS = set(json.loads(os.getenv("CURTAIN_PARAMETERS", '["position"]')))
# Regex explanation:
#
# ([01][0-9]|2[0-3]) - Hours. Either a 2 followed by 0-3 or an initial digit
#                      of 0 or 1 followed by any digit.
# : - Colon.
# ([0-5][0-9]) - Minutes, 0-5 followed by any digit.
# (:[0-5][0-9])? - Optional seconds
TIME_REGEX = os.getenv("VITE_TIME_REGEX", '^([01][0-9]|2[0-3]):([0-5][0-9])(:[0-5][0-9])?$')
COLOR_REGEX = os.getenv("VITE_COLOR_REGEX", '^#([0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$')


# Verify that the given string is a correct ISO format time string
def verify_time_string(string: str) -> bool:
    """
    Verify that the given string is a correct ISO format time string.

    :param str string: The string to verify.
    :return: True if the string is a correct ISO format time string, False otherwise.
    :rtype: bool
    """
    return bool(re.match(TIME_REGEX, string))


def verify_type_and_range(value: Any, name: str, cls: type,
                          value_range: tuple[int, int] | set[str] | str | None = None) -> tuple[bool, str | None]:
    """
    This function verifies that 'value' is of type 'cls', and when relevant that it is within
    an allowed range of values.

    If 'cls' is int, then value_range maybe a tuple of (min_value, max_value). If 'cls' is
    str, then 'value_range' may be a set of allowed values. If 'cls' is str and 'value_range'
    is the string 'time' then 'value' must be a valid ISO format time string without seconds.
    if 'cls' is str and 'value_range' is the string 'color' then 'value' must be a valid
    HTML RGB string.
    :param Any value: The value to be checked.
    :param str name: The name associated with the value, for error messages.
    :param type cls: The type to check against.
    :param tuple[int, int] | set[str] | str | None value_range: The value of 'value' is expected
        to fall within this range, if given.
    :return: A tuple of a boolean value indicating success and an optional reason for failure,
        or None on success. The boolean value is True if 'value' is of type 'cls' and matches the
        given 'value_range', False otherwise.
    :rtype: tuple[bool, str | None]
    """
    if cls == int:
        try:
            value = int(value)
        except ValueError:
            error = f"{name} must be a numeric string, got {value} instead."
            logger.error(error)
            return False, error
        if value_range is not None:
            minimum, maximum = value_range
            if value > maximum or value < minimum:
                error = f"{name} must be between {minimum} and {maximum}, got {value} instead."
                logger.error(error)
                return False, error
        return True, None

    if type(value) is not cls:
        error = f"{name} must be a {cls}, got {type(value)} instead."
        logger.error(error)
        return False, error
    if cls == str:
        if type(value_range) is set:
            if value not in value_range:
                error = f"'{value}' is not a valid value for {name}. Must be one of {value_range}."
                logger.error(error)
                return False, error
        elif value_range == 'time':
            return (bool(re.match(TIME_REGEX, value)),
                    None if re.match(TIME_REGEX, value) else f"'{value}' is not a valid ISO format time string.")
        elif value_range == 'color':
            return (bool(re.match(COLOR_REGEX, value)),
                    None if re.match(COLOR_REGEX, value) else f"'{value}' is not a valid hex color string.")
    return True, None


def validate_device_data(device_data: Mapping[str, Any], *, new_device: bool = False, device_type: str = "") -> \
        tuple[bool, list[str]]:
    """
    Verifies that new device data, either for an update or for a new device, is valid.

    :param device_data: The device data to verify
    :type device_data: Mapping[str, Any]
    :param new_device: Whether this is complete data for a new device, or partial data for a device update.
        Default value is False.
    :type new_device: bool
    :param device_type: If the data is for an update, the device type must be provided.
    :type device_type: str
    :return: A tuple of a boolean value indicating success and a list of reasons for failure.
    :rtype: tuple[bool, list[str]]
    """
    errors = []
    if new_device:
        if set(device_data.keys()) != DEVICE_PARAMETERS:
            error = (f"Incorrect field(s) in new device: {set(device_data.keys()) - DEVICE_PARAMETERS}, "
                     f"missing field(s) in new device: {DEVICE_PARAMETERS - set(device_data.keys())}, "
                     f"must be exactly these fields: {DEVICE_PARAMETERS}")
            logger.error(error)
            return False, [error]
        device_type = device_data["type"]
    else:
        if "id" in device_data:
            error = "Cannot update read-only parameter 'id'"
            logger.error(error)
            errors.append(error)
        if "type" in device_data:
            error = "Cannot update read-only parameter 'type'"
            logger.error(error)
            errors.append(error)
        if errors:
            return False, errors
    if device_type not in DEVICE_TYPES:
        error = f"Incorrect device type {device_type}, must be one of {DEVICE_TYPES}."
        logger.error(error)
        return False, [error]
    for field in device_data:
        if field == 'status':
            if device_type in DEVICE_TYPES:
                match device_type:
                    case "door_lock":
                        success, reason = verify_type_and_range(
                            value=device_data['status'],
                            name="'status'",
                            cls=str,
                            value_range={'unlocked', 'locked'},
                        )
                        if not success:
                            errors.append(reason)
                    case "curtain":
                        success, reason = verify_type_and_range(
                            value=device_data['status'],
                            name="'status'",
                            cls=str,
                            value_range={'open', 'closed'},
                        )
                        if not success:
                            errors.append(reason)
                    case _:
                        success, reason = verify_type_and_range(
                            value=device_data['status'],
                            name="'status'",
                            cls=str,
                            value_range={'on', 'off'},
                        )
                        if not success:
                            errors.append(reason)
        if field == 'parameters':
            if device_type in DEVICE_TYPES:
                success, reason = verify_type_and_range(
                    value=device_data['parameters'],
                    name="'parameters'",
                    cls=dict,
                )
                if not success:
                    errors.append(reason)
                    continue
                left_over_parameters = set(device_data['parameters'].keys())
                match device_type:
                    case "door_lock":
                        left_over_parameters -= LOCK_PARAMETERS
                        if left_over_parameters != set():
                            error = (f"Disallowed parameters for door lock {left_over_parameters}, "
                                     f"allowed parameters: {LOCK_PARAMETERS}")
                            logger.error(error)
                            errors.append(error)
                            continue
                        for key, value in device_data['parameters'].items():
                            if key == 'auto_lock_enabled':
                                if new_device:
                                    success, reason = verify_type_and_range(
                                        value=value,
                                        name="'auto_lock_enabled'",
                                        cls=bool,
                                    )
                                    if not success:
                                        errors.append(reason)
                                else:
                                    error = f"Cannot update read-only parameter '{key}'."
                                    logger.error(error)
                                    errors.append(error)
                            elif key == 'battery_level':
                                success, reason = verify_type_and_range(
                                    value=value,
                                    name="'battery_level'",
                                    cls=int,
                                    value_range=(MIN_BATTERY, MAX_BATTERY),
                                )
                                if not success:
                                    errors.append(reason)
                    case "curtain":
                        left_over_parameters -= CURTAIN_PARAMETERS
                        if left_over_parameters != set():
                            error = (f"Disallowed parameters for curtain {left_over_parameters}, "
                                     f"allowed parameters: {CURTAIN_PARAMETERS}")
                            logger.error(error)
                            errors.append(error)
                            continue
                        for key, value in device_data['parameters'].items():
                            if key == 'position':
                                success, reason = verify_type_and_range(
                                    value=value,
                                    name="'position'",
                                    cls=int,
                                    value_range=(MIN_POSITION, MAX_POSITION),
                                )
                                if not success:
                                    errors.append(reason)
                    case "air_conditioner":
                        left_over_parameters -= AC_PARAMETERS
                        if left_over_parameters != set():
                            error = (f"Disallowed parameters for air conditioner {left_over_parameters}, "
                                     f"allowed parameters: {AC_PARAMETERS}")
                            logger.error(error)
                            errors.append(error)
                            continue
                        for key, value in device_data['parameters'].items():
                            if key == 'temperature':
                                success, reason = verify_type_and_range(
                                    value=value,
                                    name="'temperature'",
                                    cls=int,
                                    value_range=(MIN_AC_TEMP, MAX_AC_TEMP),
                                )
                                if not success:
                                    errors.append(reason)
                            elif key == 'mode':
                                success, reason = verify_type_and_range(
                                    value=value,
                                    name="'mode'",
                                    cls=str,
                                    value_range=AC_MODES,
                                )
                                if not success:
                                    errors.append(reason)
                            elif key == 'fan':
                                success, reason = verify_type_and_range(
                                    value=value,
                                    name="'fan'",
                                    cls=str,
                                    value_range=AC_FAN_SETTINGS,
                                )
                                if not success:
                                    errors.append(reason)
                            elif key == 'swing':
                                success, reason = verify_type_and_range(
                                    value=value,
                                    name="'swing'",
                                    cls=str,
                                    value_range=AC_SWING_MODES,
                                )
                                if not success:
                                    errors.append(reason)
                    case "water_heater":
                        left_over_parameters -= WATER_HEATER_PARAMETERS
                        if left_over_parameters != set():
                            error = (f"Disallowed parameters for water heater {left_over_parameters}, "
                                     f"allowed parameters: {WATER_HEATER_PARAMETERS}")
                            logger.error(error)
                            errors.append(error)
                            continue
                        for key, value in device_data['parameters'].items():
                            if key == 'temperature':
                                success, reason = verify_type_and_range(
                                    value=value,
                                    name="'temperature'",
                                    cls=int,
                                )
                                if not success:
                                    errors.append(reason)
                            elif key == 'target_temperature':
                                success, reason = verify_type_and_range(
                                    value=value,
                                    name="'target_temperature'",
                                    cls=int,
                                    value_range=(MIN_WATER_TEMP, MAX_WATER_TEMP),
                                )
                                if not success:
                                    errors.append(reason)
                            elif key == 'is_heating':
                                success, reason = verify_type_and_range(
                                    value=value,
                                    name="'is_heating'",
                                    cls=bool,
                                )
                                if not success:
                                    errors.append(reason)
                            elif key == 'timer_enabled':
                                success, reason = verify_type_and_range(
                                    value=value,
                                    name="'timer_enabled'",
                                    cls=bool,
                                )
                                if not success:
                                    errors.append(reason)
                            elif key == 'scheduled_on':
                                success, reason = verify_type_and_range(
                                    value=value,
                                    name="'scheduled_on'",
                                    cls=str,
                                    value_range='time'
                                )
                                if not success:
                                    errors.append(reason)
                            elif key == 'scheduled_off':
                                success, reason = verify_type_and_range(
                                    value=value,
                                    name="'scheduled_off'",
                                    cls=str,
                                    value_range='time'
                                )
                                if not success:
                                    errors.append(reason)
                    case "light":
                        left_over_parameters -= LIGHT_PARAMETERS
                        if left_over_parameters != set():
                            error = (f"Disallowed parameters for light {left_over_parameters}, "
                                     f"allowed parameters: {LIGHT_PARAMETERS}")
                            logger.error(error)
                            errors.append(error)
                            continue
                        for key, value in device_data['parameters'].items():
                            if key == 'brightness':
                                success, reason = verify_type_and_range(
                                    value=value,
                                    name="'brightness'",
                                    cls=int,
                                    value_range=(MIN_BRIGHTNESS, MAX_BRIGHTNESS),
                                )
                                if not success:
                                    errors.append(reason)
                            elif key == 'color':
                                success, reason = verify_type_and_range(
                                    value=value,
                                    name="'color'",
                                    cls=str,
                                    value_range='color',
                                )
                                if not success:
                                    errors.append(reason)
                            elif key == 'is_dimmable':
                                if new_device:
                                    success, reason = verify_type_and_range(
                                        value=value,
                                        name="'is_dimmable'",
                                        cls=bool,
                                    )
                                    if not success:
                                        errors.append(reason)
                                else:
                                    error = f"Cannot update read-only parameter '{key}'."
                                    logger.error(error)
                                    errors.append(error)
                            elif key == 'dynamic_color':
                                if new_device:
                                    success, reason = verify_type_and_range(
                                        value=value,
                                        name="'dynamic_color'",
                                        cls=bool,
                                    )
                                    if not success:
                                        errors.append(reason)
                                else:
                                    error = f"Cannot update read-only parameter '{key}'."
                                    logger.error(error)
                                    errors.append(error)
    if errors:
        return False, errors
    return True, []
