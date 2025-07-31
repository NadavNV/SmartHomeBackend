from flask import jsonify, request, Response
from flask_jwt_extended import create_access_token, jwt_required, get_jwt
import time
from redis.exceptions import ConnectionError
from werkzeug.security import generate_password_hash, check_password_hash

# Databases
from services.db import get_redis, get_mongo_client, get_devices_collection, get_users_collection, id_exists, \
    DatabaseNotInitializedError
from pymongo.errors import ConnectionFailure, OperationFailure

# Validation
from validation.validators import validate_device_data

# Monitoring
from prometheus_client import generate_latest
from monitoring.metrics import (
    request_count,
    request_latency,
    mark_device_read,
    generate_analytics,
)

# MQTT
from services.mqtt import publish_mqtt, get_mqtt, update_device, MQTTNotInitializedError


def setup_routes(app) -> None:
    """
    Set up the different endpoints that the given Flask app serves, as well as
    functions to run before and after generating the response.

    :param app: The Flask app.
    :return: None
    :rtype: None
    """

    @app.before_request
    def before_request() -> None:
        """
        Function to run before each request. Used for calculating message latency.

        :return: None
        :rtype: None
        """
        request.start_time = time.time()

    @app.get("/metrics")
    def metrics() -> tuple[Response, int]:
        """
        Used by Prometheus to get gathered metrics.

        :return: The gathered metrics in plain text.
        :rtype: Response
        """
        return Response(generate_latest(), mimetype="text/plain"), 200

    @app.post("/register")
    def register_user() -> tuple[Response, int]:
        """
        Registers a new non-admin user with this database. Must include username and password in the request json.

        Returns JWT access token on success and {'error': <reasons>} on failure.

        :return: Response
        :rtype: tuple[Response, int]
        """
        if not request.is_json:
            return jsonify({"error": "Missing or invalid JSON in request"}), 400
        data = request.json
        if "username" not in data:
            return jsonify({"error": "Request must include a username field"}), 400
        if "password" not in data:
            return jsonify({"error": "Request must include a password field"}), 400
        user = get_users_collection().find_one({"username": data['username']})
        if user is not None:
            return jsonify({"error": "User already exists"}), 409

        hashed_pw = generate_password_hash(data['password'])
        get_users_collection().insert_one({
            "username": data['username'],
            "password_hash": hashed_pw,
            "role": "user"
        })
        token = create_access_token(identity=data['username'], additional_claims={"role": "user"})
        return jsonify(access_token=token), 201

    @app.post('/login')
    def login() -> tuple[Response, int]:
        """
        Logs into the website with the username and password given in the request json.

        Returns JWT access token on success and {'error': <reasons>} on failure.
        :return: Response
        :rtype: tuple[Response, int]
        """
        if not request.is_json:
            return jsonify({"error": "Missing or invalid JSON in request"}), 400
        data = request.json
        if "username" not in data:
            return jsonify({"error": "Request must include a username field"}), 400
        if "password" not in data:
            return jsonify({"error": "Request must include a password field"}), 400
        user = get_users_collection().find_one({"username": data['username']})
        if not user or not check_password_hash(user['password_hash'], data['password']):
            return jsonify({"error": "Invalid credentials"}), 401

        token = create_access_token(identity=user['username'], additional_claims={"role": user['role']})
        return jsonify(access_token=token), 200

    @app.get("/api/ids")
    def get_device_ids() -> tuple[Response, int]:
        """
        Returns a list of all device IDs currently in the Mongo database.

        :return: List of device IDs.
        :rtype: tuple[Response, int]
        """
        device_ids = list(get_devices_collection().find({}, {'id': 1, '_id': 0}))
        return jsonify([device_id['id'] for device_id in device_ids]), 200

    @app.get("/api/devices")
    def get_all_devices() -> tuple[Response, int]:
        """
        Returns a list of all devices currently in the Mongo database and their details.
        :return: List of devices.
        :rtype: tuple[Response, int]
        """
        devices = list(get_devices_collection().find({}, {'_id': 0}))
        for device in devices:
            if "id" in device:
                if not get_redis().sismember("seen_devices", device["id"]):
                    mark_device_read(device)  # Assumes that devices in the DB are already validated
        return jsonify(devices), 200

    @app.get("/api/devices/<device_id>")
    def get_device(device_id) -> tuple[Response, int]:
        """
        Returns a single device from the Mongo database.
        :param device_id:
        :return:
        """
        device = get_devices_collection().find_one({'id': device_id}, {'_id': 0})
        if device is not None:
            if not get_redis().sismember("seen_devices", device["id"]):
                mark_device_read(device)
            return jsonify(device), 200
        else:
            error = f"ID {device_id} not found"
            app.logger.error(error)
            return jsonify({'error': error}), 404

    @app.post("/api/devices")
    def add_device() -> tuple[Response, int]:
        """
        Adds a new device to the Mongo database.

        Returns {'output': "Device added successfully"} on success and {'error': <reasons>} on failure.
        :return: Response.
        :rtype: tuple[Response, int]
        """
        if not request.is_json:
            return jsonify({"error": "Missing or invalid JSON in request"}), 400
        new_device = request.json
        success, reasons = validate_device_data(new_device, new_device=True)
        if success:
            if id_exists(new_device["id"]):
                return jsonify({'error': f"ID {new_device["id"]} already exists"}), 400
            else:
                get_devices_collection().insert_one(new_device)
                mark_device_read(new_device)
                publish_mqtt(
                    payload=new_device,
                    device_id=new_device['id'],
                    method="post",
                )
                return jsonify({'output': "Device added successfully"}), 200
        else:
            return jsonify({'error': reasons}), 400

    @app.delete("/api/devices/<device_id>")
    @jwt_required()
    def delete_device(device_id: str) -> tuple[Response, int]:
        """
        Deletes a device from the Mongo database.

        Returns {'output': "Device was deleted from the database"} on success and {'error': <reason>} on failure.
        :param str device_id: ID of the device to delete.
        :return: Response.
        :rtype: tuple[Response, int]
        """
        role = get_jwt()["role"]
        if role != 'admin':
            return jsonify({"error": "Admins only"}), 403

        if id_exists(device_id):
            get_redis().srem("seen_devices", device_id)  # Allows adding a new device with old id
            get_devices_collection().delete_one({"id": device_id})
            publish_mqtt(
                payload={},
                device_id=device_id,
                method="delete",
            )
            return jsonify({"output": "Device was deleted from the database"}), 200
        return jsonify({"error": f"ID {device_id} not found"}), 404

    @app.put("/api/devices/<device_id>")
    def update_device_endpoint(device_id: str) -> tuple[Response, int]:
        """
        Updates a device in the Mongo database. A JSON object representing the new device configuration
        must be included in the request body.

        Validates the new device configuration and updates the database if it is valid, returning
        {'output': "Device updated successfully"}, or {'error': <reason>} on failure.

        :param str device_id: ID of the device to update.
        :return: Response.
        :rtype: tuple[Response, int]
        """
        if not request.is_json:
            return jsonify({"error": "Missing or invalid JSON in request"}), 400
        updated_device = request.json
        id_to_update = updated_device.get("id", None)
        if id_to_update is not None and id_to_update != device_id:
            error = f"ID mismatch: ID in URL: {device_id}, ID in payload: {id_to_update}"
            app.logger.error(error)
            return jsonify({'error': error}), 400
        device = get_devices_collection().find_one({'id': device_id}, {'_id': 0})
        if device is not None:
            app.logger.info("Validating new device configuration...")
            success, reasons = validate_device_data(updated_device, device_type=device["type"])
            if success:
                app.logger.info(f"Success! Updating device {device_id}")
                update_device(device, updated_device)
                publish_mqtt(
                    payload=updated_device,
                    device_id=device_id,
                    method="update",
                )
                return jsonify({'output': "Device updated successfully"}), 200
            else:
                return jsonify({'error': reasons}), 400
        return jsonify({'error': f"ID {device_id} not found"}), 404

    @app.get("/api/devices/analytics")
    def device_analytics() -> tuple[Response, int]:
        """
        Generates a json object of aggregate and individual device metrics.

        :return: A flask Response object and an HTTP status code.
        :rtype: tuple[Response, int]
        """
        return generate_analytics()

    @app.get("/healthy")
    def health_check():
        """
        Health check endpoint for Kubernetes liveness probe.

        This endpoint confirms that the Flask application is up and responding.
        It does not validate connections to external dependencies like databases
        or message brokers.

        :return: JSON response indicating the service is running.
        :rtype: Response
        """
        return jsonify({"Status": "Healthy"})

    @app.get("/ready")
    def ready_check():
        """
        Readiness check endpoint for Kubernetes readiness probe.

        This endpoint checks whether the application is ready to serve traffic by:

        - Verifying connectivity to the MongoDB database using a ping command.
        - Checking if the MQTT client is currently connected.
        - Confirming the Redis client connection with a ping.

        Returns HTTP 200 if all checks succeed, or HTTP 500 if any dependency is not available.

        :return: JSON response indicating the readiness status.
        :rtype: Response
        """
        try:
            app.logger.debug("Pinging MongoDB . . .")
            get_mongo_client().admin.command('ping')
            app.logger.debug("MongoDB ping successful.")

            app.logger.debug("Checking MQTT connection . . .")
            if not get_mqtt().is_connected():
                app.logger.debug("MQTT not connected")
                return jsonify({"Status": "Not ready"}), 500
            app.logger.debug("MQTT connected.")

            app.logger.debug("Pinging Redis . . .")
            if get_redis().ping():
                app.logger.debug("Redis ping successful.")
                app.logger.info("Ready")
                return jsonify({"Status": "Ready"})
            else:
                app.logger.error("Not ready")
                app.logger.debug("Redis ping failed.")
                return jsonify({"Status": "Not ready"}), 500

        except (ConnectionFailure, OperationFailure, ConnectionError, DatabaseNotInitializedError,
                MQTTNotInitializedError):
            app.logger.exception("Dependency check failed.")
            return jsonify({"Status": "Not ready"}), 500

    @app.after_request
    def after_request(response):
        """
        Function to run just before the HTTP response is sent. Used to calculate HTTP metrics
        and to add response headers.

        :param response: The HTTP response to send.
        :return: The modified HTTP response.
        """
        # Prometheus tracking
        method = request.method
        endpoint = request.path
        status_code = str(response.status_code)
        if hasattr(request, 'start_time'):
            duration = time.time() - request.start_time
            request_count.labels(method=method, endpoint=endpoint, status_code=status_code).inc()
            request_latency.labels(endpoint).observe(duration)

        # CORS headers
        if method == 'OPTIONS':
            response.headers['Allow'] = '*'
            response.headers['Access-Control-Allow-Methods'] = 'HEAD, DELETE, POST, GET, OPTIONS, PUT, PATCH'
        response.headers['Access-Control-Allow-Headers'] = '*'
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response
