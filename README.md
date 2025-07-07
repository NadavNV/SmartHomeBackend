# SmartHomeBackend

Part of our final project in DevSecOps course at Bar-Ilan
University ([Main project repository](https://github.com/NadavNV/SmartHomeConfig)). The project allows viewing and
managing different Smart home devices such as lights, water heaters, or air conditioners.

It is divided into several microservices, and this microservice handles API calls from the frontend as well as MQTT
messages from the different devices to update and maintain a MongoDB database as the single source of truth.

## Requirements

- [Python3](https://www.python.org/downloads/)

## Technologies Used

| Layer                  | Technology |
|------------------------|------------|
| **API Framework**      | Flask      |
| **Application Server** | Gunicorn   |
| **Web Server**         | nginx      |
| **Database**           | MongoDB    |
| **Messaging**          | Paho-MQTT  |

## Usage

- To run on your local machine:
    - Make sure you have python installed.
    - Clone this repo:
      ```bash
      git clone https://github.com/NadavNV/SmartHomeBackend.git
      cd SmartHomeBackend
      ```
    - Run `pip install -r requirements.txt`.
    - Run `python main.py`.
- To run in a Docker container:
    - Make sure you have a running Docker engine.
    - Clone this repo:
      ```bash
      git clone https://github.com/NadavNV/SmartHomeBackend.git
      cd SmartHomeBackend
      ```
    - This app requires two images, one for the app itself and one for the nginx reverse-proxy. Run: ```bash docker build -t <name for the image> .```

    - Run `docker run -e "API_URL=<full backend address>" <image name>`.