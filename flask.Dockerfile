FROM python:3.13-alpine

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY validation/ validation/
COPY monitoring/ monitoring/
COPY config/ config/
COPY services/ services/

EXPOSE 8000

CMD ["gunicorn", "--factory", "-w", "1", "-b", "0.0.0.0:8000", "main:create_app"]
