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
response = requests.get("http://localhost:5200/api/devices")
if 199 > response.status_code > 400:
    print("API is responding")
else:
    print("API is not up")
    sys.exit(1)

# Add a new test device
requests.post("http://localhost:5200/api/devices", json = data)

# Check if the new device was added
response = requests.get("http://localhost:5200/api/devices")
