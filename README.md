# SmartHomeBackend

Part of our final project in DevSecOps course at Bar-Ilan
University ([Main project repository](https://github.com/NadavNV/SmartHomeConfig)). The project allows viewing and
managing different Smart home devices such as lights, water heaters, or air conditioners.

It is divided into several microservices, and this microservice handles API calls from the frontend as well as MQTT
messages from the different devices to update and maintain a MongoDB database as the single source of truth.

## Table of Contents

- [Requirements](#requirements)
- [Technologies Used](#technologies-used)
- [Usage](#usage)
    - [Environment Variables](#environment-variables)
    - [Running The App](#running-the-app)
        - Run Locally
        - Run with Docker
- [API Reference](#api-reference)
    - Devices
    - Monitoring
- [Monitoring](#monitoring)
    - [Metrics](#metrics)

## Requirements

- [Python3](https://www.python.org/downloads/)
- [nginx](https://nginx.org/en/download.html)

## Technologies Used

| Layer                  | Technology     |
|------------------------|----------------|
| **API Framework**      | Flask          |
| **Application Server** | Gunicorn       |
| **Web Server**         | nginx          |
| **Database**           | MongoDB, Redis |
| **Messaging**          | Paho-MQTT      |

## Usage

### Environment Variables

- `REDIS_HOST` - Host name for the redis database. Used to hold metric-related data structures.
- `REDIS_PORT` - Port number for the redis database.
- `REDIS_USER` - Username for the redis database. Defaults to `default`.
- `REDIS_PASS` - Password for the redis database.
- `MONGO_DB_CONNECTION_STRING` - Connection string to the MongoDB database that holds device information. May or may not
  include credentials, but if they are omitted they must be provided separately.
- `MONGO_USER` - MongoDB username.
- `MONGO_PASS` - MongoDB password.
- `PROMETHEUS_URL` - The url of your prometheus server for tracking metrics, including port number.
- `BROKER_HOST` - Hostname of the MQTT broker used to manage device messages. Defaults to `test.mosquitto.org`.
- `BROKER_PORT` - The port to connect to. Defaults to `1883`.
- `MQTT_TOPIC` - The MQTT topic to subscribe to, e.g. `project/devices`.

### Running The App

- To run on your local machine:
    - Make sure you have nginx and python (3.13 or later recommended) installed.
    - Initialize all the necessary environment variables. Using a `.env` file in the project folder is recommended.
    - Clone this repo:
      ```bash
      git clone https://github.com/NadavNV/SmartHomeBackend.git
      cd SmartHomeBackend
      ```
    - Create and activate a virtual environment (optional but recommended):
        ```bash
        python -m venv venv
        source venv/bin/activate  # On Windows use: venv\Scripts\activate
        ```
    - Run `pip install -r requirements.txt`.
    - Run the flask app. On Linux:
        ```bash
        nohup gunicorn --factory -w 1 -b 127.0.0.1:8000 main:create_app > gunicorn.log 2>&1 &
        ```
      And on Windows:
        ```powershell
        Start-Process gunicorn -ArgumentList "--factory", "-w", "1", "-b", "127.0.0.1:8000", "main:create_app"
        ```
    - Make sure your `nginx.conf` is configured to listen on port `5200` and proxy requests to `127.0.0.1:8000`.
    - Start nginx. On Linux, if you have a custom `nginx.conf`file in your project folder:
        ```bash
        sudo nginx -c $(pwd)/nginx.conf
        # Or reload it if it's already running
        sudo nginx -s reload
        ```
      Or on Windows:
        ```powershell
        nginx -c "$PWD\nginx.conf"
        # Or reload
        nginx -s reload
        ```
      (If Windows process or port, run PowerShell as Administrator.)
    - Access the app at `http://localhost:5200`
- To run in a Docker container:
    - Make sure you have a running Docker engine.
    - Initialize all the necessary environment variables. Using a `.env` file in the project folder is recommended.
    - Clone this repo:
      ```bash
      git clone https://github.com/NadavNV/SmartHomeBackend.git
      cd SmartHomeBackend
      ```
    - Make sure your `nginx.conf` is configured to listen on port `5200` and proxy requests to the flask container.
    - This app requires two images, one for the app itself and one for the nginx reverse-proxy. Run:
      ```bash
      docker build -f flask.Dockerfile -t <name for the Flask image> .
      docker build -f nginx.Dockerfile -t <name for the nginx image> .
      ```
    - Set up a docker network:
        ```bash
        docker network create smart-home-net
        ```
    - Run:
      ```bash
      docker run -d \
        -p 5200:5200 \
        --network smart-home-net \
        --name <name for the container \
        <name of the nginx image>
      
      docker run [-d] \
        -p 8000:8000 \
        --env-file .env
        --network smart-home-net \
        --name <name for the container \
        <name of the Flask image>
      ```
        - use -d for the Flask container to run it in the background, or omit it to see logging messages.
    - Access the app at `gttp://localhost:5200`.

## API Reference

<details>
<summary>Devices</summary>

| Method | Endpoint                   | Description                 |
|--------|----------------------------|-----------------------------|
| GET    | `/api/ids`                 | List all device IDs         |
| GET    | `/api/devices`             | List all devices            |
| GET    | `/api/devices/<id>`        | Device details              |
| GET    | `/api/devices/analytics`   | Device usage analytics      |
| POST   | `/api/devices`             | Add new device              |
| PUT    | `/api/devices/<id>`        | Update device information   |
| DELETE | `/api/devices/<id>`        | Delete device               |
| POST   | `/api/devices/<id>/action` | Update device configuration |

</details>

<details>
<summary>Monitoring</summary>

| Method | Endpoint   | Description        |
|--------|------------|--------------------|
| GET    | `/metrics` | Prometheus metrics |
| GET    | `/healthy` | Liveness check     |
| GET    | `/ready`   | Readiness check    |

</details>

## Monitoring

### Metrics

- Total HTTP requests
- HTTP failure rate
- Request latency
- Various device usage metrics.
