from flask import Flask, jsonify, request
from flask_mqtt import Mqtt
import json
import atexit

# Setting up the MQTT client
BROKER_URL = "test.mosquitto.org"
BROKER_PORT = 1883  # MQTT, unencrypted, unauthenticated

# Temporary local json -> stand in for a future database
DATA_FILE_NAME = r"./devices.json"

with open(DATA_FILE_NAME, mode="r", encoding="utf-8") as read_file:
    data = json.load(read_file)


# Prints out an output of the received mqtt messages
def print_device_action(device_name, action_payload, prefix=""):
    for key, value in action_payload.items():
        if isinstance(value, dict):
            # Special case: skip printing "parameters" as a word in output
            if key == "parameters":
                print_device_action(device_name, value, prefix=prefix)
            else:
                print_device_action(device_name, value, prefix=f"{prefix}{key} ")
        else:
            app.logger.info(f"{device_name} {prefix}{key} set to {value}")


# Validates that the request data contains all the required fields
def validate_device_data(new_device):
    required_fields = ['id', 'type', 'room', 'name', 'status', 'parameters']
    for field in required_fields:
        if field not in new_device:
            return False
    return True


# Checks the validity of the device id
def check_id(device_id):
    for device in data:
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


# Function to run after the MQTT client finishes connecting to the broker
@mqtt.on_connect()
def on_connect(client, userdata, flags, rc):
    client.subscribe("project/home/#")


# Receives the published mqtt payloads -> the mqtt subscriber
@mqtt.on_message()
def on_message(client, userdata, msg):
    app.logger.info(f"MQTT Message Received on {msg.topic}")
    try:
        payload = json.loads(msg.payload.decode())
        app.logger.info(f"Payload: {payload}")

        # Extract device_id from topic: expected format project/home/<room>/<device_id>/action
        device_name = None
        topic_parts = msg.topic.split('/')
        if len(topic_parts) >= 5:
            device_id = topic_parts[3]
            if "id" in payload and device_id != payload["id"]:
                app.logger.error(f"ID mismatch - ID in payload: {payload["id"]}, ID in topic: {device_id}")
                return
            for device in data:
                if device['id'] == device_id:
                    for key, value in payload.items():
                        if key == "id":
                            continue
                        elif key == "parameters":
                            for param_key, param_value in payload["parameters"].items():
                                app.logger.info(f"Setting parameter '{param_key}' to value '{param_value}'")
                                device['parameters'][param_key] = param_value
                        else:
                            app.logger.info(f"Setting parameter '{key}' to value '{value}'")
                            device[key] = value
                    device_name = device["name"]
                    return
            app.logger.error(f"Device ID {device_id} not found")

        # print_device_action(
        #     device_name="Unknown device" if device_name is None else device_name,
        #     action_payload=payload
        # )

    except UnicodeError as e:
        app.logger.exception(f"Error decoding payload: {e.reason}")


# Returns a list of device IDs
@app.get("/api/ids")
def device_ids():
    return [device["id"] for device in data]


# Presents a list of all your devices and their configuration
@app.get("/api/devices")
def all_devices():
    return data


# Get data on a specific device by its ID
@app.get("/api/devices/<device_id>")
def get_device(device_id):
    for device in data:
        if device_id == device["id"]:
            return device
    return jsonify({'error': "ID not found"}), 400


# Adds a new device
@app.post("/api/devices")
def add_device():
    new_device = request.json
    if validate_device_data(new_device):
        if check_id(new_device["id"]):
            return jsonify({'error': "ID already exists"}), 400
        data.append(new_device)

        return jsonify({'output': "device added successfully"}), 200
    return jsonify({'error': 'Missing required field'}), 400


# Deletes a device from the device list
@app.delete("/api/devices/<device_id>")
def delete_device(device_id):
    if check_id(device_id):
        for index, device in enumerate(data):
            if device["id"] == device_id:
                index_to_delete = index
        data.pop(index_to_delete)
        return jsonify({"output": "device was deleted from the database"}), 200
    return jsonify({"error": "id not found"}), 404


# Changes a device configuration or adds a new configuration
@app.put("/api/devices/<device_id>")
def update_device(device_id):
    updated_device = request.json
    for i in range(len(data)):
        if device_id == data[i]["id"]:
            app.logger.info(f"Updating device {device_id}")
            for key, value in updated_device.items():
                app.logger.info(f"Setting parameter '{key}' to value '{value}'")
                data[i][key] = updated_device[key]
            return jsonify({'output': "Device updated successfully"}), 200
    return jsonify({'error': "Device not found"}), 404


# Sends a real time action to one of the devices.
# The request's JSON contains the parameters to update
# and their new values.
@app.post("/api/devices/<device_id>/action")
def rt_action(device_id):
    action = request.json
    for device in data:
        if device["id"] == device_id:
            app.logger.info(f"Device action {device_id}")
            for key, value in action.items():
                app.logger.info(f"Setting parameter '{key}' to value '{value}'")
                device["parameters"][key] = value

            # Formats and publishes the mqtt topic and payload -> the mqtt publisher
            room_topic = device['room'].lower().replace(" ", "-")
            topic = f"project/home/{room_topic}/{device['id']}/action"
            payload = json.dumps(action)
            mqtt.publish(topic, payload.encode(), qos=2)

            return jsonify({'output': "Action applied to device and published via MQTT"}), 200
    return jsonify({'error': "Device not found"}), 404


# Adds required headers to the response
@app.after_request
def add_header(response):
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
# POST /api/devices/<device_id>/action: Send a command to a device (requires action and optional parameters in JSON payload).
# Device Analytics:
# GET /api/devices/analytics: Fetch usage patterns and status trends for devices.

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5200, debug=True, use_reloader=False)
