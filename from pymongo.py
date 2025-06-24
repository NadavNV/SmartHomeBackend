from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi

# שלב 1: התחברות
uri = "mongodb+srv://yaniv3109:njU9dRJwFss3UjhW@smart-home-db.w9dsqtr.mongodb.net/?retryWrites=true&w=majority&appName=smart-home-db"
client = MongoClient(uri, server_api=ServerApi('1'))

# שלב 2: בחירת מסד נתונים וקולקציה
db = client["smart_home"]  # שם הדאטאבייס
devices_collection = db["devices"]  # שם הקולקציה

# שלב 3: הגדרת הנתונים (אפשר גם לטעון מ־JSON חיצוני)
devices = [
    {
      "id": "main-water-heater",
      "type": "water_heater",
      "name": "Main Water Heater",
      "room": "Main Bath",
      "status": "off",
      "parameters": {
        "temperature": 40,
        "target_temperature": 55,
        "is_heating": False,
        "timer_enabled": True,
        "scheduled_on": "06:30",
        "scheduled_off": "08:00"
      }
    },
    {
      "id": "living-room-light",
      "type": "light",
      "name": "Living Room Light",
      "room": "Living Room",
      "status": "on",
      "parameters": {
        "brightness": 80,
        "color": "#FFFFFF",
        "is_dimmable": False,
        "dynamic_color": True
      }
    },
    {
      "id": "bedroom-ac",
      "type": "air_conditioner",
      "name": "Bedroom AC",
      "room": "Bedroom",
      "status": "on",
      "parameters": {
        "temperature": 22,
        "mode": "cool",
        "fan_speed": "medium",
        "swing": "auto"
      }
    },
    {
      "id": "front-door-lock",
      "type": "door_lock",
      "room": "General",
      "name": "Front Door Lock",
      "status": "locked",
      "parameters": {
        "auto_lock_enabled": True,
        "battery_level": 78
      }
    },
    {
      "id": "living-room-curtains",
      "type": "curtain",
      "room": "Living Room",
      "name": "Living Room Curtains",
      "status": "closed",
      "parameters": {
        "position": 0
      }
    },
    {
      "id": "kitchen-light",
      "type": "light",
      "room": "Kitchen",
      "name": "Kitchen Light",
      "status": "off",
      "parameters": {
        "brightness": 80,
        "color": "#FFDF8E",
        "is_dimmable": True,
        "dynamic_color": False
      }
    }
]

# שלב 4: הכנסת הנתונים למסד
result = devices_collection.insert_many(devices)
print("Inserted IDs:", result.inserted_ids)