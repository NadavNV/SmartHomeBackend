FROM python:3.14.0b3-alpine

WORKDIR /app

COPY requirements.txt .

RUN pip install -r requirements.txt

COPY main.py .

EXPOSE 5200

CMD ["python3", "main.py"]
