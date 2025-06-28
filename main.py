from flask import Flask, jsonify, request, Response
from flask_mqtt import Mqtt
import json
import atexit
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from dotenv import load_dotenv
import os
import time
from prometheus_client import Counter, Histogram, generate_latest

# Env variables
load_dotenv()

username = os.getenv("MONGO_USER")
password = os.getenv("MONGO_PASS")

# Database parameters
uri = f"mongodb+srv://{username}:{password}@smart-home-db.w9dsqtr.mongodb.net/?retryWrites=true&w=majority&appName=smart-home-db"
client = MongoClient(uri, server_api=ServerApi('1'))

db = client["smart_home"]
devices_collection = db["devices"]

# Setting up the MQTT client
BROKER_URL = "test.mosquitto.org"
BROKER_PORT = 1883  # MQTT, unencrypted, unauthenticated

REQUEST_COUNT = Counter('request_count', 'Total Request Count', ['method', 'endpoint'])
REQUEST_LATENCY = Histogram('request_latency_seconds', 'Request latency', ['endpoint'])

# Validates that the request data contains all the required fields
def validate_device_data(new_device):
    required_fields = ['id', 'type', 'room', 'name', 'status', 'parameters']
    for field in required_fields:
        if field not in new_device:
            return False
    return True


# Checks the validity of the device id
def id_exists(device_id):
    devices = list(devices_collection.find({}, {'_id': 0}))
    for device in devices:
        if device_id == device["id"]:
            return True
    return False


app = Flask(__name__)
app.config['MQTT_BROKER_URL'] = BROKER_URL
app.config['MQTT_BROKER_PORT'] = BROKER_PORT
app.config['MQTT_USERNAME'] = ''  # set the username here if you need authentication for the broker
app.config['MQTT_PASSWORD'] = ''  # set the password here if the broker demands authentication
app.config['MQTT_KEEPALIVE'] = 5  # set the time interval for sending a ping to the broker to 5 seconds
app.config['MQTT_TLS_ENABLED'] = False  # set TLS to disabled for testing purposes

mqtt = Mqtt(app)


# Formats and publishes the mqtt topic and payload -> the mqtt publisher
def publish_mqtt(contents: dict, device_id: str, method: str):
    topic = f"project/home/{device_id}/{method}"
    payload = json.dumps({
        "sender": "backend",
        "contents": contents,
    })
    mqtt.publish(topic, payload.encode(), qos=2)


# Function to run after the MQTT client finishes connecting to the broker
@mqtt.on_connect()
def on_connect(mqtt_client, userdata, flags, rc):
    mqtt_client.subscribe("project/home/#")


# Receives the published mqtt payloads and updates the database accordingly
@mqtt.on_message()
def on_message(mqtt_client, userdata, msg):
    app.logger.info(f"MQTT Message Received on {msg.topic}")
    try:
        payload = json.loads(msg.payload.decode())
        app.logger.info(f"Payload: {payload}")
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
            devices = list(devices_collection.find({}, {'_id': 0}))
            match method:
                case "action":
                    # Only update device parameters
                    for device in devices:
                        if device['id'] == device_id:
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
                    app.logger.error(f"Device ID {device_id} not found")
                case "update":
                    # Only update device configuration (i.e. name, status, and room)
                    for device in devices:
                        if device['id'] == device_id:
                            for key, value in payload.items():
                                app.logger.info(f"Setting parameter '{key}' to value '{value}'")
                                # Find device by id and update the fields with 'set'
                            devices_collection.update_one(
                                {"id": device_id},
                                {"$set": payload}
                            )
                            return
                    app.logger.error(f"Device ID {device_id} not found")
                case "post":
                    # Add a new device to the database
                    if validate_device_data(payload):
                        if id_exists(payload["id"]) or id_exists(device_id):
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

    except UnicodeError as e:
        app.logger.exception(f"Error decoding payload: {e.reason}")

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
    devices = list(devices_collection.find({}, {'_id': 0}))
    for device in devices:
        if device_id == device['id']:
            return device
    return jsonify({'error': "ID not found"}), 400


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


# Changes a device configuration or adds a new configuration
@app.put("/api/devices/<device_id>")
def update_device(device_id):
    updated_device = request.json
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
    if id_exists(device_id):
        app.logger.info(f"Device action {device_id}")
        update_fields = {}

        for key, value in action.items():
            app.logger.info(f"Setting parameter '{key}' to value '{value}'")
            field_name = f"parameters.{key}"
            update_fields[field_name] = value

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
    return jsonify({'error': "ID not found"}), 404


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
def on_shutdown():
    app.logger.info("Shutting down")


atexit.register(on_shutdown)

# GET /api/devices: Get a list of all smart devices with their status.
# POST /api/devices: Register a new smart device (requires device_id, type, and location in payload).
# PUT /api/devices/<device_id>: Update a device's configuration or status (e.g., turn on/off).
# DELETE /api/devices/<device_id>: Remove a smart device.
# Real-Time Actions:
# POST /api/devices/<device_id>/action: Send a command to a device (requires action and
#       optional parameters in JSON payload).
# Device Analytics:
# GET /api/devices/analytics: Fetch usage patterns and status trends for devices.

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5200, debug=True, use_reloader=False)
