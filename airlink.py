import requests

def airlinkData(uuid):
    url = f"https://api.weatherlink.com/v2/current/{uuid}?api-key=b3xchodamrmfri3qd2zguel7nifkndh9"
    headers = {
        'X-Api-Secret': 'zl4kshrwl7t0xl8z2u07ewptbmjfcdg9'
    }

    data = {}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        res = response.json()
    except requests.RequestException as e:
        print(f"Errore sulla richiesta dati Airlink: {e}")
        return data
    except ValueError as e:
        print(f"Errore nel parsing JSON: {e}")
        return data

    sensors = res.get("sensors", [])

    for sensor in sensors:
        if sensor.get("data") and "hum" in sensor["data"][0]:
            raw_data = sensor["data"][0]

            F_to_C = ['temp', 'heat_index', 'dew_point', 'wet_bulb']
            inHg_to_hPa = ['bar']
            fields = [
                'hum', 'pm_10_3_hour', 'pm_10_24_hour', 'pm_2p5_1_hour',
                'aqi_nowcast_val', 'heat_index', 'pm_2p5_nowcast',
                'pm_2p5_24_hour', 'pm_1', 'aqi_val', 'temp',
                'pm_2p5_3_hour', 'aqi_1_hour_val', 'pm_10_nowcast',
                'pm_10_1_hour', 'dew_point', 'pm_10', 'pm_2p5', 'wet_bulb'
            ]

            for field in fields:
                if field in raw_data:
                    if field in F_to_C:
                        try:
                            data[field] = (float(raw_data[field]) - 32.0) * 5.0 / 9.0
                        except(ValueError, TypeError) as e:
                            print(f"Impossibile convertire il campo {field}: {e}")
                    elif field in inHg_to_hPa:
                        try:
                            data[field] = (float(raw_data[field]) *  33.86389)
                        except(ValueError, TypeError) as e:
                            print(f"Impossibile convertire il campo {field}: {e}")
                    else:
                        try:
                            data[field] = float(raw_data[field])
                        except (ValueError, TypeError) as e:
                            print(f"Impossibile convertire il campo {field}: {e}")

            break

    return data
