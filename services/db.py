import config.env  # noqa: F401  # load_dotenv side effect
import os
import sys
import redis
import random
import logging.handlers
from time import sleep
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from pymongo.errors import ConnectionFailure, ConfigurationError, OperationFailure
from dotenv import load_dotenv

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

logger = logging.getLogger("smart-home.services.db")

# Load env vars from the shared constants file
load_dotenv("config/constants.env")

# How many times to attempt a connection request
RETRIES = 5

REDIS_PASS = os.getenv("REDIS_PASS")

MONGO_DB_CONNECTION_STRING = os.getenv("MONGO_DB_CONNECTION_STRING")
MONGO_USER = os.getenv("MONGO_USER")
MONGO_PASS = os.getenv("MONGO_PASS")

# Database parameters
uri = MONGO_DB_CONNECTION_STRING if MONGO_DB_CONNECTION_STRING is not None else (
    f"mongodb+srv://{MONGO_USER}:{MONGO_PASS}"
    f"@smart-home-devices.u2axxrl.mongodb.net/?retryWrites=true&w=majority&appName=smart-home-devices"
)

logger.info("Attempting to connect to Mongo database...")

try:
    mongo_client = MongoClient(uri, server_api=ServerApi('1'))
except ConfigurationError:
    logger.exception("Failed to connect to database. Shutting down.")
    sys.exit(1)

for attempt in range(RETRIES):
    try:
        mongo_client.admin.command('ping')
        break
    except (ConnectionFailure, OperationFailure):
        if attempt + 1 == RETRIES:
            logger.exception(f"Attempt {attempt + 1}/{RETRIES} failed. Shutting down.")
            sys.exit(1)
        delay = 2 ** attempt + random.random()
        logger.exception(f"Attempt {attempt + 1}/{RETRIES} failed. Retrying in {delay:.2f} seconds...")
        sleep(delay)

logger.info("Successfully connected to Mongo database. Attempting to connect to Redis database...")
db = mongo_client["smart-home-devices"]
devices_collection = db["devices"]

try:
    r = redis.Redis(
        host="redis-13476.c276.us-east-1-2.ec2.redns.redis-cloud.com",
        port=13476,
        decode_responses=True,
        username="default",
        password=REDIS_PASS,
    )
except redis.RedisError:
    logger.exception("Failed to initialize Redis client. Shutting down.")
    sys.exit(1)

for attempt in range(RETRIES):
    try:
        r.ping()
        break
    except redis.ConnectionError:
        if attempt + 1 == RETRIES:
            logger.exception(f"Attempt {attempt + 1}/{RETRIES} failed. Shutting down.")
            sys.exit(1)
        delay = 2 ** attempt + random.random()
        logger.exception(f"Attempt {attempt + 1}/{RETRIES} failed. Retrying in {delay:.2f} seconds...")
        sleep(delay)

logger.info("Success.")
