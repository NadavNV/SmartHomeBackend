import json
import os
from pymongo import MongoClient

MONGO_DB_CONNECTION_STRING = os.getenv("MONGO_DB_CONNECTION_STRING")
MONGO_USER = os.getenv("MONGO_USER")
MONGO_PASS = os.getenv("MONGO_PASS")

uri = MONGO_DB_CONNECTION_STRING
if MONGO_USER is not None:
    uri = uri.replace("{MONGO_USER}", MONGO_USER)
if MONGO_PASS is not None:
    uri = uri.replace("{MONGO_PASS}", MONGO_PASS)

# Connect to the client
client = MongoClient(uri)

# Choose your database and collection
db = client["smart-home-devices"]
collection = db["devices"]

# Load the JSON file (array of devices)
with open("devices.json", "r") as f:
    devices = json.load(f)

# Insert each document (or use insert_many)
result = collection.insert_many(devices)

# Confirm insertion
print(f"Inserted {len(result.inserted_ids)} documents.")
