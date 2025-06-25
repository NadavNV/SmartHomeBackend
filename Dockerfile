FROM python:3.11-slim

WORKDIR /app

COPY SmartHomeBackend/requirements.txt .

RUN pip install -r requirements.txt

COPY SmartHomeBackend/ .

CMD ["python3", "main.py"]
