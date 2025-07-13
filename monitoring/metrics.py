import logging
import json
import requests
from datetime import datetime
from typing import Any, Mapping, Union
from prometheus_client import Gauge, Counter, Histogram
from services.redis_client import r

logger = logging.getLogger(__name__)

PROMETHEUS_URL = "http://prometheus-svc.smart-home.svc.cluster.local:9090"
# Prometheus metrics
# HTTP request metrics
request_count = Counter('request_count', 'Total Request Count', ['method', 'endpoint'])
request_latency = Histogram('request_latency_seconds', 'Request latency', ['endpoint'])

# Device metrics
device_metadata = Gauge("device_metadata", "Key/Value device Metadata", ["device_id", "key", "value"])
device_status = Gauge("device_status", "Device on/off state", ["device_id", "device_type"])
device_on_events = Counter("device_on_events_total", "Number of times device turned on",
                           ["device_id", "device_type", ])
device_usage_seconds = Counter("device_usage_seconds_total", "Total on-time in seconds",
                               ["device_id", "device_type"])
# Air conditioner
ac_temperature = Gauge("ac_temperature", "Current temperature (AC)", ["device_id"])
ac_mode_status = Gauge("ac_mode_status", "Current active mode of air conditioners",
                       ["device_id", "mode"])
ac_swing_status = Gauge("ac_swing_status", "Current swing mode of air conditioners",
                        ["device_id", "mode"])
ac_fan_status = Gauge("ac_fan_status", "Current fan mode of air conditioners",
                      ["device_id", "mode"])
# Water heater
water_heater_temperature = Gauge("water_heater_temperature", "Current temperature (water heater)",
                                 ["device_id"])
water_heater_target_temperature = Gauge("water_heater_target_temperature", "Target temperature",
                                        ["device_id"])
water_heater_is_heating_status = Gauge("water_heater_is_heating_status", "Water heater is heating",
                                       ["device_id", "state"])
water_heater_timer_enabled_status = Gauge("water_heater_timer_enabled_status", "Water heater timer enabled",
                                          ["device_id", "state"])
water_heater_schedule_info = Gauge("water_heater_schedule_info", "Water heater schedule info",
                                   ["device_id", "scheduled_on", "scheduled_off"])
# Light
light_brightness = Gauge("light_brightness", "Current light brightness",
                         ["device_id", "is_dimmable"])
light_color = Gauge("light_color", "Current light color as decimal RGB",
                    ["device_id", "dynamic_color"])
light_color_info = Gauge("light_color_info", "Current light color as label",
                         ["device_id", "dynamic_color", "color"])
# Door lock
lock_status = Gauge("lock_status", "Locked/unlocked status", ["device_id", "state"])
auto_lock_enabled = Gauge("auto_lock_enabled", "Auto-lock enabled",
                          ["device_id", "state"])
lock_battery_level = Gauge("lock_battery_level", "Battery level", ["device_id"])
# Curtain
curtain_status = Gauge("curtain_status", "Open/closed status", ["device_id", "state"])


# Redis-backed interval tracking
def record_on_interval_start(device_id: str) -> None:
    """
    Records a new interval during which the device was on.

    This function is called when a device is turned on from being off,
    or when a device is first seen while being on. It creates a new interval
    comprised of a length 2 list, its first member being the current UTC time
    in ISO format, and its second member being None, to be filled later when
    the device is turned off. It then adds that interval to a Redis data
    structure.

    :param str device_id: The ID of the device.
    :return: None
    :rtype: None
    """
    r.rpush(f"device_on_intervals:{device_id}", json.dumps([datetime.now().isoformat(), None]))


def record_on_interval_end(device_id: str) -> float | None:
    """
    Closes an interval during which the device was on.

    This function is called when a device is turned off from being on. It reads
    the last on-interval for the given device and replaces the second member,
    which is assumed to be None, with the current UTC time. It then writes
    the new interval to the Redis data structure and returns the interval
    duration in seconds, of an interval was found.

    :param str device_id: The ID of the device.
    :return: The interval duration in seconds, or None if no interval was found
    :rtype: float | None
    """
    key = f"device_on_intervals:{device_id}"
    intervals = r.lrange(key, 0, -1)
    if not intervals:
        return None
    # Update the last interval
    last_interval = json.loads(intervals[-1])
    last_interval[1] = datetime.now().isoformat()
    # Replace last item
    r.lset(key, len(intervals) - 1, json.dumps(last_interval))
    start_time = datetime.fromisoformat(last_interval[0])
    end_time = datetime.fromisoformat(last_interval[1])
    return (end_time - start_time).total_seconds()


def update_binary_device_status(device: Mapping[str, Any], new_status: str) -> None:
    # For binary states, determine the two options
    known_states = {
        "on": "off",
        "off": "on",
        "locked": "unlocked",
        "unlocked": "locked",
        "open": "closed",
        "closed": "open"
    }
    other_state = known_states.get(new_status)
    if not other_state:
        logger.warning(f"Unknown binary state: {new_status}")
        return

    if new_status == "on" and (not r.sismember("seen_devices", device["id"]) or device["status"] == "off"):
        # Starting interval
        record_on_interval_start(device["id"])
        if r.sismember("seen_devices", device["id"]):
            device_on_events.labels(device_id=device["id"], device_type=device["type"]).inc()

    # Ending interval
    if new_status == "off" and device["status"] == "on":
        duration = record_on_interval_end(device["id"])
        if duration:
            device_usage_seconds.labels(device_id=device["id"], device_type=device["type"]).inc(duration)

    device_status.labels(device_id=device["id"], device_type=device["type"]).set(
        1 if new_status in {"on", "locked", "closed"} else 0)


def flip_device_boolean_flag(metric: Gauge, device_id: str, flag: str, new_value: bool) -> bool:
    if isinstance(new_value, bool):
        metric.labels(device_id=device_id, state=str(new_value)).set(1)
        metric.labels(device_id=device_id, state=str(not new_value)).set(0)
        return True
    logger.error(f"Unsupported value '{new_value}' for parameter '{flag}'")
    raise ValueError(f"Unsupported value '{new_value}' for parameter '{flag}'")


def get_device_on_interval_at_time(device_id: str, check_time: datetime) -> (
        Union[tuple[datetime, datetime | None], None]
):
    key = f"device_on_intervals:{device_id}"
    intervals = r.lrange(key, 0, -1)  # Get all intervals for the device
    for interval_json in intervals:
        try:
            on_str, off_str = json.loads(interval_json)
            on_time = datetime.fromisoformat(on_str)
            off_time = datetime.fromisoformat(off_str) if off_str else None
            if off_time is None:
                return on_time, None
            elif on_time <= check_time <= off_time:
                return on_time, off_time
        except (ValueError, TypeError):
            continue
    return None


def mark_device_read(device: Mapping[str, Any]):
    device_id = device.get("id")
    if device_id and not r.sismember("seen_devices", device["id"]):
        logger.info(f"Device {device_id} read from DB for the first time")
        logger.info(f"Adding metrics for device {device_id}")
        device_on_events.labels(device_id=device_id, device_type=device["type"]).inc(0)
        device_usage_seconds.labels(device_id=device_id, device_type=device["type"]).inc(0)
        update_device_metrics(device, device)
        for key, value in device["parameters"].items():
            device_metrics_action(device, key, value)
        r.sadd("seen_devices", device_id)


def update_device_metrics(old_device: Mapping[str, Any], updated_device: Mapping[str, Any]) -> None:
    for key, value in updated_device.items():
        logger.info(f"Setting parameter '{key}' to value '{value}'")
        match key:
            case "name" | "room":
                # Mark old metadata stale, set new data valid
                # If it's the same value, it's set to 0 then
                # back to 1 immediately
                device_metadata.labels(
                    device_id=old_device["id"],
                    key=key,
                    value=old_device[key],
                ).set(0)
                device_metadata.labels(
                    device_id=old_device["id"],
                    key=key,
                    value=value,
                ).set(1)
            case "status":
                update_binary_device_status(old_device, value)


def device_metrics_action(device: Mapping[str, Any], key: str, value: Any) -> tuple[bool, str | None]:
    # Update metrics
    match device["type"]:
        case "water_heater":
            match key:
                case "temperature":
                    water_heater_temperature.labels(
                        device_id=device["id"],
                    ).set(value)
                case "target_temperature":
                    water_heater_target_temperature.labels(
                        device_id=device["id"],
                    ).set(value)
                case "is_heating":
                    if not flip_device_boolean_flag(
                            metric=water_heater_is_heating_status,
                            new_value=value,
                            device_id=device["id"],
                            flag=key,
                    ):
                        return False, f"Unsupported value '{value}' for parameter '{key}'"
                case "timer_enabled":
                    if not flip_device_boolean_flag(
                            metric=water_heater_timer_enabled_status,
                            new_value=value,
                            device_id=device["id"],
                            flag=key,
                    ):
                        return False, f"Unsupported value '{value}' for parameter '{key}'"
                case "scheduled_on":
                    water_heater_schedule_info.labels(
                        device_id=device["id"],
                        scheduled_on=value,
                        scheduled_off=device["parameters"]["scheduled_off"],
                    ).set(1)
                case "scheduled_off":
                    water_heater_schedule_info.labels(
                        device_id=device["id"],
                        scheduled_on=device["parameters"]["scheduled_on"],
                        scheduled_off=value,
                    )
                case _:
                    logger.error(f"Unknown parameter '{key}'")
                    return False, f"Unknown parameter '{key}'"
        case "light":
            match key:
                case "brightness":
                    light_brightness.labels(
                        device_id=device["id"],
                        is_dimmable=str(device["parameters"]["is_dimmable"]),
                    ).set(value)
                case "color":
                    try:
                        light_color.labels(
                            device_id=device["id"],
                            dynamic_color=str(device["parameters"]["dynamic_color"]),
                        ).set(int("0x" + value[1:], 16))
                    except (KeyError, ValueError):
                        logger.exception(f"Incorrect color string '{value}'")
                case "is_dimmable" | "dynamic_color":
                    # Read-only parameter, not tracking in metrics
                    pass
                case _:
                    logger.error(f"Unknown parameter '{key}'")
                    return False, f"Unknown parameter '{key}'"
        case "air_conditioner":
            match key:
                case "temperature":
                    ac_temperature.labels(
                        device_id=device["id"],
                    ).set(value)
                case "mode":
                    modes = ["cool", "heat", "fan"]
                    for mode in modes:
                        ac_mode_status.labels(
                            device_id=device["id"],
                            mode=mode,
                        ).set(1 if mode == value else 0)
                case "fan_speed":
                    modes = ["off", "low", "medium", "high"]
                    for mode in modes:
                        ac_fan_status.labels(
                            device_id=device["id"],
                            mode=value,
                        ).set(1 if mode == value else 0)
                case "swing":
                    modes = ["off", "on", "auto"]
                    for mode in modes:
                        ac_swing_status.labels(
                            device_id=device["id"],
                            mode=value,
                        ).set(1 if mode == value else 0)
                case _:
                    logger.error(f"Unknown parameter '{key}'")
                    return False, f"Unknown parameter '{key}'"
        case "door_lock":
            match key:
                case "auto_lock_enabled":
                    # Read-only parameter, not tracked in metrics
                    pass
                case "battery_level":
                    lock_battery_level.labels(
                        device_id=device["id"],
                    ).set(value)
                case _:
                    logger.error(f"Unknown parameter '{key}'")
                    return False, f"Unknown parameter '{key}'"
        case "curtain":
            match key:
                case "position":
                    # Read-only parameter, not tracked in metrics
                    pass
                case _:
                    logger.error(f"Unknown parameter '{key}'")
                    return False, f"Unknown parameter '{key}'"
        case _:
            logger.error(f"Unknown device type '{device['type']}'")
            return False, f"Unknown device type '{device['type']}'"
    return True, None


def query_prometheus(query) -> Union[list[dict[str, Any]], dict[str, str]]:
    try:
        logger.debug(f"Querying Prometheus: {query}")
        resp = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": query}, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        logger.debug(f"Prometheus response for query '{query}': {data}")
        return data.get("data", {}).get("result", [])
    except requests.RequestException as e:
        logger.exception(f"Error querying Prometheus for query '{query}'")
        return {"error": str(e)}


def query_prometheus_range(metric: str, start: datetime, end: datetime, step: str = "60s") -> (
        Union[list[dict[str, Any]], dict[str, str]]):
    query = metric
    try:
        params = {
            "query": query,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "step": step
        }
        logger.debug(f"Querying Prometheus range: {params}")
        resp = requests.get(f"{PROMETHEUS_URL}/api/v1/query_range", params=params, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", {}).get("result", [])
    except requests.RequestException as e:
        logger.exception(f"Error querying Prometheus for metric '{metric}' in range")
        return {"error": str(e)}


def query_prometheus_point_increase(metric: str, start: datetime, end: datetime) -> (
        Union[list[dict[str, Any]], dict[str, str]]):
    window_seconds = int((end - start).total_seconds())
    range_expr = f"{window_seconds}s"
    query = f"increase({metric}[{range_expr}])"
    try:
        params = {
            "query": query,
            "time": end.isoformat()  # run instant query at the end of window
        }
        logger.debug(f"Querying Prometheus point increase: {params}")
        resp = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params=params, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", {}).get("result", [])
    except requests.RequestException as e:
        logger.exception(f"Error querying Prometheus point increase for metric '{metric}'")
        return {"error": str(e)}
