FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY medflow_exporter.py .

EXPOSE 8000 8001
CMD ["python", "medflow_exporter.py"]
