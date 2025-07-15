import config.env  # noqa: F401  # load_dotenv side effect
import os
import sys
import redis
import random
import logging.handlers
from time import sleep
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from pymongo.synchronous.collection import Collection
from pymongo.errors import ConnectionFailure, ConfigurationError, OperationFailure
from dotenv import load_dotenv
from typing import Any, Callable


class DatabaseNotInitializedError(Exception):
    """Raised when the database client is accessed before initialization."""
    pass


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

# Database parameters
REDIS_PASS = os.getenv("REDIS_PASS")

MONGO_DB_CONNECTION_STRING = os.getenv("MONGO_DB_CONNECTION_STRING")
MONGO_USER = os.getenv("MONGO_USER")
MONGO_PASS = os.getenv("MONGO_PASS")
URI = MONGO_DB_CONNECTION_STRING if MONGO_DB_CONNECTION_STRING is not None else (
    f"mongodb+srv://{MONGO_USER}:{MONGO_PASS}"
    f"@smart-home-devices.u2axxrl.mongodb.net/?retryWrites=true&w=majority&appName=smart-home-devices"
)

mongo_client: MongoClient | None = None
devices_collection: Collection | None = None
redis_client: redis.Redis | None = None


def retry_function(
        func: Callable[..., None],
        exceptions: type[BaseException] | tuple[type[BaseException], ...],
        retries: int = RETRIES,
        args: tuple[Any] = None,
        kwargs: dict[str, Any] = None,
) -> None:
    for attempt in range(retries):
        if args is None:
            args = tuple()
        if kwargs is None:
            kwargs = {}
        try:
            func(*args, **kwargs)
            break
        except exceptions:
            if attempt + 1 == retries:
                logger.exception(f"Attempt {attempt + 1}/{RETRIES} failed. Shutting down.")
                sys.exit(1)
            delay = 2 ** attempt + random.random()
            logger.exception(f"Attempt {attempt + 1}/{RETRIES} failed. Retrying in {delay:.2f} seconds...")
            sleep(delay)


def init_db() -> None:
    global mongo_client, devices_collection, redis_client
    logger.info("Attempting to connect to Mongo database...")

    try:
        mongo_client = MongoClient(URI, server_api=ServerApi('1'))
    except ConfigurationError:
        logger.exception("Failed to connect to database. Shutting down.")
        sys.exit(1)

    retry_function(mongo_client.admin.command, (ConnectionFailure, OperationFailure), args=('ping',))

    logger.info("Successfully connected to Mongo database. Attempting to connect to Redis database...")
    db = mongo_client["smart-home-devices"]
    devices_collection = db["devices"]

    try:
        redis_client = redis.Redis(
            host="redis-13476.c276.us-east-1-2.ec2.redns.redis-cloud.com",
            port=13476,
            decode_responses=True,
            username="default",
            password=REDIS_PASS,
        )
    except redis.RedisError:
        logger.exception("Failed to initialize Redis client. Shutting down.")
        sys.exit(1)

    retry_function(redis_client.ping, redis.ConnectionError)

    logger.info("Success.")


def id_exists(device_id: str) -> bool:
    """
    Check if a device ID exists in the Mongo database.

    :param str device_id: Device ID to check.
    :return: True if the device ID exists, False otherwise.
    :rtype: bool
    """
    device = devices_collection.find_one({"id": device_id}, {'_id': 0})
    return device is not None


def get_redis() -> redis.Redis:
    """
    Returns the Redis client if it was initialized.

    :return: Redis client if it was initialized.
    :rtype: redis.Redis

    :raises: DatabaseNotInitializedException if it was not initialized.
    """
    if redis_client is None:
        raise DatabaseNotInitializedError("Redis client is not initialized.")
    else:
        return redis_client


def get_mongo_client() -> MongoClient:
    """
    Returns the Mongo client if it was initialized.

    :return: Mongo client if it was initialized.
    :rtype: MongoClient

    :raises: DatabaseNotInitializedException if it was not initialized.
    """
    if mongo_client is None:
        raise DatabaseNotInitializedError("Mongo client is not initialized.")
    return mongo_client


def get_devices_collection() -> Collection:
    """
    Returns the collection of devices if the Mongo client was initialized.

    :return: Collection of devices if the Mongo client was initialized.
    :rtype: Collection

    :raises: DatabaseNotInitializedException if Mongo was not initialized.
    """
    if devices_collection is None:
        raise DatabaseNotInitializedError("Mongo client is not initialized.")
    return devices_collection
