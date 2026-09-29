FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy
RUN pip install --no-cache-dir playwright==1.47.0 flask requests beautifulsoup4 lxml jsonschema qdrant-client tzdata
WORKDIR /app
COPY courtwatch/ /app/courtwatch/
COPY schema.json /app/schema.json
RUN cp /app/courtwatch/load_db.py /app/load_db.py
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1
EXPOSE 8090
CMD ["python3", "-m", "courtwatch.app"]
