FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY atcsim ./atcsim
COPY scenarios ./scenarios
COPY run.py README.md ./
COPY tools ./tools
ENV ATC_DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8000
CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8000"]
