import requests
import json

lat = 40.1281
lon = 32.9950
url = f"https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={lat}&lon={lon}"
headers = {'User-Agent': 'WeatherMonitorBot/1.0'}

try:
    print(f"Fetching {url}...")
    r = requests.get(url, headers=headers, timeout=10)
    if r.status_code == 200:
        data = r.json()
        coords = data.get('geometry', {}).get('coordinates', [])
        props = data.get('properties', {})
        units = props.get('meta', {}).get('units', {})
        timeseries = props.get('timeseries', [])
        
        print("\n--- Met.no Data ---")
        print(f"Coordinates: {coords}")
        print(f"Units: {units}")
        
        if timeseries:
            now_data = timeseries[0]
            time_str = now_data.get('time')
            details = now_data.get('data', {}).get('instant', {}).get('details', {})
            
            print(f"Time: {time_str}")
            print(f"Temperature: {details.get('air_temperature')} {units.get('air_temperature')}")
            print(f"Pressure: {details.get('air_pressure_at_sea_level')} {units.get('air_pressure_at_sea_level')}")
            print(f"Humidity: {details.get('relative_humidity')} {units.get('relative_humidity')}")
            print(f"Wind Speed: {details.get('wind_speed')} {units.get('wind_speed')}")
            
            # Check for altitude in response if available (usually in geometry or header, but compact might hide it)
            # Actually Met.no locationforecast doesn't explicitly return the model altitude in 'compact', 
            # but usually it's good to check if coordinates were snapped.
            
    else:
        print(f"Error: {r.status_code}")
        print(r.text)

except Exception as e:
    print(f"Exception: {e}")
