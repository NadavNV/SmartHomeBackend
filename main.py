from flask import Flask, jsonify, request, Response
from werkzeug.middleware.proxy_fix import ProxyFix
import paho.mqtt.client as paho
import json
import atexit
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from pymongo.errors import ConnectionFailure, ConfigurationError, OperationFailure
from dotenv import load_dotenv
from typing import Any
from threading import Event
import re
import os
import time
import random
import sys
import logging.handlers
from prometheus_client import Counter, Histogram, generate_latest

logging.basicConfig(
    format="[%(asctime)s] %(levelname)s in %(module)s: %(message)s",
    handlers=[
        # Prints to sys.stderr
        logging.StreamHandler(),
        # Writes to a log file which rotates every 1mb, or gets overwritten when the app is restarted
        logging.handlers.RotatingFileHandler(
            filename="backend.log",
            mode='w',
            maxBytes=1024 * 1024,
            backupCount=3
        )
    ],
    level=logging.INFO,
)

# Env variables
load_dotenv()

username = os.getenv("MONGO_USER")
password = os.getenv("MONGO_PASS")

# How many times to attempt a connection request
RETRIES = 5
RETRY_TIMEOUT = 10

# Setting up the MQTT client
BROKER_URL = os.getenv("BROKER_URL", "test.mosquitto.org")
BROKER_PORT = int(os.getenv("BROKER_PORT", 1883))

DB_CONNECTION_STRING = os.getenv("DB_CONNECTION_STRING")

REQUEST_COUNT = Counter('request_count', 'Total Request Count', ['method', 'endpoint'])
REQUEST_LATENCY = Histogram('request_latency_seconds', 'Request latency', ['endpoint'])

# Minimum temperature (Celsius) for water heater
MIN_WATER_TEMP = 49
# Maximum temperature (Celsius) for water heater
MAX_WATER_TEMP = 60
# Minimum temperature (Celsius) for air conditioner
MIN_AC_TEMP = 16
# Maximum temperature (Celsius) for air conditioner
MAX_AC_TEMP = 30
# Minimum brightness for dimmable light
MIN_BRIGHTNESS = 0
# Maximum brightness for dimmable light
MAX_BRIGHTNESS = 100
# Minimum position for curtain
MIN_POSITION = 0
# Maximum position for curtain
MAX_POSITION = 100
# Minimum value for battery level
MIN_BATTERY = 0
# Maximum value for battery level
MAX_BATTERY = 100

DEVICE_TYPES = {"light", "water_heater", "air_conditioner", "door_lock", "curtain"}
WATER_HEATER_PARAMETERS = {
    "temperature",
    "target_temperature",
    "is_heating",
    "timer_enabled",
    "scheduled_on",
    "scheduled_off",
}
LIGHT_PARAMETERS = {
    "brightness",
    "color",
    "is_dimmable",
    "dynamic_color",
}
AC_PARAMETERS = {
    "temperature",
    "mode",
    "fan_speed",
    "swing",
}
AC_MODES = {'cool', 'heat', 'fan'}
AC_FAN_SETTINGS = {'off', 'low', 'medium', 'high'}
AC_SWING_MODES = {'off', 'on', 'auto'}
LOCK_PARAMETERS = {
    "auto_lock_enabled",
    "battery_level",
}
CURTAIN_PARAMETERS = {
    "position",
}

# Regex explanation:
#
# ([01]?[0-9]|2[0-3]) - Hours. Either a 2 followed by 0-3 or an optional
#                       initial digit of 0 or 1 followed by any digit.
# : - Colon.
# ([0-5]\d) - Minutes, 0-5 followed by any digit.
TIME_REGEX = '^([01][0-9]|2[0-3]):([0-5][0-9])$'

COLOR_REGEX = '^#([0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$'


def verify_type_and_range(value: Any, name: str, cls: type,
                          value_range: tuple[int, int] | set[str] | str | None = None) -> bool:
    """
    This function verifies that 'value' is of type 'cls', and when relevant that it is
    within an allowed range of values.
    If 'cls' is int, then value_range maybe a tuple of (min_value, max_value). If 'cls' is
    str, then 'value_range' may be a set of allowed values. If 'cls' is str and 'value_range'
    is the string 'time' then 'value' must be a valid ISO format time string without seconds.
    if 'cls' is str and 'value_range' is the string 'color' then 'value' must be a valid
    HTML RGB string.
    :param value: The value to be checked.
    :param name: The name associated with the value, for error messages.
    :param cls: The type to check against.
    :param value_range: The value of 'value' is expected to fall within this range, if given.
    :return: True if 'value' is of type 'cls' and matches the given 'value_range', False otherwise.
    """
    if cls == int:
        try:
            value = int(value)
            if value_range is not None:
                minimum, maximum = value_range
                if value > maximum or value < minimum:
                    app.logger.error(f"{name} must be between {minimum} and {maximum}, got {value} instead.")
                    return False
            return True
        except ValueError:
            app.logger.error(f"{name} must be a numeric string, got {value} instead.")
            return False
    if type(value) is not cls:
        app.logger.error(f"{name} must be a {cls}, got {type(value)} instead.")
        return False
    if cls == str:
        if type(value_range) is set:
            if value not in value_range:
                app.logger.error(f"{name} must be one of {value_range}, "
                                 f"got {value} instead.")
                return False
        elif value_range == 'time':
            return bool(re.match(TIME_REGEX, value))
        elif value_range == 'color':
            return bool(re.match(COLOR_REGEX, value))
    return True


# Verify that the given string is a correct ISO format time string (without seconds)
def verify_time_string(string: str) -> bool:
    return bool(re.match(TIME_REGEX, string))


# Validates that the request to add a new device contains only valid information
def validate_device_data(new_device):
    required_fields = {'id', 'type', 'room', 'name', 'status', 'parameters'}
    if set(new_device.keys()) != required_fields:
        app.logger.error(f"Incorrect field(s) in new device {set(new_device.keys()) - required_fields}, "
                         f"must be exactly these fields: {required_fields}")
        return False
    for field in list(new_device.keys()):
        if field == 'type' and new_device['type'] not in DEVICE_TYPES:
            app.logger.error(f"Incorrect device type {new_device['type']}, must be on of {DEVICE_TYPES}.")
            return False
        if field == 'status':
            if 'type' in new_device and new_device['type'] in DEVICE_TYPES:
                match new_device['type']:
                    case "door_lock":
                        if not verify_type_and_range(
                            value=new_device['status'],
                            name="'status'",
                            cls=str,
                            value_range={'open', 'locked'},
                        ):
                            return False
                    case "curtain":
                        if not verify_type_and_range(
                                value=new_device['status'],
                                name="'status'",
                                cls=str,
                                value_range={'open', 'closed'},
                        ):
                            return False
                    case _:
                        if not verify_type_and_range(
                                value=new_device['status'],
                                name="'status'",
                                cls=str,
                                value_range={'on', 'off'},
                        ):
                            return False
        if field == 'parameters':
            if 'type' in new_device and new_device['type'] in DEVICE_TYPES:
                if not verify_type_and_range(
                    value=new_device['parameters'],
                    name="'parameters'",
                    cls=dict,
                ):
                    return False
                left_over_parameters = set(new_device['parameters'].keys())
                match new_device['type']:
                    case "door_lock":
                        left_over_parameters -= LOCK_PARAMETERS
                        if left_over_parameters != set():
                            app.logger.error(f"Disallowed parameters for door lock {left_over_parameters}, "
                                             f"allowed parameters: {LOCK_PARAMETERS}")
                            return False
                        for key, value in new_device['parameters'].items():
                            if key == 'auto_lock_enabled':
                                if not verify_type_and_range(
                                    value=value,
                                    name="'auto_lock_enabled'",
                                    cls=bool,
                                ):
                                    return False
                            elif key == 'battery_level':
                                if not verify_type_and_range(
                                    value=value,
                                    name="'battery_level'",
                                    cls=int,
                                    value_range=(MIN_BATTERY, MAX_BATTERY),
                                ):
                                    return False
                    case "curtain":
                        left_over_parameters -= CURTAIN_PARAMETERS
                        if left_over_parameters != set():
                            app.logger.error(f"Disallowed parameters for curtain {left_over_parameters}, "
                                             f"allowed parameters: {CURTAIN_PARAMETERS}")
                            return False
                        for key, value in new_device['parameters'].items():
                            if key == 'position':
                                if not verify_type_and_range(
                                    value=value,
                                    name="'position'",
                                    cls=int,
                                    value_range=(MIN_POSITION, MAX_POSITION),
                                ):
                                    return False
                    case "air-conditioner":
                        left_over_parameters -= AC_PARAMETERS
                        if left_over_parameters != set():
                            app.logger.error(f"Disallowed parameters for air conditioner {left_over_parameters}, "
                                             f"allowed parameters: {AC_PARAMETERS}")
                            return False
                        for key, value in new_device['parameters'].items():
                            if key == 'temperature':
                                if not verify_type_and_range(
                                    value=value,
                                    name="'temperature'",
                                    cls=int,
                                    value_range=(MIN_AC_TEMP, MAX_AC_TEMP),
                                ):
                                    return False
                            elif key == 'mode':
                                if not verify_type_and_range(
                                    value=value,
                                    name="'mode'",
                                    cls=str,
                                    value_range=AC_MODES,
                                ):
                                    return False
                            elif key == 'fan':
                                if not verify_type_and_range(
                                    value=value,
                                    name="'fan'",
                                    cls=str,
                                    value_range=AC_FAN_SETTINGS,
                                ):
                                    return False
                            elif key == 'swing':
                                if not verify_type_and_range(
                                    value=value,
                                    name="'swing'",
                                    cls=str,
                                    value_range=AC_SWING_MODES,
                                ):
                                    return False
                    case "water-heater":
                        left_over_parameters -= WATER_HEATER_PARAMETERS
                        if left_over_parameters != set():
                            app.logger.error(f"Disallowed parameters for water heater {left_over_parameters}, "
                                             f"allowed parameters: {WATER_HEATER_PARAMETERS}")
                            return False
                        for key, value in new_device['parameters'].items():
                            if key == 'temperature':
                                if not verify_type_and_range(
                                    value=value,
                                    name="'temperature'",
                                    cls=int,
                                    value_range=(MIN_WATER_TEMP, MAX_WATER_TEMP),
                                ):
                                    return False
                            elif key == 'target_temperature':
                                if not verify_type_and_range(
                                        value=value,
                                        name="'target_temperature'",
                                        cls=int,
                                        value_range=(MIN_WATER_TEMP, MAX_WATER_TEMP),
                                ):
                                    return False
                            elif key == 'is_heating':
                                if not verify_type_and_range(
                                    value=value,
                                    name="'is_heating'",
                                    cls=bool,
                                ):
                                    return False
                            elif key == 'timer_enabled':
                                if not verify_type_and_range(
                                    value=value,
                                    name="'timer_enabled'",
                                    cls=bool,
                                ):
                                    return False
                            elif key == 'scheduled_on':
                                if not verify_type_and_range(
                                    value=value,
                                    name="'scheduled_on'",
                                    cls=str,
                                    value_range='time'
                                ):
                                    return False
                            elif key == 'scheduled_off':
                                if not verify_type_and_range(
                                    value=value,
                                    name="'scheduled_off'",
                                    cls=str,
                                    value_range='time'
                                ):
                                    return False
                    case "light":
                        left_over_parameters -= LIGHT_PARAMETERS
                        if left_over_parameters != set():
                            app.logger.error(f"Disallowed parameters for door lock {left_over_parameters},"
                                             f"allowed parameters: {LIGHT_PARAMETERS}")
                            return False
                        for key, value in new_device['parameters'].items():
                            if key == 'brightness':
                                if not verify_type_and_range(
                                    value=value,
                                    name="'brightness'",
                                    cls=int,
                                    value_range=(MIN_BRIGHTNESS, MAX_BRIGHTNESS),
                                ):
                                    return False
                            elif key == 'color':
                                if not verify_type_and_range(
                                    value=value,
                                    name="'color'",
                                    cls=str,
                                    value_range='color',
                                ):
                                    return False
                            elif key == 'brightness':
                                if not verify_type_and_range(
                                    value=value,
                                    name="'is_dimmable'",
                                    cls=bool,
                                ):
                                    return False
                            elif key == 'dynamic_color':
                                if not verify_type_and_range(
                                    value=value,
                                    name="'dynamic_color'",
                                    cls=bool,
                                ):
                                    return False

    return True


# Checks the validity of the device id
def id_exists(device_id):
    device = devices_collection.find_one({"id": device_id}, {'_id': 0})
    return device is not None


app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

# Database parameters
uri = DB_CONNECTION_STRING if DB_CONNECTION_STRING is not None else (
        f"mongodb+srv://{username}:{password}" +
        "@smart-home-db.w9dsqtr.mongodb.net/?retryWrites=true&w=majority&appName=smart-home-db"
)
try:
    mongo_client = MongoClient(uri, server_api=ServerApi('1'))
except ConfigurationError:
    app.logger.error("Failed to connect to database. Shutting down.")
    sys.exit(1)

for attempt in range(RETRIES):
    try:
        mongo_client.admin.command('ping')
    except (ConnectionFailure, OperationFailure):
        if attempt + 1 == RETRIES:
            app.logger.exception(f"Attempt {attempt + 1}/{RETRIES} failed. Shutting down.")
            sys.exit(1)
        delay = 2 ** attempt + random.random()
        app.logger.exception(f"Attempt {attempt + 1}/{RETRIES} failed. Retrying in {delay:.2f} seconds...")
        time.sleep(delay)

db = mongo_client["smart_home"]
devices_collection = db["devices"]

mqtt = paho.Client(paho.CallbackAPIVersion.VERSION2)

# To track if the MQTT connection was successful
connected_event = Event()


# Function to run after the MQTT client finishes connecting to the broker
def on_connect(client, userdata, connect_flags, reason_code, properties):
    app.logger.info(f'CONNACK received with code {reason_code}.')
    if reason_code == 0:
        app.logger.info("Connected successfully")
        client.subscribe("project/home/#")
        connected_event.set()
    else:
        app.logger.error(f"Connection failed with code {reason_code}")


# Verify that only parameters that are relevant to the device type are being
# modified. For example, a light shouldn't have a target temperature and a
# water heater shouldn't have a brightness.
def validate_action_parameters(device_type: str, updated_parameters: dict) -> bool:
    match device_type:
        case "water_heater":
            allowed_parameters = [
                "temperature",
                "target_temperature",
                "is_heating",
                "timer_enabled",
                "scheduled_on",
                "scheduled_off",
            ]
        case 'light':
            allowed_parameters = [
                "brightness",
                "color",
                "is_dimmable",
                "dynamic_color",
            ]
        case 'air_conditioner':
            allowed_parameters = [
                "temperature",
                "mode",
                "fan_speed",
                "swing",
            ]
        case 'door_lock':
            allowed_parameters = [
                "auto_lock_enabled",
                "battery_level",
            ]
        case 'curtain':
            allowed_parameters = ["position"]
        case _:
            app.logger.error(f"Unknown device type {device_type}")
            return False
    for field in updated_parameters:
        if field not in allowed_parameters:
            app.logger.error(f"Incorrect field in update endpoint: {field}")
            return False
    return True


# Receives the published mqtt payloads and updates the database accordingly
def on_message(mqtt_client, userdata, msg):
    app.logger.info(f"MQTT Message Received on {msg.topic}")
    try:
        payload = json.loads(msg.payload.decode())
        # Ignore self messages
        if "sender" in payload:
            if payload["sender"] == "backend":
                app.logger.info("Ignoring self message")
                return
            else:
                payload = payload["contents"]
        else:
            app.logger.error("Payload missing sender")
            return

        # Extract device_id from topic: expected format project/home/<device_id>/<method>
        topic_parts = msg.topic.split('/')
        if len(topic_parts) == 4:
            device_id = topic_parts[2]
            method = topic_parts[-1]
            device = devices_collection.find_one({"id": device_id}, {"_id": 0})
            if device is None:
                app.logger.error(f"Device ID {device_id} not found")
                return
            match method:
                case "action":
                    # Only update device parameters
                    if not validate_action_parameters(device['type'], payload):
                        return
                    update_fields = {}
                    for key, value in payload.items():
                        app.logger.info(f"Setting parameter '{key}' to value '{value}'")
                        field_name = f"parameters.{key}"
                        update_fields[field_name] = value
                    devices_collection.update_one(
                        {"id": device_id},
                        {"$set": update_fields}
                    )
                    return
                case "update":
                    # Only update device configuration (i.e. name, status, and room)
                    if "id" in payload and payload["id"] != device_id:
                        app.logger.error(f"ID mismatch: ID in URL: {device_id}, ID in payload: {payload['id']}")
                        return
                    # Make sure that this endpoint is only used to update specific fields
                    allowed_fields = ['room', 'name', 'status']
                    for field in payload:
                        if field not in allowed_fields:
                            app.logger.error(f"Incorrect field in update method: {field}")
                            return
                    for key, value in payload.items():
                        app.logger.info(f"Setting parameter '{key}' to value '{value}'")
                    # Find device by id and update the fields with 'set'
                    devices_collection.update_one(
                        {"id": device_id},
                        {"$set": payload}
                    )
                    return
                case "post":
                    # Add a new device to the database
                    if "id" in payload and payload["id"] != device_id:
                        app.logger.error(f"ID mismatch: ID in URL: {device_id}, ID in payload: {payload['id']}")
                        return
                    if validate_device_data(payload):
                        if id_exists(payload["id"]):
                            app.logger.error("ID already exists")
                            return
                        devices_collection.insert_one(payload)
                        app.logger.info("Device added successfully")
                        return
                    app.logger.error("Missing required field")
                    return
                case "delete":
                    # Remove a device from the database
                    if id_exists(device_id):
                        devices_collection.delete_one({"id": device_id})
                        app.logger.info("Device deleted successfully")
                        return
                    app.logger.error("ID not found")
                    return
                case _:
                    app.logger.error(f"Unknown method: {method}")
        else:
            app.logger.error(f"Incorrect topic {msg.topic}")

    except UnicodeDecodeError as e:
        app.logger.exception(f"Error decoding payload: {e.reason}")


mqtt.on_connect = on_connect
mqtt.on_message = on_message
mqtt.loop_start()

# for attempt in range(RETRIES):
#     try:
#         connected_event.clear()
#         mqtt.connect(BROKER_URL, BROKER_PORT)
#         if connected_event.wait(timeout=RETRY_TIMEOUT):
#             break  # Successfully connected
#         else:
#             raise TimeoutError("Connection timeout waiting for on_connect.")
#     except Exception:
#         if attempt + 1 == RETRIES:
#             app.logger.exception(f"Attempt {attempt + 1}/{RETRIES} failed. Shutting down.")
#             mongo_client.close()
#             mqtt.loop_stop()
#             sys.exit(1)
#         delay = 2 ** attempt + random.random()
#         app.logger.exception(f"Attempt {attempt + 1}/{RETRIES} failed. Retrying in {delay:.2f} seconds...")
#         time.sleep(delay)
# else:
#     app.logger.error("Failed to connect to MQTT server. Shutting down.")
#     mongo_client.close()
#     mqtt.loop_stop()
#     sys.exit(1)


# Formats and publishes the mqtt topic and payload -> the mqtt publisher
def publish_mqtt(contents: dict, device_id: str, method: str):
    topic = f"project/home/{device_id}/{method}"
    payload = json.dumps({
        "sender": "backend",
        "contents": contents,
    })
    mqtt.publish(topic, payload.encode(), qos=2)


@app.before_request
def before_request():
    request.start_time = time.time()


@app.route("/metrics")
def metrics():
    return Response(generate_latest(), mimetype="text/plain")


# Returns a list of device IDs
@app.get("/api/ids")
def get_device_ids():
    device_ids = list(devices_collection.find({}, {'id': 1, '_id': 0}))
    return [device_id['id'] for device_id in device_ids]


# Presents a list of all your devices and their configuration
@app.get("/api/devices")
def get_all_devices():
    devices = list(devices_collection.find({}, {'_id': 0}))
    return jsonify(devices)


# Get data on a specific device by its ID
@app.get("/api/devices/<device_id>")
def get_device(device_id):
    device = devices_collection.find_one({'id': device_id}, {'_id': 0})
    if device is not None:
        return jsonify(device)
    app.logger.error(f"ID {device_id} not found")
    return jsonify({'error': f"ID {device_id} not found"}), 400


# Adds a new device
@app.post("/api/devices")
def add_device():
    new_device = request.json
    if validate_device_data(new_device):
        if id_exists(new_device["id"]):
            return jsonify({'error': "ID already exists"}), 400
        devices_collection.insert_one(new_device)
        # Remove MongoDB unique id (_id) before publishing to mqtt
        new_device.pop("_id", None)
        publish_mqtt(
            contents=new_device,
            device_id=new_device['id'],
            method="post",
        )
        return jsonify({'output': "Device added successfully"}), 200
    return jsonify({'error': 'Missing required field'}), 400


# Deletes a device from the device list
@app.delete("/api/devices/<device_id>")
def delete_device(device_id):
    if id_exists(device_id):
        devices_collection.delete_one({"id": device_id})
        # new_device.pop("_id", None)
        publish_mqtt(
            contents={},
            device_id=device_id,
            method="delete",
        )
        return jsonify({"output": "Device was deleted from the database"}), 200
    return jsonify({"error": "ID not found"}), 404


# Changes a device configuration (i.e. name, room, or status) or adds a new configuration
@app.put("/api/devices/<device_id>")
def update_device(device_id):
    updated_device = request.json
    # Remove ID from the received device, to ensure it doesn't overwrite an existing ID
    id_to_update = updated_device.pop("id", None)
    if id_to_update and id_to_update != device_id:
        app.logger.error(f"ID mismatch: ID in URL: {device_id}, ID in payload: {id_to_update}")
        return jsonify({'error': f"ID mismatch: ID in URL: {device_id}, ID in payload: {id_to_update}"}), 400
    # Make sure that this endpoint is only used to update specific fields
    allowed_fields = ['room', 'name', 'status']
    for field in updated_device:
        if field not in allowed_fields:
            app.logger.error(f"Incorrect field in update endpoint: {field}")
            return jsonify({'error': f"Incorrect field in update endpoint: {field}"}), 400
    if id_exists(device_id):
        app.logger.info(f"Updating device {device_id}")
        for key, value in updated_device.items():
            app.logger.info(f"Setting parameter '{key}' to value '{value}'")
        # Find device by id and update the fields with 'set'
        devices_collection.update_one(
            {"id": device_id},
            {"$set": updated_device}
        )
        publish_mqtt(
            contents=updated_device,
            device_id=device_id,
            method="update",
        )
        return jsonify({'output': "Device updated successfully"}), 200
    return jsonify({'error': "Device not found"}), 404


# Sends a real time action to one of the devices.
# The request's JSON contains the parameters to update
# and their new values.
@app.post("/api/devices/<device_id>/action")
def rt_action(device_id):
    action = request.json
    app.logger.info(f"Device action {device_id}")
    device = devices_collection.find_one(filter={"id": device_id}, projection={'_id': 0})
    if device is None:
        app.logger.error(f"ID {device_id} not found")
        return jsonify({'error': "ID not found"}), 404
    if not validate_action_parameters(device['type'], action):
        return jsonify({'error': f"Incorrect field in update endpoint or unknown device type"}), 400
    update_fields = {}
    for key, value in action.items():
        app.logger.info(f"Setting parameter '{key}' to value '{value}'")
        update_fields[f"parameters.{key}"] = value
    devices_collection.update_one(
        {"id": device_id},
        {"$set": update_fields}
    )
    publish_mqtt(
        contents=action,
        device_id=device_id,
        method="action",
    )
    return jsonify({'output': "Action applied to device and published via MQTT"}), 200


@app.get("/healthy")
def health_check():
    return jsonify({"Status": "Healthy"})


@app.get("/ready")
def ready_check():
    try:
        app.logger.debug("Pinging DB . . .")
        mongo_client.admin.command('ping')
        app.logger.debug("Ping successful. Checking MQTT connection")
        if mqtt.is_connected():
            app.logger.debug("Connected")
            return jsonify({"Status": "Ready"})
        else:
            app.logger.debug("Not connected")
            return jsonify({"Status": "Not ready"}), 500
    except (ConnectionFailure, OperationFailure):
        app.logger.exception("Ping failed")
        return jsonify({"Status": "Not ready"}), 500


# Adds required headers to the response
@app.after_request
def after_request_combined(response):
    # Prometheus tracking
    if hasattr(request, 'start_time'):
        duration = time.time() - request.start_time
        REQUEST_COUNT.labels(request.method, request.path).inc()
        REQUEST_LATENCY.labels(request.path).observe(duration)

    # CORS headers
    if request.method == 'OPTIONS':
        response.headers['Allow'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'HEAD, DELETE, POST, GET, OPTIONS, PUT, PATCH'
    response.headers['Access-Control-Allow-Headers'] = '*'
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response


# Function to run when shutting down the server
@atexit.register
def on_shutdown():
    mqtt.loop_stop()
    mqtt.disconnect()
    mongo_client.close()
    app.logger.info("Shutting down")


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5200, debug=True, use_reloader=False)
