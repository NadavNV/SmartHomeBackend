import config.env  # noqa: F401  # load_dotenv side effect
import logging.handlers
import os
import json
import paho.mqtt.client as paho
from paho.mqtt.properties import Properties
from paho.mqtt.packettypes import PacketTypes
from typing import Any, cast, Mapping

from monitoring.metrics import update_device_status, update_device_metrics
from services.db import get_devices_collection, id_exists
from validation.validators import validate_device_data

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

logger = logging.getLogger("smart-home.services.mqtt")


class MQTTNotInitializedError(Exception):
    """Raised when the MQTT client is accessed before initialization."""
    pass


# Setting up the MQTT client
BROKER_HOST = os.getenv("BROKER_HOST", "test.mosquitto.org")
BROKER_PORT = int(os.getenv("BROKER_PORT", 1883))

client_id = f"flask-backend-{os.getenv('HOSTNAME')}"
mqtt: paho.Client | None = None


def on_connect(client: paho.Client, _userdata, _connect_flags, reason_code, _properties) -> None:
    """
    Function to run after the MQTT client finishes connecting to the broker. Logs the
    connection and subscribes to the project topic.

    :param paho.Client client: The MQTT client instance for this callback.
    :param _userdata: Unused by this function.
    :param _connect_flags: Unused by this function.
    :param reason_code: The connection reason code received from the broker.
    :param _properties: Unused by this function.
    :return: None
    :rtype: None
    """
    logger.info(f'CONNACK received with code {reason_code}.')
    if reason_code == 0:
        logger.info("Connected successfully")
        client.subscribe("$share/backend/nadavnv-smart-home/devices/#")
    else:
        logger.error(f"Connection failed with code {reason_code}")


def on_disconnect(_client, _userdata, _disconnect_flags, reason_code, _properties=None) -> None:
    """
    Function to run after the MQTT client disconnects.

    :param _client: Unused by this function.
    :param _userdata: Unused by this function.
    :param _disconnect_flags: Unused by this function.
    :param reason_code: The disconnection reason code possibly received from the broker.
    :param _properties: Unused by this function.
    :return: None
    :rtype: None
    """
    logger.warning(f"Disconnected from broker with reason: {reason_code}")


def on_message(_mqtt_client, _userdata, msg: paho.MQTTMessage) -> None:
    """
    Receives the published MQTT payloads and updates the database accordingly.

    Validates the device data and updates metrics and the database if it's valid.

    :param _mqtt_client: Unused by this function.
    :param _userdata: Unused by this function.
    :param paho.MQTTMessage msg: The MQTT message received from the broker.
    :return: None
    :rtype: None
    """
    sender_id, sender_group = None, None
    props = msg.properties
    user_props = getattr(props, "UserProperty", None)
    if user_props is not None:
        sender_id = dict(user_props).get("sender_id")
        sender_group = dict(user_props).get("sender_group")
    if sender_id is None:
        logger.error("Message missing sender")
    if sender_group is None:
        logger.error("Message missing sender group")
    if sender_id == client_id or sender_group == "backend":
        # Ignore backend messages
        return

    logger.info(f"MQTT Message Received on {msg.topic}")
    payload = cast(bytes, msg.payload)
    try:
        payload = json.loads(payload.decode("utf-8"))
    except UnicodeDecodeError as e:
        logger.exception(f"Error decoding payload: {e.reason}")
        return
    payload = payload["contents"]
    # Extract device_id from topic: expected format nadavnv-smart-home/devices/<device_id>/<method>
    topic_parts = msg.topic.split('/')
    if len(topic_parts) == 4:
        device_id = topic_parts[2]
        method = topic_parts[-1]
        device = get_devices_collection().find_one({"id": device_id}, {"_id": 0})
        if device is None:
            logger.error(f"Device ID {device_id} not found")
            return
        match method:
            case "update":
                # Update an existing device
                if "id" in payload and payload["id"] != device_id:
                    logger.error(f"ID mismatch: ID in URL: {device_id}, ID in payload: {payload['id']}")
                    return
                success, reasons = validate_device_data(payload, device_type=device["type"])
                if success:
                    update_device(device, payload)
                    return
            case "post":
                # Add a new device to the database
                if "id" in payload and payload["id"] != device_id:
                    logger.error(f"ID mismatch: ID in URL: {device_id}, ID in payload: {payload['id']}")
                    return
                success, reason = validate_device_data(payload, new_device=True)
                if success:
                    if id_exists(device_id):
                        logger.error(f"ID {device_id} already exists")
                        return
                    get_devices_collection().insert_one(payload)
                    logger.info("Device added successfully")
                    return
                else:
                    return
            case "delete":
                # Remove a device from the database
                if id_exists(device_id):
                    if device["status"] == "on":
                        # Calculate device usage, etc.
                        update_device_status(device, "off")
                    get_devices_collection().delete_one({"id": device_id})
                    logger.info("Device deleted successfully")
                    return
                logger.error(f"ID {device_id} not found")
                return
            case _:
                logger.error(f"Unknown method: {method}")
    else:
        logger.error(f"Incorrect topic {msg.topic}")


def update_device(old_device: Mapping[str, Any], updated_device: Mapping[str, Any]) -> None:
    """
    Updates a device to a new configuration in the Mongo database and in the Prometheus metrics.

    :param Mapping[str, Any] old_device: Previous device configuration
    :param Mapping[str, Any] updated_device: New device configuration
    :return: None
    :rtype: None
    """
    update_device_metrics(old_device, updated_device)
    update_fields = {}
    for key, value in updated_device.items():
        if key != "parameters":
            update_fields[key] = value
        else:
            for param, param_value in updated_device[key].items():
                update_fields[f"parameters.{param}"] = param_value
    # Find device by id and update the fields with 'set'
    get_devices_collection().update_one(
        {"id": old_device["id"]},
        {"$set": update_fields}
    )


def publish_mqtt(contents: dict[str, Any], device_id: str, method: str) -> None:
    """
    Publishes the MQTT message to the broker.

    Formats the message and adds appropriate sender id and sender group tags.

    :param dict[str, Any] contents: The message to be published.
    :param str device_id: ID of the device the message is about.
    :param str method: The method to attach to the topic, either "update", "post" or "delete".
    :return: None
    :rtype: None
    """
    topic = f"nadavnv-smart-home/devices/{device_id}/{method}"
    contents.pop("_id", None)  # Make sure the contents are serializable
    payload = json.dumps({
        "contents": contents,
    })
    properties = Properties(PacketTypes.PUBLISH)
    properties.UserProperty = [("sender_id", client_id), ("sender_group", "backend")]
    mqtt.publish(topic, payload.encode("utf-8"), qos=2, properties=properties)


def init_mqtt() -> None:
    """
    Initialize the MQTT broker.

    :return: None
    :rtype: None
    """
    global mqtt
    mqtt = paho.Client(paho.CallbackAPIVersion.VERSION2, protocol=paho.MQTTv5, client_id=client_id)
    mqtt.on_connect = on_connect
    mqtt.on_disconnect = on_disconnect
    mqtt.on_message = on_message

    logger.info(f"Connecting to MQTT broker {BROKER_HOST}:{BROKER_PORT}...")

    mqtt.connect_async(BROKER_HOST, BROKER_PORT)
    mqtt.loop_start()


def get_mqtt() -> paho.Client:
    """
    Returns the MQTT client if available.

    :return: MQTT client
    :rtype: paho.Client

    :raises: MQTTNotInitializedError If the MQTT client is not initialized.
    """
    if mqtt is None:
        raise MQTTNotInitializedError()
    return mqtt
