#!/bin/bash

cd /home/weather/vantage-publisher
docker-compose down
git pull
chmod +x vantage-updater.sh
make build
docker compose up -d
