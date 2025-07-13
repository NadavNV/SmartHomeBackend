import os
import redis
from dotenv import load_dotenv

# Load env vars from the shared constants file
load_dotenv("config/constants.env")

REDIS_PASS = os.getenv("REDIS_PASS")

r = redis.Redis(
    host="redis-13476.c276.us-east-1-2.ec2.redns.redis-cloud.com",
    port=13476,
    decode_responses=True,
    username="default",
    password=REDIS_PASS,
)
