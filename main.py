import config.env  # noqa: F401  # load_dotenv side effect
from flask import Flask, jsonify, request, Response
from werkzeug.middleware.proxy_fix import ProxyFix
from typing import Any, cast, Mapping
import paho.mqtt.client as paho
from paho.mqtt.properties import Properties
from paho.mqtt.packettypes import PacketTypes
import json
import atexit
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from pymongo.errors import ConnectionFailure, ConfigurationError, OperationFailure
from dotenv import load_dotenv
import os
import time
import random
import sys
import logging.handlers
from prometheus_client import generate_latest
from services.redis_client import r  # TODO: Add to dockerfile

# Validation
from validation.validators import validate_device_data, validate_new_device_data

# Monitoring
from monitoring.metrics import (
    request_count,
    request_latency,
    mark_device_read,
    update_device_metrics,
    generate_analytics,
    update_device_status,
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

smart_home_logger = logging.getLogger("smart_home")
smart_home_logger.propagate = True
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
app.logger.propagate = False

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


def on_connect(client: paho.Client, _userdata, _connect_flags, reason_code, _properties) -> None:
    """
    Function to run after the MQTT client finishes connecting to the broker. Logs the
    connection and subscribes to the project topic.

    :param paho.Client client: The MQTT client instance for this callback.
    :param _userdata: Unused by this function.
    :param _connect_flags: Unused by this function.
    :param reason_code: The connection reason code received from the broker.
    :param _properties: Unused by this function.
    :return: None
    :rtype: None
    """
    app.logger.info(f'CONNACK received with code {reason_code}.')
    if reason_code == 0:
        app.logger.info("Connected successfully")
        client.subscribe("$share/backend/nadavnv-smart-home/devices/#")
    else:
        app.logger.error(f"Connection failed with code {reason_code}")


def on_disconnect(_client, _userdata, _disconnect_flags, reason_code, _properties=None) -> None:
    """
    Function to run after the MQTT client disconnects.

    :param _client: Unused by this function.
    :param _userdata: Unused by this function.
    :param _disconnect_flags: Unused by this function.
    :param reason_code: The disconnection reason code possibly received from the broker.
    :param _properties: Unused by this function.
    :return: None
    :rtype: None
    """
    app.logger.warning(f"Disconnected from broker with reason: {reason_code}")


def on_message(_mqtt_client, _userdata, msg: paho.MQTTMessage) -> None:
    """
    Receives the published MQTT payloads and updates the database accordingly.

    Validates the device data and updates metrics and the database if it's valid.

    :param _mqtt_client: Unused by this function.
    :param _userdata: Unused by this function.
    :param paho.MQTTMessage msg: The MQTT message received from the broker.
    :return: None
    :rtype: None
    """
    sender_id, sender_group = None, None
    props = msg.properties
    user_props = getattr(props, "UserProperty", None)
    if user_props is not None:
        sender_id = dict(user_props).get("sender_id")
        sender_group = dict(user_props).get("sender_group")
    if sender_id is None:
        app.logger.error("Message missing sender")
    if sender_group is None:
        app.logger.error("Message missing sender group")
    if sender_id == client_id or sender_group == "backend":
        # Ignore backend messages
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
            case "update":
                # Update an existing device
                if "id" in payload and payload["id"] != device_id:
                    app.logger.error(f"ID mismatch: ID in URL: {device_id}, ID in payload: {payload['id']}")
                    return
                success, reason = validate_device_data(device)
                if success:
                    update_device(device, payload)
                    return
            case "post":
                # Add a new device to the database
                if "id" in payload and payload["id"] != device_id:
                    app.logger.error(f"ID mismatch: ID in URL: {device_id}, ID in payload: {payload['id']}")
                    return
                success, reason = validate_new_device_data(payload)
                if success:
                    if id_exists(payload["id"]):
                        app.logger.error("ID already exists")
                        return
                    devices_collection.insert_one(payload)
                    app.logger.info("Device added successfully")
                    return
                else:
                    return
            case "delete":
                # Remove a device from the database
                if id_exists(device_id):
                    if device["status"] == "on":
                        # Calculate device usage, etc.
                        update_device_status(device, "off")
                    devices_collection.delete_one({"id": device_id})
                    app.logger.info("Device deleted successfully")
                    return
                app.logger.error(f"ID {device_id} not found")
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


def publish_mqtt(contents: dict[str, Any], device_id: str, method: str) -> None:
    """
    Publishes the MQTT message to the broker.

    Formats the message and adds appropriate sender id and sender group tags.

    :param dict[str, Any] contents: The message to be published.
    :param str device_id: ID of the device the message is about.
    :param str method: The method to attach to the topic, either "update", "post" or "delete".
    :return: None
    :rtype: None
    """
    topic = f"nadavnv-smart-home/devices/{device_id}/{method}"
    payload = json.dumps({
        "contents": contents,
    })
    properties = Properties(PacketTypes.PUBLISH)
    properties.UserProperty = [("sender_id", client_id), ("sender_group", "backend")]
    mqtt.publish(topic, payload.encode("utf-8"), qos=2, properties=properties)


def id_exists(device_id: str) -> bool:
    """
    Check if a device ID exists in the Mongo database.

    :param str device_id: Device ID to check.
    :return: True if the device ID exists, False otherwise.
    :rtype: bool
    """
    device = devices_collection.find_one({"id": device_id}, {'_id': 0})
    return device is not None


@app.before_request
def before_request() -> None:
    """
    Function to run before each request. Used for calculating message latency.

    :return: None
    :rtype: None
    """
    request.start_time = time.time()


@app.get("/metrics")
def metrics() -> tuple[Response, int]:
    """
    Used by Prometheus to get gathered metrics.

    :return: The gathered metrics in plain text.
    :rtype: Response
    """
    return Response(generate_latest(), mimetype="text/plain"), 200


@app.get("/api/ids")
def get_device_ids() -> tuple[Response, int]:
    """
    Returns a list of all device IDs currently in the Mongo database.

    :return: List of device IDs.
    :rtype: tuple[Response, int]
    """
    device_ids = list(devices_collection.find({}, {'id': 1, '_id': 0}))
    return jsonify([device_id['id'] for device_id in device_ids]), 200


@app.get("/api/devices")
def get_all_devices() -> tuple[Response, int]:
    """
    Returns a list of all devices currently in the Mongo database and their details.
    :return: List of devices.
    :rtype: tuple[Response, int]
    """
    devices = list(devices_collection.find({}, {'_id': 0}))
    for device in devices:
        if "id" in device:
            if not r.sismember("seen_devices", device["id"]):
                mark_device_read(device)
    return jsonify(devices), 200


@app.get("/api/devices/<device_id>")
def get_device(device_id) -> tuple[Response, int]:
    """
    Returns a single device from the Mongo database.
    :param device_id:
    :return:
    """
    device = devices_collection.find_one({'id': device_id}, {'_id': 0})
    if device is not None:
        if not r.sismember("seen_devices", device["id"]):
            mark_device_read(device)
        return jsonify(device), 200
    else:
        app.logger.error(f"ID {device_id} not found")
        return jsonify({'error': f"ID {device_id} not found"}), 400


@app.post("/api/devices")
def add_device() -> tuple[Response, int]:
    """
    Adds a new device to the Mongo database.

    Returns {'output': "Device added successfully"} on success and {'error': <reason>} on failure.
    :return: Response.
    :rtype: tuple[Response, int]
    """
    new_device = request.json
    success, reason = validate_new_device_data(new_device)
    if success:
        if id_exists(new_device["id"]):
            return jsonify({'error': f"ID {new_device["id"]} already exists"}), 400
        else:
            devices_collection.insert_one(new_device)
            mark_device_read(new_device)
            publish_mqtt(
                contents=new_device,
                device_id=new_device['id'],
                method="post",
            )
            return jsonify({'output': "Device added successfully"}), 200
    else:
        return jsonify({'error': f'{reason}'}), 400


@app.delete("/api/devices/<device_id>")
def delete_device(device_id: str) -> tuple[Response, int]:
    """
    Deletes a device from the Mongo database.

    Returns {'output': "Device was deleted from the database"} on success and {'error': <reason>} on failure.
    :param str device_id: ID of the device to delete.
    :return: Response.
    :rtype: tuple[Response, int]
    """
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


def update_device(old_device: Mapping[str, Any], updated_device: Mapping[str, Any]) -> None:
    """
    Updates a device to a new configuration in the Mongo database and in the Prometheus metrics.

    :param Mapping[str, Any] old_device: Previous device configuration
    :param Mapping[str, Any] updated_device: New device configuration
    :return: None
    :rtype: None
    """
    update_device_metrics(old_device, updated_device)
    update_fields = {}
    for key, value in updated_device.items():
        if key != "parameters":
            update_fields[key] = value
        else:
            for param, param_value in updated_device[key].items():
                update_fields[f"parameters.{param}"] = param_value
    # Find device by id and update the fields with 'set'
    devices_collection.update_one(
        {"id": old_device["id"]},
        {"$set": update_fields}
    )


@app.put("/api/devices/<device_id>")
def update_device_endpoint(device_id: str) -> tuple[Response, int]:
    """
    Updates a device in the Mongo database. A JSON object representing the new device configuration
    must be included in the request body.

    Validates the new device configuration and updates the database if it is valid, returning
    {'output': "Device updated successfully"}, or {'error': <reason>} on failure.

    :param str device_id: ID of the device to update.
    :return: Response.
    :rtype: tuple[Response, int]
    """
    # TODO: Validate all parameters before updating metrics
    updated_device = request.json
    # Remove ID from the received device, to ensure it doesn't overwrite an existing ID
    id_to_update = updated_device.pop("id", None)
    if id_to_update is not None and id_to_update != device_id:
        error = f"ID mismatch: ID in URL: {device_id}, ID in payload: {id_to_update}"
        app.logger.error(error)
        return jsonify({'error': error}), 400
    device = devices_collection.find_one({'id': device_id}, {'_id': 0})
    if device is not None:
        app.logger.info("Validating new device configuration...")
        success, reason = validate_device_data(updated_device)
        if success:
            app.logger.info(f"Success! Updating device {device_id}")
            update_device(device, updated_device)
            publish_mqtt(
                contents=updated_device,
                device_id=device_id,
                method="update",
            )
            return jsonify({'output': "Device updated successfully"}), 200
        else:
            return jsonify({'error': f"{reason}"}), 400
    return jsonify({'error': f"Device ID {device_id} not found"}), 404


@app.get("/api/devices/analytics")
def device_analytics() -> tuple[Response, int]:
    """
    Generates a json object of aggregate and individual device metrics.

    :return: A flask Response object and an HTTP status code.
    :rtype: tuple[Response, int]
    """
    return generate_analytics()


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
    """
    Function to run just before the HTTP response is sent. Used to calculate HTTP metrics
    and to add response headers.

    :param response: The HTTP response to send.
    :return: The modified HTTP response.
    """
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


@atexit.register
def on_shutdown() -> None:
    """
    Function to run when shutting down the server. Disconnects from MQTT broker
    and DB clients.

    :return: None
    :rtype: None
    """
    mqtt.loop_stop()
    mqtt.disconnect()
    mongo_client.close()
    r.close()
    app.logger.info("Shutting down")


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5200, debug=True, use_reloader=False)
