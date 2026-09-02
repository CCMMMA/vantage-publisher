build:
	docker build -t vantage-publisher .

run:
	docker run --network=host -v ./config.json:/vantage-publisher/config.json -v ./parameters.json:/vantage-publisher/parameters.json -d vantage-publisher
