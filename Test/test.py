import requests
import sys

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

# Get current devices list
response = requests.get("http://test-container:5200/api/devices")
if 199 < response.status_code < 400:
    print("API is responding")
else:
    print("API is not up")
    sys.exit(1)

# Add a new test device
requests.post("http://test-container:5200/api/devices", json = data)

# Check if the new device was added
response = requests.get("http://test-container:5200/api/devices")
output = response.json()
for device in output:
    if device["id"] == data["id"]:
        print("Test device added successfully")
        # print(response.json())
        break
else:
    print("API is not functioning properly")
    sys.exit(1)

# delete Test Device
requests.delete(f"http://test-container:5200/api/devices/{data['id']}")

# Check if the test device was deleted
response = requests.get("http://test-container:5200/api/devices")
output = response.json()
for device in output:
    if device["id"] == data["id"]:
        print("API is not functioning properly")
        sys.exit(1)
        break
else:
    print("Test device deleted successfully")
    sys.exit(0)

# test connection to frontend
response = requests.get("https://nadavnv.github.io/SmartHomeDashboard/")
if 199 < response.status_code < 400:
    print("Frontend is up")
else:
    print("Frontend is not down")
    sys.exit(1)