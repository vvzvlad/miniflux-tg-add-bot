FROM python:3.11-slim

WORKDIR /app

# System packages; add other ones here if they become necessary.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Dependencies as a separate layer: change less often than code -> cached better
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Runtime state directory (mounted as a docker volume in production)
RUN mkdir -p data

# Application code
COPY src/ src/
COPY main.py .

# No EXPOSE: this is a polling bot, it has no inbound port.

CMD ["python", "main.py"]
