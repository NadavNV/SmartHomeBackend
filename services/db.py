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
from urllib.parse import urlparse


class DatabaseNotInitializedError(Exception):
    """Raised when the database client is accessed before initialization."""
    pass


logger = logging.getLogger("smart-home.services.db")

# Load env vars from the shared constants file
load_dotenv("config/constants.env")

# How many times to attempt a connection request
RETRIES = 5

# Database parameters
REDIS_HOST = os.getenv("REDIS_HOST", "redis-13476.c276.us-east-1-2.ec2.redns.redis-cloud.com")
REDIS_PORT = int(os.getenv("REDIS_PORT", 13476))
REDIS_USER = os.getenv("REDIS_USER", "default")
REDIS_PASS = os.getenv("REDIS_PASS")

MONGO_DB_CONNECTION_STRING = os.getenv("MONGO_DB_CONNECTION_STRING",
                                       "mongodb+srv://@smart-home-devices.u2axxrl."
                                       "mongodb.net/?retryWrites=true&w=majority&appName"
                                       "=smart-home-devices")
MONGO_USER = os.getenv("MONGO_USER")
MONGO_PASS = os.getenv("MONGO_PASS")

mongo_client: MongoClient | None = None
devices_collection: Collection | None = None
redis_client: redis.Redis | None = None


def retry_function(
        func: Callable[..., Any],
        exceptions: type[BaseException] | tuple[type[BaseException], ...],
        retries: int = RETRIES,
        args: tuple[Any] = None,
        kwargs: dict[str, Any] = None,
) -> None:
    """
    Retries to run a given function a certain number of times, until success or
    enough retries have failed. Exits with error code 1 on repeated failures.

    The return value of the function is not checked. After each failure there is
    an exponential delay with jitter. Must provide an exception
    type or a tuple of exception types that are expected on failure. Can
    optionally provide positional and keyword arguments to pass to the function.

    :param func: The function to execute.
    :type func: Callable[..., Any]
    :param exceptions: The expected exception types.
    :type exceptions: type[BaseException] | tuple[type[BaseException], ...]
    :param retries: How many times to retry the function.
    :type retries: int
    :param args: Positional arguments to pass to the function.
    :type args: tuple[Any]
    :param kwargs: Keyword arguments to pass to the function.
    :type kwargs: dict[str, Any]
    :return: None
    :rtype: None
    """
    # Can't use mutable default arguments
    if args is None:
        args = tuple()
    if kwargs is None:
        kwargs = {}
    for attempt in range(retries):
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
    """
    Initialize the Mongo database and connect to Redis.
    :return: None
    :rtype: None
    """
    global mongo_client, devices_collection, redis_client
    logger.info("Attempting to connect to Mongo database...")

    try:
        # Parse the URI to see if credentials are already included
        parsed = urlparse(MONGO_DB_CONNECTION_STRING)

        # If the netloc does NOT contain username:password, add them via MongoClient params
        if parsed.username is None and parsed.password is None and MONGO_USER and MONGO_PASS:
            mongo_client = MongoClient(MONGO_DB_CONNECTION_STRING, username=MONGO_USER, password=MONGO_PASS,
                                       server_api=ServerApi('1'))
        else:
            mongo_client = MongoClient(MONGO_DB_CONNECTION_STRING, server_api=ServerApi('1'))
    except ConfigurationError:
        logger.exception("Failed to connect to database. Shutting down.")
        sys.exit(1)

    retry_function(mongo_client.admin.command, (ConnectionFailure, OperationFailure), args=('ping',))

    logger.info("Successfully connected to Mongo database. Attempting to connect to Redis database...")
    db = mongo_client["smart-home-devices"]
    devices_collection = db["devices"]

    try:
        redis_client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            decode_responses=True,
            username=REDIS_USER,
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
