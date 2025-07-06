import requests
import sys
import time
import os
import paho.mqtt.client as mqtt

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3001")
backend_url = os.getenv("BACKEND_URL", "http://localhost:5200")

data = {
    "id": "test-device",
    "type": "test_device",
    "name": "Test Device",
    "room": "Test",
    "status": "off",
    "parameters": {
        "test": 40
    }
}

api_test = False
frontend_test = False
simulator_test = False

### ---------- Test 1: API test ----------
response = requests.get(f"{backend_url}/api/devices")
if 199 < response.status_code < 400:
    print("API is responding")
else:
    print("API is not up")
    sys.exit(1)

# Add a new test device
requests.post(f"{backend_url}/api/devices", json=data)

# Check if the new device was added
response = requests.get(f"{backend_url}/api/devices")
output = response.json()
for device in output:
    if device["id"] == data["id"]:
        print("Test device added successfully")
        break
else:
    print("API is not functioning properly")
    sys.exit(1)

# delete Test Device
requests.delete(f"{backend_url}/api/devices/{data['id']}")
response = requests.get(f"{backend_url}/api/devices")
output = response.json()
for device in output:
    if device["id"] == data["id"]:
        print("API is not functioning properly")
        sys.exit(1)
else:
    print("Test device deleted successfully")
    api_test = True

### ---------- Test 2: Frontend ----------
response = requests.get(FRONTEND_URL)
if 199 < response.status_code < 400:
    print("Frontend is up")
    frontend_test = True
else:
    print("Frontend is not up")
    sys.exit(1)

### ---------- Test 3: Simulator MQTT ----------
mqtt_message_received = False

def on_message(client, userdata, msg):
    global mqtt_message_received
    print(f"MQTT message received on topic: {msg.topic}")
    mqtt_message_received = True
    client.disconnect()  # Stop loop after receiving

client = mqtt.Client()
client.on_message = on_message
client.connect("test.mosquitto.org", 1883, 60)
client.subscribe("project/home/#")
client.loop_start()

print("Waiting up to 10 seconds for simulator MQTT message...")

# Wait up to 30 seconds for message
for _ in range(30): 
    if mqtt_message_received:
        break
    time.sleep(1)

client.loop_stop()

if mqtt_message_received:
    print("Simulator is publishing MQTT messages")
    simulator_test = True
else:
    print("Simulator did not publish MQTT messages")
    sys.exit(1)

### ---------- Final result ----------
if api_test and frontend_test and simulator_test:
    print("All 3 tests have gone through successfully")
    sys.exit(0)