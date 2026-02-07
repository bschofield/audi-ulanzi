#!/usr/bin/env python3
"""Poll Audi SoC and push to AWTRIX 3 display on Ulanzi TC001."""

import asyncio
import json
import sys
from pathlib import Path
import requests
from audi_connect import AudiConnect

BATTERY_ICONS = [
    (20, "6354"),
    (40, "6355"),
    (60, "6356"),
    (80, "6357"),
]
BATTERY_ICON_FULL = "6358"
BATTERY_ICON_CHARGING = "21585"
BATTERY_ICON_DRIVING = "1172"
BATTERY_ICON_PARKED = "70271"

# API constants
STATUS_URL = "https://emea.bff.cariad.digital/vehicle/v1/vehicles/{vin}/selectivestatus?jobs=charging"
PARKING_URL = "https://emea.bff.cariad.digital/vehicle/v1/vehicles/{vin}/parkingposition"
X_CLIENT_ID = "77869e21-e30a-4a92-b016-48ab7d3db1d8"


def load_config(config_file: Path) -> dict:
    """Load configuration from JSON file.

    Expected format:
    {
      "username": "your.email@example.com",
      "password": "your_password",
      "awtrix_ip": "192.168.1.x",
      "home": {
        "lat": 53.896171,
        "lon": -0.962557
      },
      "vehicles": {
        "WAUXXXXXXXXXXXXXX": "Q4",
        "WAUXXXXXXXXXXXXXX": "Q6",
        "WAUXXXXXXXXXXXXXX": "Q8",
        "WAUXXXXXXXXXXXXXX": "GT"
      }
    }
    """
    if not config_file.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_file}\n"
            f"Create a JSON file with: username, password, awtrix_ip, home (with lat/lon), and vehicles dict"
        )
    return json.loads(config_file.read_text())


def soc_icon(pct: int) -> str:
    for threshold, icon in BATTERY_ICONS:
        if pct < threshold:
            return icon
    return BATTERY_ICON_FULL


def soc_color(pct: int) -> str:
    if pct >= 60:
        return "#00FF00"
    if pct > 20:
        return "#FFA500"
    return "#FF0000"


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in meters between two lat/lon points using Haversine formula."""
    from math import radians, sin, cos, sqrt, atan2

    R = 6371000  # Earth radius in meters

    lat1_rad = radians(lat1)
    lat2_rad = radians(lat2)
    delta_lat = radians(lat2 - lat1)
    delta_lon = radians(lon2 - lon1)

    a = sin(delta_lat / 2) ** 2 + cos(lat1_rad) * cos(lat2_rad) * sin(delta_lon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))

    return R * c


def reverse_geocode(lat: float, lon: float) -> str:
    """Get location name from coordinates using Nominatim API.

    Returns formatted location as "Street Name, Town" or None if request fails.
    """
    try:
        url = "https://nominatim.openstreetmap.org/reverse"
        params = {
            "lat": lat,
            "lon": lon,
            "format": "json",
            "addressdetails": 1,
        }
        headers = {
            "User-Agent": "AudiAWTRIX/1.0"  # Nominatim requires a user agent
        }
        resp = requests.get(url, params=params, headers=headers, timeout=5)
        resp.raise_for_status()
        data = resp.json()

        address = data.get("address", {})
        # Try to get street (road, street, pedestrian, etc.)
        street = (address.get("road") or
                 address.get("street") or
                 address.get("pedestrian") or
                 address.get("footway"))
        # Try to get town/city
        town = (address.get("town") or
               address.get("city") or
               address.get("village") or
               address.get("hamlet"))

        parts = []
        if street:
            parts.append(street)
        if town:
            parts.append(town)

        return ", ".join(parts) if parts else None
    except Exception:
        return None


def push_app(awtrix_url: str, name: str, soc: int, charging: bool, icon: str = None, location: str = None, duration: int = 8):
    """Push a custom app to AWTRIX."""
    # Determine icon: use provided icon, or default to charging/battery icons
    if icon is None:
        icon = BATTERY_ICON_CHARGING if charging else soc_icon(soc)

    # Format text with optional location
    text = f"{name} {soc}%"
    if location:
        text = f"{name} {soc}% - {location}"

    payload = {
        "text": text,
        "icon": icon,
        "color": soc_color(soc),
        "progress": min(int(soc * 100 / 80), 100),
        "progressC": soc_color(soc),
        "progressBC": "#333333",
        "duration": duration,
        "textCase": 2,
        "lifetime": 1800,
    }
    r = requests.post(f"{awtrix_url}?name={name.lower()}", json=payload, timeout=5)
    r.raise_for_status()


async def get_soc(audi, vin: str) -> dict:
    url = STATUS_URL.format(vin=vin)
    headers = {
        "Authorization": f"Bearer {audi.access_token}",
        "Accept": "application/json",
        "X-Client-Id": X_CLIENT_ID,
    }
    async with audi.session.get(url, headers=headers) as resp:
        if resp.status not in (200, 207):
            text = await resp.text()
            raise Exception(f"{vin}: HTTP {resp.status} - {text}")
        return await resp.json()


async def get_parking_position(audi, vin: str) -> dict:
    """Get parking position. Returns None if car is driving (204 response)."""
    url = PARKING_URL.format(vin=vin)
    headers = {
        "Authorization": f"Bearer {audi.access_token}",
        "Accept": "application/json",
        "X-Client-Id": X_CLIENT_ID,
    }
    async with audi.session.get(url, headers=headers) as resp:
        if resp.status == 204:
            return None  # Car is driving
        if resp.status == 200:
            return await resp.json()
        text = await resp.text()
        raise Exception(f"{vin}: HTTP {resp.status} - {text}")


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Push Audi SoC to AWTRIX 3")
    parser.add_argument("--config", "-c", type=Path, help=f"Config file", required=True)
    args = parser.parse_args()

    # Load configuration
    config = load_config(args.config)
    awtrix_url = f"http://{config['awtrix_ip']}/api/custom"
    home_lat = config.get("home", {}).get("lat", None)
    home_lon = config.get("home", {}).get("lon", None)

    async with AudiConnect(config["username"], config["password"]) as audi:
        await audi.login()

        for vin, name in config["vehicles"].items():
            try:
                data = await get_soc(audi, vin)
                soc = data["charging"]["batteryStatus"]["value"]["currentSOC_pct"]
                charging_state = data["charging"]["chargingStatus"]["value"][
                    "chargingState"
                ]
                is_charging = charging_state not in (
                    "notReadyForCharging",
                    "readyForCharging",
                    "off",
                )

                # Get parking position to determine icon (only if home is configured)
                icon = None
                location = None
                duration = 8  # Default for at home
                status_msg = f"charging={is_charging}"

                if home_lat is not None and home_lon is not None:
                    parking_data = await get_parking_position(audi, vin)
                    if parking_data is None:
                        # Car is driving - use current time
                        from datetime import datetime
                        icon = BATTERY_ICON_DRIVING
                        time_suffix = datetime.now().strftime("%H%M")
                        location = time_suffix
                        duration = 20
                        status_msg = f"driving - {time_suffix}"
                    else:
                        # Car is parked, check if away from home
                        car_lat = parking_data["data"]["lat"]
                        car_lon = parking_data["data"]["lon"]
                        distance = haversine_distance(home_lat, home_lon, car_lat, car_lon)

                        if distance > 100:
                            icon = BATTERY_ICON_PARKED
                            duration = 20
                            # Get location name for display
                            location = reverse_geocode(car_lat, car_lon)

                            # Get timestamp from parking data
                            from datetime import datetime
                            parking_time_str = parking_data["data"]["timestamp"]
                            parking_time = datetime.fromisoformat(parking_time_str.replace('Z', '+00:00'))
                            time_suffix = parking_time.strftime("%H%M")

                            if location:
                                location = f"{location} - {time_suffix}"
                                status_msg = f"parked - {location}"
                            else:
                                location = time_suffix
                                status_msg = f"parked {int(distance)}m from home - {time_suffix}"
                        else:
                            status_msg = f"at home, {status_msg}"

                push_app(awtrix_url, name, soc, is_charging, icon=icon, location=location, duration=duration)
                print(f"{name}: {soc}% {status_msg} -> AWTRIX OK")

            except Exception as e:
                print(f"{name} ({vin}): ERROR - {e}", file=sys.stderr)
                sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
