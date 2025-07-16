import os
import unittest
import random
import mongomock
import fakeredis
import json
from unittest import TestCase
from unittest.mock import MagicMock, patch, call
from paho.mqtt.properties import Properties
from paho.mqtt.packettypes import PacketTypes
from copy import deepcopy

from services.mqtt import on_connect, on_disconnect, on_message, update_device, publish_mqtt
from validation.validators import (
    MIN_WATER_TEMP, MAX_WATER_TEMP,
)

VALID_TOPIC = f"nadavnv-smart-home/devices/"
CLIENT_ID = f"flask-backend-{os.getenv('HOSTNAME')}"


def fake_mqtt_client(*_args, **_kwargs):
    mock_client = MagicMock()
    mock_client.connect_async.return_value = None
    mock_client.disconnect.return_value = None
    mock_client.loop_start.return_value = None
    mock_client.loop_stop.return_value = None
    mock_client.subscribe.return_value = 0
    mock_client.publish.return_value = None
    return mock_client


class TestMQTTCallbacks(TestCase):

    def setUp(self):
        # Patch the loggers
        self.mock_logger = MagicMock()
        self.db_logger_patcher = patch('services.db.logger', self.mock_logger)
        self.mqtt_logger_patcher = patch('services.mqtt.logger', self.mock_logger)
        self.validators_logger_patcher = patch('validation.validators.logger', self.mock_logger)
        self.metrics_logger_patcher = patch('monitoring.metrics.logger', self.mock_logger)
        self.db_logger_patcher.start()
        self.mqtt_logger_patcher.start()
        self.validators_logger_patcher.start()
        self.metrics_logger_patcher.start()

        self.mock_mongo_client = mongomock.MongoClient()
        self.mock_redis = fakeredis.FakeRedis()
        self.mock_collection = self.mock_mongo_client.smarthome.devices
        self.mock_mqtt_client = fake_mqtt_client()

        self.mark_device_read_patcher = patch('routes.mark_device_read', MagicMock())
        self.mongo_client_constructor_patch = patch('services.db.MongoClient', return_value=self.mock_mongo_client)
        self.redis_constructor_patch = patch('services.db.redis.Redis', return_value=self.mock_redis)
        self.routes_get_devices_patch = patch('routes.get_devices_collection', return_value=self.mock_collection)
        self.mqtt_get_devices_patch = patch('services.mqtt.get_devices_collection', return_value=self.mock_collection)
        self.get_redis_patch = patch('routes.get_redis', return_value=self.mock_redis)
        self.get_mongo_client_patch = patch('routes.get_mongo_client', return_value=self.mock_mongo_client)
        self.routes_id_exists_patch = patch('routes.id_exists',
                                            lambda device_id: self.mock_collection.find_one({"id": device_id},
                                                                                            {'_id': 0}))
        self.mqtt_id_exists_patch = patch('services.mqtt.id_exists',
                                          lambda device_id: self.mock_collection.find_one({"id": device_id},
                                                                                          {'_id': 0}))
        self.mark_device_read = self.mark_device_read_patcher.start()
        self.mongo_client_constructor_patch.start()
        self.redis_constructor_patch.start()
        self.routes_get_devices_patch.start()
        self.mqtt_get_devices_patch.start()
        self.get_redis_patch.start()
        self.get_mongo_client_patch.start()
        self.routes_id_exists_patch.start()
        self.mqtt_id_exists_patch.start()

        self.valid_water_heater = {
            "id": "main-water-heater",
            "type": "water_heater",
            "name": "Main Water Heater",
            "room": "Main Bath",
            "status": "off",
            "parameters": {
                "temperature": 40,
                "target_temperature": (MIN_WATER_TEMP + MAX_WATER_TEMP) / 2,
                "is_heating": False,
                "timer_enabled": True,
                "scheduled_on": "06:30",
                "scheduled_off": "08:00"
            }
        }

    def tearDown(self):
        self.db_logger_patcher.stop()
        self.mqtt_logger_patcher.stop()
        self.validators_logger_patcher.stop()
        self.metrics_logger_patcher.stop()

        self.mark_device_read = self.mark_device_read_patcher.stop()
        self.mongo_client_constructor_patch.stop()
        self.redis_constructor_patch.stop()
        self.routes_get_devices_patch.stop()
        self.mqtt_get_devices_patch.stop()
        self.get_redis_patch.stop()
        self.get_mongo_client_patch.stop()
        self.routes_id_exists_patch.stop()
        self.mqtt_id_exists_patch.stop()

    def test_on_connect_success(self):
        client = MagicMock()
        on_connect(client, None, {}, 0)
        calls = [call('CONNACK received with code 0.'), call("Connected successfully")]
        self.mock_logger.info.assert_has_calls(calls, any_order=False)
        client.subscribe.assert_called_with("$share/backend/nadavnv-smart-home/devices/#")

    def test_on_connect_failure(self):
        client = MagicMock()
        on_connect(client=client, _userdata=None, _connect_flags={}, reason_code=1)
        calls = [call('CONNACK received with code 1.')]
        self.mock_logger.info.assert_has_calls(calls, any_order=False)
        client.subscribe.assert_not_called()

    def test_on_disconnect_random(self):
        reason_code = random.randint(0, 23)
        on_disconnect(None, None, None, reason_code)
        self.mock_logger.warning.assert_called_with(f"Disconnected from broker with reason: {reason_code}")

    def test_on_message_invalid_method(self):
        fake_msg = MagicMock()
        fake_msg.payload = json.dumps(deepcopy(self.valid_water_heater)).encode()
        fake_msg.topic = VALID_TOPIC + f"{self.valid_water_heater["id"]}/steve"

        on_message(None, None, fake_msg)
        self.mock_logger.error.assert_called_with(f"Unknown method: steve")
        self.mock_logger.info.assert_called_with(f"MQTT Message Received on {fake_msg.topic}")

    def test_on_message_invalid_topic(self):
        fake_msg = MagicMock()
        fake_msg.payload = json.dumps(deepcopy(self.valid_water_heater)).encode()
        fake_msg.topic = VALID_TOPIC + f"{self.valid_water_heater["id"]}/post/extra"

        on_message(None, None, fake_msg)
        self.mock_logger.error.assert_called_with(f"Incorrect topic {fake_msg.topic}")
        self.mock_logger.info.assert_called_with(f"MQTT Message Received on {fake_msg.topic}")

    def test_on_message_valid_post_no_props(self):
        fake_msg = MagicMock()
        fake_msg.payload = json.dumps(deepcopy(self.valid_water_heater)).encode()
        fake_msg.topic = VALID_TOPIC + f"{self.valid_water_heater["id"]}/post"

        on_message(None, None, fake_msg)
        error_calls = [call("Message missing sender"), call("Message missing sender group")]
        info_calls = [
            call(f"MQTT Message Received on {fake_msg.topic}"),
            call("Device added successfully"),
        ]
        self.mock_logger.info.assert_has_calls(info_calls)
        self.mock_logger.error.assert_has_calls(error_calls)
        self.assertEqual(self.valid_water_heater,
                         self.mock_collection.find_one({"id": self.valid_water_heater["id"]}, {"_id": 0}))

    def test_on_message_valid_post_backend_group(self):
        props = Properties(PacketTypes.PUBLISH)
        props.UserProperty = [('sender_group', 'backend')]

        fake_msg = MagicMock()
        fake_msg.payload = json.dumps(deepcopy(self.valid_water_heater)).encode()
        fake_msg.topic = VALID_TOPIC + f"{self.valid_water_heater["id"]}/post"
        fake_msg.properties = props

        on_message(None, None, fake_msg)
        self.mock_logger.error.assert_called_with("Message missing sender")
        self.mock_logger.info.assert_not_called()

    def test_on_message_invalid_ignored(self):
        props = Properties(PacketTypes.PUBLISH)
        props.UserProperty = [('sender_id', CLIENT_ID), ('sender_group', 'simulator')]

        fake_msg = MagicMock()
        fake_msg.payload = json.dumps(deepcopy(self.valid_water_heater)).encode()
        fake_msg.topic = VALID_TOPIC + f"{self.valid_water_heater["id"]}/post"
        fake_msg.properties = props

        on_message(None, None, fake_msg)
        self.mock_logger.info.assert_not_called()

    def test_on_message_invalid_post_id_exists(self):
        self.mock_collection.insert_one(deepcopy(self.valid_water_heater))
        props = Properties(PacketTypes.PUBLISH)
        props.UserProperty = [('sender_id', CLIENT_ID + "2"), ('sender_group', 'simulator')]

        fake_msg = MagicMock()
        fake_msg.payload = json.dumps(deepcopy(self.valid_water_heater)).encode()
        fake_msg.topic = VALID_TOPIC + f"{self.valid_water_heater["id"]}/post"
        fake_msg.properties = props

        on_message(None, None, fake_msg)
        self.mock_logger.error.assert_called_with(f"ID {self.valid_water_heater["id"]} already exists")

    def test_on_message_invalid_post_id_mismatch(self):
        props = Properties(PacketTypes.PUBLISH)
        props.UserProperty = [('sender_id', CLIENT_ID + "2"), ('sender_group', 'simulator')]

        fake_msg = MagicMock()
        fake_msg.payload = json.dumps(deepcopy(self.valid_water_heater)).encode()
        fake_msg.topic = VALID_TOPIC + f"steve/post"
        fake_msg.properties = props

        on_message(None, None, fake_msg)
        self.mock_logger.error.assert_called_with(
            f"ID mismatch: ID in URL: steve, ID in payload: {self.valid_water_heater["id"]}"
        )

    def test_on_message_invalid_post_bad_data(self):
        props = Properties(PacketTypes.PUBLISH)
        props.UserProperty = [('sender_id', CLIENT_ID + "2"), ('sender_group', 'simulator')]

        fake_msg = MagicMock()
        device = deepcopy(self.valid_water_heater)
        device["status"] = "steve"
        fake_msg.payload = json.dumps(device).encode()
        fake_msg.topic = VALID_TOPIC + f"{device["id"]}/post"
        fake_msg.properties = props

        on_message(None, None, fake_msg)
        reasons = [f"'{device["status"]}' is not a valid value for 'status'. Must be one of { {'on', 'off'} }."]
        self.mock_logger.error.assert_called_with(
            f"Validation failed, reasons: {reasons}"
        )

    def test_on_message_valid_update(self):
        self.mock_collection.insert_one(deepcopy(self.valid_water_heater))
        props = Properties(PacketTypes.PUBLISH)
        props.UserProperty = [('sender_id', CLIENT_ID + "2"), ('sender_group', 'simulator')]

        fake_msg = MagicMock()
        device = deepcopy(self.valid_water_heater)
        device["parameters"]["temperature"] = (MIN_WATER_TEMP + MAX_WATER_TEMP) / 2
        fake_msg.payload = json.dumps({"parameters": {"temperature": (MIN_WATER_TEMP + MAX_WATER_TEMP) / 2}}).encode()
        fake_msg.topic = VALID_TOPIC + f"{device["id"]}/update"
        fake_msg.properties = props

        on_message(None, None, fake_msg)
        calls = [
            call(f"MQTT Message Received on {fake_msg.topic}"),
            call(f"Setting parameter 'temperature' to value '{(MIN_WATER_TEMP + MAX_WATER_TEMP) / 2}'"),
            call("Device updated successfully"),
        ]
        self.mock_logger.info.assert_has_calls(calls)
        self.assertEqual(device, self.mock_collection.find_one({"id": device["id"]}, {"_id": 0}))

    def test_on_message_invalid_update_missing_id(self):
        props = Properties(PacketTypes.PUBLISH)
        props.UserProperty = [('sender_id', CLIENT_ID + "2"), ('sender_group', 'simulator')]

        fake_msg = MagicMock()
        device_id = self.valid_water_heater
        fake_msg.payload = json.dumps({"parameters": {"temperature": (MIN_WATER_TEMP + MAX_WATER_TEMP) / 2}}).encode()
        fake_msg.topic = VALID_TOPIC + f"{device_id}/update"
        fake_msg.properties = props

        on_message(None, None, fake_msg)
        self.mock_logger.info.assert_called_with(f"MQTT Message Received on {fake_msg.topic}")
        self.mock_logger.error.assert_called_with(f"Device ID {device_id} not found")

    def test_on_message_invalid_update_bad_data(self):
        self.mock_collection.insert_one(deepcopy(self.valid_water_heater))
        props = Properties(PacketTypes.PUBLISH)
        props.UserProperty = [('sender_id', CLIENT_ID + "2"), ('sender_group', 'simulator')]

        fake_msg = MagicMock()
        fake_msg.payload = json.dumps({"parameters": {"target_temperature": MAX_WATER_TEMP + 1}}).encode()
        fake_msg.topic = VALID_TOPIC + f"{self.valid_water_heater["id"]}/update"
        fake_msg.properties = props

        on_message(None, None, fake_msg)
        self.mock_logger.info.assert_called_with(f"MQTT Message Received on {fake_msg.topic}")
        reasons = [f"'target_temperature' must be between {MIN_WATER_TEMP} and"
                   f" {MAX_WATER_TEMP}, got {MAX_WATER_TEMP + 1} instead."]
        self.mock_logger.error.assert_called_with(f"Validation failed, reasons: {reasons}")

    def test_on_message_valid_delete(self):
        self.mock_collection.insert_one(deepcopy(self.valid_water_heater))
        props = Properties(PacketTypes.PUBLISH)
        props.UserProperty = [('sender_id', CLIENT_ID + "2"), ('sender_group', 'simulator')]

        fake_msg = MagicMock()
        device_id = self.valid_water_heater["id"]
        fake_msg.payload = json.dumps({}).encode()
        fake_msg.topic = VALID_TOPIC + f"{device_id}/delete"
        fake_msg.properties = props

        on_message(None, None, fake_msg)
        calls = [call(f"MQTT Message Received on {fake_msg.topic}"), call("Device deleted successfully")]
        self.mock_logger.info.assert_has_calls(calls)
        self.assertEqual(None, self.mock_collection.find_one({"id": device_id}))

    def test_on_message_invalid_delete_missing_id(self):
        props = Properties(PacketTypes.PUBLISH)
        props.UserProperty = [('sender_id', CLIENT_ID + "2"), ('sender_group', 'simulator')]

        fake_msg = MagicMock()
        device_id = self.valid_water_heater["id"]
        fake_msg.payload = json.dumps({}).encode()
        fake_msg.topic = VALID_TOPIC + f"{device_id}/delete"
        fake_msg.properties = props

        on_message(None, None, fake_msg)
        self.mock_logger.info.assert_called_with(f"MQTT Message Received on {fake_msg.topic}")
        self.mock_logger.error.assert_called_with(f"Device ID {device_id} not found")


if __name__ == "__main__":
    unittest.main()
