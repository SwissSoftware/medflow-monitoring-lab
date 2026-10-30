FROM python:3.12-slim

WORKDIR /app
COPY webhook_sink.py .

EXPOSE 9999
CMD ["python", "-u", "webhook_sink.py"]
