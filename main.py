from flask import Flask, jsonify, request, Response
from werkzeug.middleware.proxy_fix import ProxyFix
from typing import Any, cast, Mapping, Union
import paho.mqtt.client as paho
from paho.mqtt.properties import Properties
from paho.mqtt.packettypes import PacketTypes
import json
import atexit
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from pymongo.errors import ConnectionFailure, ConfigurationError, OperationFailure
from dotenv import load_dotenv
from datetime import datetime, timedelta, UTC
import re
import os
import time
import random
import sys
import requests
import logging.handlers
from prometheus_client import generate_latest
from services.redis_client import r

# Validation
from validation.validators import (
    validate_action_parameters,
    validate_device_data,
)

# Monitoring
from monitoring.metrics import (
    request_count,
    request_latency,
    update_binary_device_status,
    get_device_on_interval_at_time,
    mark_device_read,
    device_metrics_action,
    update_device_metrics,
    query_prometheus,
    query_prometheus_point_increase,
    query_prometheus_range
)

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

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

# Env variables
load_dotenv("/config/constants.env")

# How many times to attempt a connection request
RETRIES = 5

# Setting up the MQTT client
BROKER_HOST = os.getenv("BROKER_HOST", "test.mosquitto.org")
BROKER_PORT = int(os.getenv("BROKER_PORT", 1883))

MONGO_DB_CONNECTION_STRING = os.getenv("MONGO_DB_CONNECTION_STRING")
MONGO_USER = os.getenv("MONGO_USER")
MONGO_PASS = os.getenv("MONGO_PASS")


# Checks the validity of the device id
def id_exists(device_id):
    device = devices_collection.find_one({"id": device_id}, {'_id': 0})
    return device is not None


# Database parameters
uri = MONGO_DB_CONNECTION_STRING if MONGO_DB_CONNECTION_STRING is not None else (
    f"mongodb+srv://{MONGO_USER}:{MONGO_PASS}"
    f"@smart-home-devices.u2axxrl.mongodb.net/?retryWrites=true&w=majority&appName=smart-home-devices"
)
try:
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

db = mongo_client["smart-home-devices"]
devices_collection = db["devices"]

client_id = f"flask-backend-{os.getenv('HOSTNAME')}"
mqtt = paho.Client(paho.CallbackAPIVersion.VERSION2, protocol=paho.MQTTv5, client_id=client_id)


# Function to run after the MQTT client finishes connecting to the broker
def on_connect(client, _userdata, _connect_flags, reason_code, _properties) -> None:
    app.logger.info(f'CONNACK received with code {reason_code}.')
    if reason_code == 0:
        app.logger.info("Connected successfully")
        client.subscribe("$share/backend/nadavnv-smart-home/devices/#")
    else:
        app.logger.error(f"Connection failed with code {reason_code}")


def on_disconnect(_client, _userdata, _disconnect_flags, reason_code, _properties=None) -> None:
    app.logger.warning(f"Disconnected from broker with reason: {reason_code}")


# Receives the published mqtt payloads and updates the database accordingly
def on_message(_mqtt_client, _userdata, msg: paho.MQTTMessage):
    sender_id = None
    props = msg.properties
    user_props = getattr(props, "UserProperty", None)
    if user_props is not None:
        sender_id = dict(user_props).get("sender_id")

    if sender_id is None:
        app.logger.error("Message missing sender")

    if sender_id == client_id:
        return

    app.logger.info(f"MQTT Message Received on {msg.topic}")
    payload = cast(bytes, msg.payload)
    try:
        payload = json.loads(payload.decode("utf-8"))
    except UnicodeDecodeError as e:
        app.logger.exception(f"Error decoding payload: {e.reason}")
        return
    payload = payload["contents"]
    # Extract device_id from topic: expected format nadavnv-smart-home/devices/<device_id>/<method>
    topic_parts = msg.topic.split('/')
    if len(topic_parts) == 4:
        device_id = topic_parts[2]
        method = topic_parts[-1]
        device = devices_collection.find_one({"id": device_id}, {"_id": 0})
        if device is None:
            app.logger.error(f"Device ID {device_id} not found")
            return
        match method:
            # TODO: Fold action into update
            # TODO: Validate all parameters before updating metrics
            case "action":
                # Only update device parameters
                if not validate_action_parameters(device['type'], payload):
                    return
                update_fields = {}
                for key, value in payload.items():
                    app.logger.info(f"Setting parameter '{key}' to value '{value}'")
                    success, reason = device_metrics_action(device, key, value)
                    if not success:
                        return
                    update_fields[f"parameters.{key}"] = value
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
                update_device_metrics(device, payload)
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
                success, reason = validate_device_data(payload)
                if success:
                    if id_exists(payload["id"]):
                        app.logger.error("ID already exists")
                        return
                    devices_collection.insert_one(payload)
                    app.logger.info("Device added successfully")
                    return
                app.logger.error(f"Missing required field {reason}")
                return
            case "delete":
                # Remove a device from the database
                if id_exists(device_id):
                    if device["status"] == "on":
                        # Calculate device usage, etc.
                        update_binary_device_status(device, "off")
                    devices_collection.delete_one({"id": device_id})
                    app.logger.info("Device deleted successfully")
                    return
                app.logger.error("ID not found")
                return
            case _:
                app.logger.error(f"Unknown method: {method}")
    else:
        app.logger.error(f"Incorrect topic {msg.topic}")


mqtt.on_connect = on_connect
mqtt.on_disconnect = on_disconnect
mqtt.on_message = on_message

app.logger.info(f"Connecting to MQTT broker {BROKER_HOST}:{BROKER_PORT}...")

mqtt.connect_async(BROKER_HOST, BROKER_PORT)
mqtt.loop_start()


# Formats and publishes the mqtt topic and payload -> the mqtt publisher
def publish_mqtt(contents: dict, device_id: str, method: str):
    topic = f"nadavnv-smart-home/devices/{device_id}/{method}"
    payload = json.dumps({
        "sender": "backend",
        "contents": contents,
    })
    properties = Properties(PacketTypes.PUBLISH)
    properties.UserProperty = [("sender_id", client_id)]
    mqtt.publish(topic, payload.encode(), qos=2, properties=properties)


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
    for device in devices:
        if "id" in device:
            if not r.sismember("seen_devices", device["id"]):
                mark_device_read(device)
    return jsonify(devices)


# Get data on a specific device by its ID
@app.get("/api/devices/<device_id>")
def get_device(device_id):
    device = devices_collection.find_one({'id': device_id}, {'_id': 0})
    if "id" in device:
        if not r.sismember("seen_devices", device["id"]):
            mark_device_read(device)
    if device is not None:
        return jsonify(device)
    app.logger.error(f"ID {device_id} not found")
    return jsonify({'error': f"ID {device_id} not found"}), 400


# Adds a new device
@app.post("/api/devices")
def add_device():
    new_device = request.json
    success, reason = validate_device_data(new_device)
    if success:
        if id_exists(new_device["id"]):
            return jsonify({'error': "ID already exists"}), 400
        devices_collection.insert_one(new_device)
        mark_device_read(new_device)
        # Remove MongoDB unique id (_id) before publishing to mqtt
        new_device.pop("_id", None)
        publish_mqtt(
            contents=new_device,
            device_id=new_device['id'],
            method="post",
        )
        return jsonify({'output': "Device added successfully"}), 200
    return jsonify({'error': f'Missing required field {reason}'}), 400


# Deletes a device from the device list
@app.delete("/api/devices/<device_id>")
def delete_device(device_id):
    if id_exists(device_id):
        r.srem("seen_devices", device_id)  # Allows adding a new device with old id
        devices_collection.delete_one({"id": device_id})
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
    # TODO: Validate all parameters before updating metrics
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
        device = devices_collection.find_one({'id': device_id}, {'_id': 0})
        update_device_metrics(device, updated_device)
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
    # TODO: Fold action into update
    # TODO: Validate all parameters before updating metrics
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
        success, reason = device_metrics_action(device, key, value)
        if not success:
            return jsonify({'error': reason}), 400
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


@app.get("/api/devices/analytics")
def device_analytics():
    try:
        body = request.get_json(silent=True) or {}
        app.logger.debug(f"Received analytics request body: {body}")

        now = datetime.now(UTC)
        to_ts = datetime.fromisoformat(body.get("to")) if "to" in body else now
        from_ts = datetime.fromisoformat(body.get("from")) if "from" in body else to_ts - timedelta(days=7)

        # Safety check
        if from_ts >= to_ts:
            return jsonify({"error": "'from' must be before 'to'"}), 400

        usage_results = query_prometheus_point_increase("device_usage_seconds_total", from_ts, to_ts)
        event_results = query_prometheus_point_increase("device_on_events_total", from_ts, to_ts)

        if isinstance(usage_results, dict) and "error" in usage_results:
            app.logger.error(f"Prometheus usage query failed: {usage_results['error']}")
            return jsonify({"error": "Failed to query Prometheus", "details": usage_results["error"]}), 500
        if isinstance(event_results, dict) and "error" in usage_results:
            app.logger.error(f"Prometheus usage query failed: {event_results['error']}")
            return jsonify({"error": "Failed to query Prometheus", "details": event_results["error"]}), 500

        device_analytics_json = {}

        for item in usage_results:
            if "value" not in item:
                app.logger.warning(f"Missing 'value' in usage result: {item}")
                continue
            device_id = item["metric"].get("device_id", "unknown")
            usage_seconds = float(item["value"][1])
            app.logger.info(f"Device {device_id} usage seconds: {usage_seconds}")
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
                app.logger.warning(f"Missing 'value' in event result: {item}")
                continue
            device_id = item["metric"].get("device_id", "unknown")
            on_count = int(float(item["value"][1]))
            app.logger.info(f"Device {device_id} on count: {on_count}")
            device_analytics_json.setdefault(device_id, {})["on_events"] = on_count

        app.logger.info(json.dumps(device_analytics_json, indent=4, sort_keys=True))
        total_usage = sum(d.get("total_usage_minutes", 0) for d in device_analytics_json.values())
        total_on_events = sum(d.get("on_events", 0) for d in device_analytics_json.values())

        response = {
            "analytics_window": {
                "from": from_ts.isoformat(),
                "to": to_ts.isoformat()
            },
            "aggregate": {
                "total_devices": r.scard("seen_devices"),
                "total_on_events": total_on_events,
                "total_usage_minutes": total_usage
            },
            "on_devices": device_analytics_json,
            "message": "For full analytics, charts, and trends, visit the Grafana dashboard."
        }

        app.logger.debug(f"Returning analytics response: {response}")
        return jsonify(response)
    except Exception as e:
        app.logger.exception("Unexpected error in /api/devices/analytics")
        return jsonify({"error": "Internal server error", "details": str(e)}), 500


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
