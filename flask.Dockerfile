FROM python:3.13-alpine

WORKDIR /app

COPY requirements.txt .

RUN apk add --no-cache git
RUN pip install --no-cache -r requirements.txt

COPY *.py .
COPY monitoring/ monitoring/
COPY services/ services/
COPY test/ test/

# Ensure all packages are visible to python
ENV PYTHONPATH="${PYTHONPATH}:/app"

EXPOSE 8000

CMD ["gunicorn", "-w", "1", "-b", "0.0.0.0:8000", "main:create_app()"]
