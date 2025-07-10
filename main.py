from flask import Flask, jsonify, request, Response
from werkzeug.middleware.proxy_fix import ProxyFix
from typing import Union, Any, List, Dict
import paho.mqtt.client as paho
import json
import atexit
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from pymongo.errors import ConnectionFailure, ConfigurationError, OperationFailure
from dotenv import load_dotenv
from datetime import datetime, timedelta, UTC
import os
import time
import random
import sys
import requests
import logging.handlers
from prometheus_client import Gauge, Counter, Histogram, generate_latest

logging.basicConfig(
    format="[%(asctime)s] %(levelname)s in %(module)s: %(message)s",
    handlers=[
        # Prints to sys.stderr
        logging.StreamHandler(),
        # Writes to a log file which rotates every 1mb, or gets overwritten when the app is restarted
        logging.handlers.RotatingFileHandler(
            filename="simulator.log",
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

PROMETHEUS_URL = "http://smart-home-prometheus-svc.smart-home.svc.cluster.local:9090"
# Prometheus metrics
# HTTP request metrics
request_count = Counter('request_count', 'Total Request Count', ['method', 'endpoint'])
request_latency = Histogram('request_latency_seconds', 'Request latency', ['endpoint'])

# Device metrics
device_status = Gauge("device_status", "Device on/off state", ["device_id", "device_type", "device_name"])
device_usage_seconds_total = Gauge("device_usage_seconds_total", "Cumulative usage",
                                   ["device_id", "device_type", "device_name"])
device_on_events = Counter("device_on_events_total", "Number of times device turned on",
                           ["device_id", "device_type", "device_name"])
device_usage_seconds = Counter("device_usage_seconds_total", "Total on-time in seconds",
                               ["device_id", "device_type", "device_name"])
ac_temperature = Gauge("ac_temperature", "Current temperature (AC)", ["device_id", "device_type", "device_name"])
ac_mode_status = Gauge("ac_mode_status", "Current active mode of air conditioners",
                       ["device_id", "device_name", "mode"])
ac_swing_status = Gauge("ac_swing_status", "Current swing mode of air conditioners",
                        ["device_id", "device_name", "mode"])
ac_fan_status = Gauge("ac_fan_status", "Current fan mode of air conditioners",
                      ["device_id", "device_name", "mode"])
water_heater_temperature = Gauge("water_heater_temperature", "Current temperature (water heater)",
                                 ["device_id", "device_type", "device_name"])
water_heater_target_temperature = Gauge("water_heater_target_temperature", "Target temperature",
                                        ["device_id", "device_type", "device_name"])
water_heater_is_heating_status = Gauge("water_heater_is_heating_status", "Water heater is heating",
                                       ["device_id", "device_type", "device_name"])
water_heater_timer_enabled_status = Gauge("water_heater_is_heating_status", "Water heater timer enabled",
                                          ["device_id", "device_type", "device_name"])
water_heater_schedule_info = Gauge("water_heater_schedule_info", "Water heater schedule info",
                                   ["device_id", "device_type", "device_name", "scheduled_on", "scheduled_off"])

# For tracking usage
device_on_timestamps = {}


# Validates that the request data contains all the required fields
def validate_device_data(new_device):
    required_fields = ['id', 'type', 'room', 'name', 'status', 'parameters']
    for field in required_fields:
        if field not in new_device:
            return False
    return True


# Checks the validity of the device id
def id_exists(device_id):
    device = devices_collection.find_one({"id": device_id}, {'_id': 0})
    return device is not None


app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

# Database parameters
uri = (
        f"mongodb+srv://{username}:{password}" +
        "@smart-home-db.w9dsqtr.mongodb.net/?retryWrites=true&w=majority&appName=smart-home-db"
)
try:
    app.logger.info("Connecting to DB...")
    mongo_client = MongoClient(uri, server_api=ServerApi('1'))
except ConfigurationError:
    app.logger.exception("Failed to connect to database. Shutting down.")
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

try:
    mongo_client.admin.command('ping')
except ConnectionFailure:
    app.logger.error("Failed to connect to database. Shutting down.")
    sys.exit(1)

db = mongo_client["smart_home"]
devices_collection = db["devices"]

mqtt = paho.Client(paho.CallbackAPIVersion.VERSION2, protocol=paho.MQTTv5)


# Function to run after the MQTT client finishes connecting to the broker
def on_connect(client, userdata, connect_flags, reason_code, properties):
    app.logger.info(f'CONNACK received with code {reason_code}.')
    if reason_code == 0:
        app.logger.info("Connected successfully")
        client.subscribe("project/home/#")
    else:
        app.logger.error(f"Connection failed with code {reason_code}")


def on_disconnect(client, userdata, disconnect_flags, reason_code, properties=None):
    app.logger.warning(f"Disconnected from broker with reason: {reason_code}")


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
mqtt.on_disconnect = on_disconnect
mqtt.on_message = on_message

app.logger.info(f"Connecting to MQTT broker {BROKER_URL}:{BROKER_PORT}...")

# # Force connecting to MQTT broker via IPv4
# def get_ipv4_address(hostname):
#     app.logger.info(f"Forcing IPv4 for hostname {hostname}")
#     for res in socket.getaddrinfo(hostname, None):
#         if res[0] == socket.AF_INET:  # IPv4
#             return res[4][0]
#     raise Exception(f"No IPv4 address found for {hostname}")
#
#
# broker_ip = get_ipv4_address(BROKER_URL)
mqtt.connect_async(BROKER_URL, BROKER_PORT)
mqtt.loop_start()


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


def query_prometheus(query) -> Union[List[Dict[str, Any]], Dict[str, str]]:
    try:
        app.logger.debug(f"Querying Prometheus: {query}")
        resp = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": query}, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        app.logger.debug(f"Prometheus response for query '{query}': {data}")
        return data.get("data", {}).get("result", [])
    except requests.RequestException as e:
        app.logger.exception(f"Error querying Prometheus for query '{query}'")
        return {"error": str(e)}


@app.get("/api/devices/analytics")
def device_analytics():
    try:
        body = request.get_json(silent=True) or {}
        app.logger.debug(f"Received analytics request body: {body}")

        now = datetime.now(UTC)
        week_ago = now - timedelta(days=7)

        usage_results = query_prometheus("device_usage_seconds_total")
        event_results = query_prometheus("device_on_events_total")

        if isinstance(usage_results, dict) and "error" in usage_results:
            app.logger.error(f"Prometheus usage query failed: {usage_results['error']}")
            return jsonify({"error": "Failed to query Prometheus", "details": usage_results["error"]}), 500

        device_analytics_json = {}

        for item in usage_results:
            device_id = item["metric"].get("device_id", "unknown")
            usage_seconds = float(item["value"][1])
            device_analytics_json.setdefault(device_id, {})["total_usage_minutes"] = usage_seconds / 60

        for item in event_results:
            device_id = item["metric"].get("device_id", "unknown")
            on_count = int(float(item["value"][1]))
            device_analytics_json.setdefault(device_id, {})["on_events"] = on_count

        total_usage = sum(d.get("total_usage_minutes", 0) for d in device_analytics_json.values())
        total_on_events = sum(d.get("on_events", 0) for d in device_analytics_json.values())

        response = {
            "analytics_window": {
                "from": week_ago.isoformat(),
                "to": now.isoformat()
            },
            "aggregate": {
                "total_devices": len(device_analytics_json),
                "total_on_events": total_on_events,
                "total_usage_minutes": total_usage
            },
            "devices": device_analytics_json
        }

        app.logger.debug(f"Returning analytics response: {response}")
        return jsonify(response)

    except Exception as e:
        app.logger.exception("Unexpected error in /api/devices/analytics")
        return jsonify({"error": "Internal server error", "details": str(e)}), 500


# Adds required headers to the response
@app.after_request
def after_request_combined(response):
    # Prometheus tracking
    if hasattr(request, 'start_time'):
        duration = time.time() - request.start_time
        request_count.labels(request.method, request.path).inc()
        request_latency.labels(request.path).observe(duration)

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
