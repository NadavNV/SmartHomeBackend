from config import env  # noqa: F401  # load_dotenv side effect
import os
import logging.handlers
import json
import requests
from datetime import datetime, UTC, timedelta
from flask import jsonify, request, Response
from typing import Any, Mapping
from prometheus_client import Gauge, Counter, Histogram
from services.db import get_redis

from validation.validators import (
    AC_MODES, AC_FAN_SETTINGS, AC_SWING_MODES
)

logger = logging.getLogger("smart_home.monitoring.metrics")

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus-svc.smart-home.svc.cluster.local:9090")
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
light_is_dimmable = Gauge("light_is_dimmable", "Is this light dimmable", ["device_id", "state"])
light_dynamic_color = Gauge("light_dynamic_color", "Does this light have dynamic color", ["device_id", "state"])
# Door lock
lock_status = Gauge("lock_status", "Locked/unlocked status", ["device_id", "state"])
auto_lock_enabled = Gauge("auto_lock_enabled", "Auto-lock enabled",
                          ["device_id", "state"])
lock_battery_level = Gauge("lock_battery_level", "Battery level", ["device_id"])
# Curtain
curtain_status = Gauge("curtain_status", "Open/closed status", ["device_id", "state"])
curtain_position = Gauge("curtain_position", "Current position (%)", ["device_id"])


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
    get_redis().rpush(f"device_on_intervals:{device_id}", json.dumps([datetime.now(UTC).isoformat(), None]))


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
    intervals = get_redis().lrange(key, 0, -1)
    if not intervals:
        return None
    # Update the last interval
    last_interval = json.loads(intervals[-1])
    last_interval[1] = datetime.now(UTC).isoformat()
    # Replace last item
    get_redis().lset(key, len(intervals) - 1, json.dumps(last_interval))
    start_time = datetime.fromisoformat(last_interval[0])
    end_time = datetime.fromisoformat(last_interval[1])
    return (end_time - start_time).total_seconds()


def update_device_status(device: Mapping[str, Any], new_status: str) -> None:
    """
    Records an update of a device's status with a new status.

    If the new status is the same as the current status, effectively nothing happens.

    :param Mapping[str, Any] device: The device whose status is being updated.
    :param str new_status: The new status of the device.
    :return: None
    :rtype: None
    """
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

    if new_status == "on" and (not get_redis().sismember("seen_devices", device["id"]) or device["status"] == "off"):
        # Starting new interval
        record_on_interval_start(device["id"])
        if get_redis().sismember("seen_devices", device["id"]):
            device_on_events.labels(device_id=device["id"], device_type=device["type"]).inc()

    # Ending latest interval
    if new_status == "off" and device["status"] == "on":
        duration = record_on_interval_end(device["id"])
        if duration:
            device_usage_seconds.labels(device_id=device["id"], device_type=device["type"]).inc(duration)

    device_status.labels(device_id=device["id"], device_type=device["type"]).set(
        1 if new_status in {"on", "locked", "closed"} else 0)


def flip_device_boolean_flag(metric: Gauge, device_id: str, new_value: bool) -> None:
    """
    Records flipping a boolean flag on the device.

    :param Gauge metric: The metric recording the boolean flag.
    :param str device_id: ID of the device.
    :param bool new_value: The new value of the boolean flag.
    :return: None
    :rtype: None
    """
    metric.labels(device_id=device_id, state=str(new_value)).set(1)
    metric.labels(device_id=device_id, state=str(not new_value)).set(0)


def get_device_on_interval_at_time(device_id: str, check_time: datetime) -> tuple[datetime, datetime | None] | None:
    """
    Returns the interval of time during which the device was on that includes the
    given time, if exists.

    :param str device_id: ID of the device.
    :param datetime check_time: The time to check against.
    :return: Either a tuple of datetimes representing the interval, or None
        if no interval was found.
    :rtype: tuple[datetime, datetime | None] | None
    """
    # The key of the device in the Redis data structure
    key = f"device_on_intervals:{device_id}"
    intervals = get_redis().lrange(key, 0, -1)  # Get all intervals for the device
    for interval_json in intervals:
        try:
            on_str, off_str = json.loads(interval_json)
            on_time = datetime.fromisoformat(on_str)
            off_time = datetime.fromisoformat(off_str) if off_str is not None else None
            if off_time is None:
                return on_time, None
            elif on_time <= check_time <= off_time:
                return on_time, off_time
        except (ValueError, TypeError):
            continue  # Skip malformed intervals
    return None


def mark_device_read(device: Mapping[str, Any]) -> tuple[bool, str | None]:
    """
    Marks a device as seen when read for the first time from the Mongo database.

    If the device is indeed new, create new 0 value metrics for its total on time and total on events.
    This function assumes that the device's data has already been validated.

    :param Mapping[str, Any] device: The device to mark as seen.
    :return: A tuple of a boolean value indicating success and an optional reason for failure,
        or None on success. Returns True if the device was marked as seen, False if already marked
        as seen or if it didn't pass data validation.
    :rtype: tuple[bool, str | None]
    """
    device_id = device.get("id")
    if device_id is not None and not get_redis().sismember("seen_devices", device_id):
        logger.info(f"Device {device_id} read from DB for the first time. Validating device {device}:")
        logger.info(f"Success. Adding metrics for device {device_id}")
        device_on_events.labels(device_id=device_id, device_type=device["type"]).inc(0)
        device_usage_seconds.labels(device_id=device_id, device_type=device["type"]).inc(0)
        update_device_metrics(device, device)
        for key, value in device["parameters"].items():
            update_device_parameter(device, key, value)
        get_redis().sadd("seen_devices", device_id)
        return True, None
    else:
        return False, f"Device {device_id} already read."


def update_device_metrics(old_device: Mapping[str, Any], updated_device: Mapping[str, Any]) -> None:
    """
    Updates the device's metrics based on the received updated configuration.

    This function assumes that the new configuration has already been validated.

    :param Mapping[str, Any] old_device: Current device configuration.
    :param Mapping[str, Any] updated_device: Updated device configuration.
    :return: None
    :rtype: None
    """
    for key, value in updated_device.items():
        match key:
            case "name" | "room":
                logger.info(f"Setting parameter '{key}' to value '{value}'")
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
                logger.info(f"Setting parameter '{key}' to value '{value}'")
                update_device_status(old_device, value)
            case "parameters":
                for param, param_value in value.items():
                    update_device_parameter(old_device, param, param_value)


def update_device_parameter(device: Mapping[str, Any], param: str, param_value: Any) -> None:
    """
    Updates the metrics of a specific parameter based on the given new value.

    This function assumes that the new configuration has already been validated.

    :param Mapping[str, Any] device: Current device configuration
    :param str param: The parameter being updated
    :param str param_value: The updated value
    :return: None
    :rtype: None
    """
    logger.info(f"Setting parameter '{param}' to value '{param_value}'")
    match device["type"]:
        case "water_heater":
            match param:
                case "temperature":
                    water_heater_temperature.labels(
                        device_id=device["id"],
                    ).set(param_value)
                case "target_temperature":
                    water_heater_target_temperature.labels(
                        device_id=device["id"],
                    ).set(param_value)
                case "is_heating":
                    flip_device_boolean_flag(
                        metric=water_heater_is_heating_status,
                        new_value=param_value,
                        device_id=device["id"],
                    )
                case "timer_enabled":
                    flip_device_boolean_flag(
                        metric=water_heater_timer_enabled_status,
                        new_value=param_value,
                        device_id=device["id"],
                    )
                case "scheduled_on":
                    water_heater_schedule_info.labels(
                        device_id=device["id"],
                        scheduled_on=param_value,
                        scheduled_off=device["parameters"]["scheduled_off"],
                    ).set(1)
                case "scheduled_off":
                    water_heater_schedule_info.labels(
                        device_id=device["id"],
                        scheduled_on=device["parameters"]["scheduled_on"],
                        scheduled_off=param_value,
                    )
        case "light":
            match param:
                case "brightness":
                    light_brightness.labels(
                        device_id=device["id"],
                        is_dimmable=str(device["parameters"]["is_dimmable"]),
                    ).set(param_value)
                case "color":
                    light_color.labels(
                        device_id=device["id"],
                        dynamic_color=str(device["parameters"]["dynamic_color"]),
                    ).set(int("0x" + param_value[1:], 16))
                case "is_dimmable":
                    flip_device_boolean_flag(
                        metric=light_is_dimmable,
                        new_value=param_value,
                        device_id=device["id"],
                    )
                case "dynamic_color":
                    flip_device_boolean_flag(
                        metric=light_dynamic_color,
                        new_value=param_value,
                        device_id=device["id"],
                    )
        case "air_conditioner":
            match param:
                case "temperature":
                    ac_temperature.labels(
                        device_id=device["id"],
                    ).set(param_value)
                case "mode":
                    modes = AC_MODES
                    for mode in modes:
                        ac_mode_status.labels(
                            device_id=device["id"],
                            mode=mode,
                        ).set(1 if mode == param_value else 0)
                case "fan_speed":
                    modes = AC_FAN_SETTINGS
                    for mode in modes:
                        ac_fan_status.labels(
                            device_id=device["id"],
                            mode=param_value,
                        ).set(1 if mode == param_value else 0)
                case "swing":
                    modes = AC_SWING_MODES
                    for mode in modes:
                        ac_swing_status.labels(
                            device_id=device["id"],
                            mode=param_value,
                        ).set(1 if mode == param_value else 0)
        case "door_lock":
            match param:
                case "auto_lock_enabled":
                    flip_device_boolean_flag(
                        metric=auto_lock_enabled,
                        new_value=param_value,
                        device_id=device["id"],
                    )
                case "battery_level":
                    lock_battery_level.labels(
                        device_id=device["id"],
                    ).set(param_value)
        case "curtain":
            match param:
                case "position":
                    curtain_position.labels(
                        device_id=device["id"]
                    ).set(param_value)


def query_prometheus(query: str) -> list[dict[str, Any]] | dict[str, str]:
    """
    Queries the Prometheus database.

    Returns a list of matching metric results, or if an error occurred,
    returns a dictionary with the one key "error" whose value is a string
    detailing the error.

    :param str query: The query string.
    :return: A list of matching results, or a dictionary explaining the error if one occurs.
    :rtype: list[dict[str, Any]] | dict[str, str]
    """
    try:
        logger.info(f"Querying Prometheus: {query}")
        resp = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": query}, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        logger.info(f"Prometheus response for query '{query}': {data}")
        return data.get("data", {}).get("result", [])
    except requests.RequestException as e:
        logger.exception(f"Error querying Prometheus for query '{query}'")
        return {"error": str(e)}


def query_prometheus_range(metric: str, start: datetime, end: datetime, step: str = "60s") -> list[dict[str, Any]] | \
                                                                                              dict[str, str]:
    """
    Queries the Prometheus database during a given time window.

    :param str metric: The metric to query.
    :param datetime start: When to start the query.
    :param datetime end: When to stop the query.
    :param str step: The time step to use when querying.
    :return: A list of matching results, or a dictionary explaining the error if one occurs.
    :rtype: list[dict[str, Any]] | dict[str, str]
    """
    query = metric
    try:
        params = {
            "query": query,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "step": step
        }
        logger.info(f"Querying Prometheus range: {params}")
        resp = requests.get(f"{PROMETHEUS_URL}/api/v1/query_range", params=params, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", {}).get("result", [])
    except requests.RequestException as e:
        logger.exception(f"Error querying Prometheus for metric '{metric}' in range")
        return {"error": str(e)}


def query_prometheus_point_increase(metric: str, start: datetime, end: datetime) -> list[dict[str, Any]] | \
                                                                                    dict[str, str]:
    """
    Queries the Prometheus database for the increase of a given metric during a given time window.

    :param str metric: The metric to query.
    :param datetime start: When to start the query.
    :param datetime end: When to stop the query.
    :return: A list of matching results, or a dictionary explaining the error if one occurs.
    :rtype: list[dict[str, Any]] | dict[str, str]
    """
    window_seconds = int((end - start).total_seconds())
    range_expr = f"{window_seconds}s"
    query = f"increase({metric}[{range_expr}])"
    try:
        params = {
            "query": query,
            "time": end.isoformat()  # run instant query at the end of window
        }
        logger.info(f"Querying Prometheus point increase: {params}")
        resp = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params=params, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", {}).get("result", [])
    except requests.RequestException as e:
        logger.exception(f"Error querying Prometheus point increase for metric '{metric}'")
        return {"error": str(e)}


def generate_analytics() -> tuple[Response, int]:
    """
    Generates a json object of aggregate and individual device metrics.

    :return: A flask Response object and an HTTP status code.
    :rtype: tuple[Response, int]
    """
    try:
        body = request.get_json(silent=True) or {}
        logger.info(f"Received analytics request body: {body}")

        to_ts = datetime.fromisoformat(body.get("to")) if "to" in body else datetime.now(UTC)
        from_ts = datetime.fromisoformat(body.get("from")) if "from" in body else to_ts - timedelta(days=7)

        # Safety check
        if from_ts >= to_ts:
            return jsonify({"error": "'from' must be before 'to'"}), 400

        usage_results = query_prometheus_point_increase("device_usage_seconds_total", from_ts, to_ts)
        event_results = query_prometheus_point_increase("device_on_events_total", from_ts, to_ts)

        if isinstance(usage_results, dict) and "error" in usage_results:
            logger.error(f"Prometheus usage query failed: {usage_results['error']}")
            return jsonify({"error": f"Failed to query Prometheus, details: {usage_results["error"]}"}), 500
        if isinstance(event_results, dict) and "error" in usage_results:
            logger.error(f"Prometheus usage query failed: {event_results['error']}")
            return jsonify({"error": f"Failed to query Prometheus, details: {event_results["error"]}"}), 500

        device_analytics_json = {}

        for item in usage_results:
            if "value" not in item:
                logger.warning(f"Missing 'value' in usage result: {item}")
                continue
            device_id = item["metric"].get("device_id", "unknown")
            usage_seconds = float(item["value"][1])
            logger.info(f"Device {device_id} usage seconds: {usage_seconds}")
            device_analytics_json.setdefault(device_id, {})["total_usage_minutes"] = usage_seconds / 60
            # Include currently on devices that haven't been added to the metric yet
            interval = get_device_on_interval_at_time(device_id, to_ts)
            if interval:
                on_time, off_time = interval
                effective_start = max(on_time, from_ts)
                effective_end = min(off_time or to_ts, to_ts)
                extra_seconds = (effective_end - effective_start).total_seconds()
                if device_id in device_analytics_json:
                    device_analytics_json[device_id]["total_usage_minutes"] += extra_seconds / 60
                else:
                    device_analytics_json[device_id] = {"total_usage_minutes": extra_seconds / 60}
        for item in event_results:
            if "value" not in item:
                logger.warning(f"Missing 'value' in event result: {item}")
                continue
            device_id = item["metric"].get("device_id", "unknown")
            on_count = int(float(item["value"][1]))
            logger.info(f"Device {device_id} on count: {on_count}")
            device_analytics_json.setdefault(device_id, {})["on_events"] = on_count

        total_usage = sum(d.get("total_usage_minutes", 0) for d in device_analytics_json.values())
        total_on_events = sum(d.get("on_events", 0) for d in device_analytics_json.values())

        response = {
            "analytics_window": {
                "from": from_ts.isoformat(),
                "to": to_ts.isoformat()
            },
            "aggregate": {
                "total_devices": get_redis().scard("seen_devices"),
                "total_on_events": total_on_events,
                "total_usage_minutes": total_usage
            },
            "on_devices": device_analytics_json,
            "message": "For full analytics, charts, and trends, visit the Grafana dashboard."
        }

        logger.info(f"Returning analytics response: {response}")
        return jsonify(response), 200
    except Exception as e:
        logger.exception("Unexpected error in /api/devices/analytics")
        return jsonify({"error": str(e)}), 500
