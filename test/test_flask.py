import unittest
from unittest import TestCase
from unittest.mock import MagicMock, patch, call
import mongomock
import fakeredis
from copy import deepcopy
from main import create_app

from validation.validators import (
    MIN_WATER_TEMP, MAX_WATER_TEMP
)


def fake_mqtt_client(*_args, **_kwargs):
    mock_client = MagicMock()
    mock_client.connect_async.return_value = None
    mock_client.disconnect.return_value = None
    mock_client.loop_start.return_value = None
    mock_client.loop_stop.return_value = None
    mock_client.subscribe.return_value = 0
    msg_info = MagicMock()
    msg_info.rc = 0
    mock_client.publish.return_value = msg_info

    return mock_client


class FlaskTest(TestCase):

    def setUp(self):
        # Patch the loggers
        self.mock_logger = MagicMock()
        self.db_logger_patcher = patch('services.db.logger', self.mock_logger)
        self.mqtt_logger_patcher = patch('services.mqtt.logger', self.mock_logger)
        self.smart_home_logger_patcher = patch('main.smart_home_logger', self.mock_logger)
        self.validators_logger_patcher = patch('validation.validators.logger', self.mock_logger)
        self.metrics_logger_patcher = patch('monitoring.metrics.logger', self.mock_logger)

        self.mark_device_read_patcher = patch('routes.mark_device_read', MagicMock())
        self.mock_mqtt_client = fake_mqtt_client()
        self.mock_mongo_client = mongomock.MongoClient()
        self.mock_redis = fakeredis.FakeRedis()
        self.mock_collection = self.mock_mongo_client.smarthome.devices

        self.mongo_client_constructor_patch = patch('services.db.MongoClient', return_value=self.mock_mongo_client)
        self.redis_constructor_patch = patch('services.db.redis.Redis', return_value=self.mock_redis)
        self.mqtt_constructor_patch = patch('services.mqtt.paho.Client', return_value=self.mock_mqtt_client)
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
        self.mqtt_constructor_patch.start()
        self.routes_get_devices_patch.start()
        self.mqtt_get_devices_patch.start()
        self.get_redis_patch.start()
        self.get_mongo_client_patch.start()
        self.routes_id_exists_patch.start()
        self.mqtt_id_exists_patch.start()
        self.db_logger_patcher.start()
        self.mqtt_logger_patcher.start()
        self.smart_home_logger_patcher.start()
        self.validators_logger_patcher.start()
        self.metrics_logger_patcher.start()

        self.app = create_app()
        self.app.testing = True
        self.app.logger = self.mock_logger
        self.client = self.app.test_client()
        self.valid_water_heater = {
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
        }

    def tearDown(self):
        self.validators_logger_patcher.stop()
        self.metrics_logger_patcher.stop()
        self.mqtt_get_devices_patch.stop()
        self.smart_home_logger_patcher.stop()
        self.mark_device_read_patcher.stop()
        self.get_mongo_client_patch.stop()
        self.get_redis_patch.stop()
        self.routes_get_devices_patch.stop()
        self.mqtt_id_exists_patch.stop()
        self.routes_id_exists_patch.stop()
        self.db_logger_patcher.stop()
        self.mqtt_logger_patcher.stop()
        self.mock_collection.delete_many({})
        self.mongo_client_constructor_patch.stop()
        self.routes_get_devices_patch.stop()
        self.redis_constructor_patch.stop()
        self.mqtt_constructor_patch.stop()

    def test_metrics(self):
        res = self.client.get('/metrics')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content_type, 'text/plain; charset=utf-8')

    def test_get_ids(self):
        self.mock_collection.insert_many([
            {"id": "main-water-heater"},
            {"id": "living-room-light"},
            {"id": "bedroom-ac"},
            {"id": "front-door-lock"},
            {"id": "living-room-curtains"},
        ])
        res = self.client.get('/api/ids')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content_type, 'application/json')
        self.assertEqual(sorted(res.get_json()), sorted([
            "main-water-heater",
            "living-room-light",
            "bedroom-ac",
            "front-door-lock",
            "living-room-curtains"
        ]))

    def test_get_all_devices(self):
        self.mock_collection.insert_many([
            {"id": "main-water-heater"},
            {"id": "living-room-light"},
            {"id": "bedroom-ac"},
            {"id": "front-door-lock"},
            {"id": "living-room-curtains"},
        ])
        res = self.client.get('/api/devices')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content_type, 'application/json')
        self.assertEqual(res.get_json(),
                         [{"id": "main-water-heater"}, {"id": "living-room-light"}, {"id": "bedroom-ac"},
                          {"id": "front-door-lock"}, {"id": "living-room-curtains"}])
        self.mark_device_read.assert_has_calls([
            call({"id": "main-water-heater"}),
            call({"id": "living-room-light"}),
            call({"id": "bedroom-ac"}),
            call({"id": "front-door-lock"}),
            call({"id": "living-room-curtains"}),
        ], any_order=True)

    def test_get_device_id_valid(self):
        self.mock_collection.insert_one({"id": "main-water-heater"})
        res = self.client.get('/api/devices/main-water-heater')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content_type, 'application/json')
        self.assertEqual(res.get_json(), {"id": "main-water-heater"})
        self.mark_device_read.assert_called_with({"id": "main-water-heater"})

    def test_get_device_id_invalid(self):
        self.mock_collection.insert_one({"id": "main-water-heater"})
        res = self.client.get('/api/devices/steve')
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.content_type, 'application/json')
        self.assertEqual(res.get_json(), {"error": "ID steve not found"})

    def test_post_device_valid(self):
        device = deepcopy(self.valid_water_heater)
        res = self.client.post('/api/devices', json=device)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content_type, 'application/json')
        self.assertEqual(res.get_json(), {'output': "Device added successfully"})
        self.assertEqual(self.valid_water_heater,
                         self.mock_collection.find_one({"id": "main-water-heater"}, {"_id": 0}))
        self.mock_mqtt_client.publish.assert_called()

    def test_post_device_invalid_duplicate_id(self):
        device = deepcopy(self.valid_water_heater)
        self.mock_collection.insert_one(deepcopy(device))
        res = self.client.post('/api/devices', json=device)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.content_type, 'application/json')
        self.assertEqual(res.get_json(), {'error': f"ID {device["id"]} already exists"})

    def test_post_device_invalid_validation(self):
        device = {
            "id": "main-water-heater",
            "type": "water_heater",
            "name": "Main Water Heater",
            "room": "Main Bath",
            "status": "off",
            "parameters": {
                "temperature": 40,
                "target_temperature": MAX_WATER_TEMP + 1,  # Too hot
                "is_heating": 5,  # Wrong type
                "timer_enabled": True,
                "scheduled_on": "06:30",
                "scheduled_off": "08:00"
            }
        }
        res = self.client.post('/api/devices', json=device)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.content_type, 'application/json')
        self.assertIn("error", res.get_json())
        reasons = res.get_json()['error']
        self.assertEqual(sorted(reasons), [
            f"'is_heating' must be a {type(True)}, got {type(5)} instead.",
            f"'target_temperature' must be between {MIN_WATER_TEMP} and {MAX_WATER_TEMP}, "
            f"got {MAX_WATER_TEMP + 1} instead.",
        ])

    def test_delete_device_valid(self):
        device = deepcopy(self.valid_water_heater)
        self.mock_collection.insert_one(deepcopy(device))
        res = self.client.delete('/api/devices/main-water-heater')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content_type, 'application/json')
        self.assertEqual(res.get_json(), {'output': "Device was deleted from the database"})
        self.assertEqual(None, self.mock_collection.find_one({"id": "main-water-heater"}))
        self.mock_mqtt_client.publish.assert_called()

    def test_delete_device_invalid(self):
        res = self.client.delete('/api/devices/main-water-heater')
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.content_type, 'application/json')
        self.assertEqual(res.get_json(), {'error': "ID main-water-heater not found"})

    def test_put_device_valid(self):
        device = deepcopy(self.valid_water_heater)
        self.mock_collection.insert_one(deepcopy(device))
        update = {
            "name": "Main Water Heater 34",
            "parameters": {
                "target_temperature": (MIN_WATER_TEMP + MAX_WATER_TEMP) / 2,
            }
        }
        res = self.client.put('/api/devices/main-water-heater', json=update)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content_type, 'application/json')
        self.assertEqual(res.get_json(), {'output': "Device updated successfully"})
        self.mock_mqtt_client.publish.assert_called()

    def test_put_device_invalid_not_found(self):
        update = {
            "name": "Main Water Heater 34",
            "parameters": {
                "target_temperature": (MIN_WATER_TEMP + MAX_WATER_TEMP) / 2,
            }
        }
        res = self.client.put('/api/devices/main-water-heater', json=update)
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.content_type, 'application/json')
        self.assertEqual(res.get_json(), {'error': "ID main-water-heater not found"})

    def test_put_device_invalid_id_mismatch(self):
        update = {
            "id": "main-water-heater",
            "name": "Main Water Heater 34",
            "parameters": {
                "target_temperature": (MIN_WATER_TEMP + MAX_WATER_TEMP) / 2,
            }
        }
        res = self.client.put('/api/devices/air-conditioner', json=update)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.content_type, 'application/json')
        self.assertEqual(res.get_json(),
                         {'error': f"ID mismatch: ID in URL: air-conditioner, ID in payload: main-water-heater"})

    def test_put_device_invalid_read_only(self):
        self.mock_collection.insert_one(deepcopy(self.valid_water_heater))
        update = {
            "id": "main-water-heater",
        }
        res = self.client.put('/api/devices/main-water-heater', json=update)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.content_type, 'application/json')
        self.assertEqual(res.get_json(), {'error': ["Cannot update read-only parameter 'id'"]})

    def test_put_device_invalid_validation(self):
        self.mock_collection.insert_one(deepcopy(self.valid_water_heater))
        update = {
            "parameters": {
                "target_temperature": MIN_WATER_TEMP - 1,
            }
        }
        res = self.client.put('/api/devices/main-water-heater', json=update)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.content_type, 'application/json')
        self.assertEqual(res.get_json(), {'error': [
            f"'target_temperature' must be between {MIN_WATER_TEMP} and {MAX_WATER_TEMP}, "
            f"got {MIN_WATER_TEMP - 1} instead."
        ]})


if __name__ == '__main__':
    unittest.main()
