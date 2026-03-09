import requests
import logging


logger = logging.getLogger(__name__)

def airlinkData(uuid, api_key="", api_secret=""):
    api_key = str(api_key or "").strip()
    api_secret = str(api_secret or "").strip()
    if not api_key or not api_secret:
        logger.warning("AirLink credentials missing: set airlinkApiKey and airlinkApiSecret in config")
        return {}

    url = f"https://api.weatherlink.com/v2/current/{uuid}?api-key={api_key}"
    headers = {
        'X-Api-Secret': api_secret
    }

    data = {}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        res = response.json()
    except requests.RequestException as e:
        logger.error("AirLink data request failed: %s", e)
        return data
    except ValueError as e:
        logger.error("JSON parsing failed: %s", e)
        return data

    sensors = res.get("sensors", [])

    for sensor in sensors:
        if sensor.get("data") and "hum" in sensor["data"][0]:
            raw_data = sensor["data"][0]

            # WeatherLink temperatures are reported in Fahrenheit.
            # Convert to SI base unit Celsius.
            F_to_C = ['temp', 'heat_index', 'dew_point', 'wet_bulb']
            # WeatherLink pressure is reported in inHg.
            # Convert to SI base unit Pascal.
            inHg_to_HPa = ['bar']
            fields = [
                'hum', 'pm_10_3_hour', 'pm_10_24_hour', 'pm_2p5_1_hour',
                'aqi_nowcast_val', 'heat_index', 'pm_2p5_nowcast',
                'pm_2p5_24_hour', 'pm_1', 'aqi_val', 'temp',
                'pm_2p5_3_hour', 'aqi_1_hour_val', 'pm_10_nowcast',
                'pm_10_1_hour', 'dew_point', 'pm_10', 'pm_2p5', 'wet_bulb', 'bar'
            ]

            for field in fields:
                if field in raw_data:
                    if field in F_to_C:
                        try:
                            data[field] = (float(raw_data[field]) - 32.0) * 5.0 / 9.0
                        except(ValueError, TypeError) as e:
                            logger.warning("Unable to convert field %s: %s", field, e)
                    elif field in inHg_to_HPa:
                        try:
                            data[field] = float(raw_data[field]) * 33.8639
                        except(ValueError, TypeError) as e:
                            logger.warning("Unable to convert field %s: %s", field, e)
                    else:
                        try:
                            data[field] = float(raw_data[field])
                        except (ValueError, TypeError) as e:
                            logger.warning("Unable to convert field %s: %s", field, e)

            break

    return data
