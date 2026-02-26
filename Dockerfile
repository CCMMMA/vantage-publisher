FROM python:3.8-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        git \
        && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd -g 1000 weather
RUN useradd -u 1000 -g 1000 -m -s /bin/bash weather

WORKDIR /vantage-publisher

COPY requirements.txt requirements.txt
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

COPY airlink.py airlink.py
#COPY vantage.log /var/log/vantage.log
COPY vantage-publisher-threading.py vantage-publisher-threading.py

CMD ["python", "-u", "vantage-publisher-threading.py"]
