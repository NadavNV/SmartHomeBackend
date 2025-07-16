import config.env  # noqa: F401  # load_dotenv side effect
from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix
import logging.handlers
from routes import setup_routes
from services.db import init_db, get_mongo_client, get_redis
from services.mqtt import init_mqtt, get_mqtt
import atexit
from dotenv import load_dotenv

load_dotenv()

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

smart_home_logger = logging.getLogger("smart_home")
smart_home_logger.propagate = True


def create_app() -> Flask:
    """
    Create the Flask app instance, initiate the databases and MQTT client,
    set up the routes, and return the Flask app.
    :return: Flask app instance
    :rtype: Flask
    """
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    app.logger.propagate = False
    init_db()
    init_mqtt()
    setup_routes(app)
    return app


@atexit.register
def on_shutdown() -> None:
    """
    Function to run when shutting down the server. Disconnects from MQTT broker
    and DB clients.

    :return: None
    :rtype: None
    """
    get_mqtt().loop_stop()
    get_mqtt().disconnect()
    get_mongo_client().close()
    get_redis().close()
    smart_home_logger.info("Shutting down")


if __name__ == '__main__':
    create_app().run(host='0.0.0.0', port=5200, debug=True, use_reloader=False)
