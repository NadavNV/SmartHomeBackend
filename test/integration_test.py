import requests
import sys
import time
import os
import paho.mqtt.client as paho
from services.mqtt import MQTT_TOPIC

FRONTEND_URL = os.getenv("FRONTEND_URL")
BACKEND_URL = os.getenv("BACKEND_URL")
GRAFANA_URL = os.getenv("GRAFANA_URL")

DATA = {
    "id": "test-device",
    "type": "light",
    "name": "Test Device",
    "room": "Test",
    "status": "off",
    "parameters": {
        "is_dimmable": False,
        "dynamic_color": False,
    }
}

error_list = []

api_test = False
frontend_test = False
simulator_test = False
prom_test = False
grafana_test = False
tests = 0
total_test_num = 4


def run_api_test(backend_url, data):
    res = requests.get(f"{backend_url}/api/devices")
    if 199 < res.status_code < 400:
        print("API is responding")
    else:
        print("API is not up")
        return False

    # Add a new test device
    requests.post(f"{backend_url}/api/devices", json=data)

    # Check if the new device was added
    res = requests.get(f"{backend_url}/api/devices")
    output = res.json()
    for device in output:
        if device["id"] == data["id"]:
            print("Test device added successfully")
            break
    else:
        print("Test device was not added properly")
        return False

    # delete Test Device
    requests.delete(f"{backend_url}/api/devices/{data['id']}")
    res = requests.get(f"{backend_url}/api/devices")
    output = res.json()
    for device in output:
        if device["id"] == data["id"]:
            print("Test device was not deleted")
            return False
    else:
        print("Test device deleted successfully")
        return True


# ---------- Test 1: API test ----------
if run_api_test(BACKEND_URL, DATA):
    api_test = True
    tests += 1
else:
    error_list.append("API Backend")

# ---------- Test 2: Frontend ----------
response = requests.get(FRONTEND_URL)
if 199 < response.status_code < 400:
    print("Frontend is up")
    frontend_test = True
    tests += 1
else:
    print("Frontend is not up")
    error_list.append("Frontend")

# ---------- Test 3: MQTT Simulator----------
mqtt_message_received = False


def on_message(mqtt_client: paho.Client, _userdata, msg: paho.MQTTMessage):
    global mqtt_message_received
    print(f"MQTT message received on topic: {msg.topic}")
    mqtt_message_received = True
    mqtt_client.disconnect()  # Stop loop after receiving


client = paho.Client(paho.CallbackAPIVersion.VERSION2, protocol=paho.MQTTv5)
client.on_message = on_message
client.connect("mqtt-broker", 1883, 60)
client.subscribe(f"{MQTT_TOPIC}/#")
client.loop_start()

print("Waiting up to 30 seconds for simulator MQTT message...")

# Wait up to 30 seconds for message
for _ in range(30):
    if mqtt_message_received:
        break
    time.sleep(1)

client.loop_stop()

if mqtt_message_received:
    print("Simulator is publishing MQTT messages")
    simulator_test = True
    tests += 1
else:
    print("Simulator did not publish MQTT messages")
    error_list.append("Simulator")

# ---------- Test 4: Grafana ----------
response = requests.get(f"{GRAFANA_URL}/api/health")
if 199 < response.status_code < 400:
    print("Grafana is healthy")
    grafana_test = True
    tests += 1
else:
    print("Grafana is unhealthy")
    error_list.append("Grafana")

# ---------- Final result ----------
print(f"{tests}/{total_test_num} tests have gone through successfully")
if not error_list:
    sys.exit(0)
else:
    print("The following tests have failed")
    print(error_list)
    sys.exit(1)
