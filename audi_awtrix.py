#!/usr/bin/env python3
"""Poll Audi SoC and push to AWTRIX 3 display on Ulanzi TC001."""

import asyncio
import json
import sys
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional
import requests
from audi_connect import AudiConnect, log

# Battery icon IDs (AWTRIX icon numbers)
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

# API endpoints and credentials
STATUS_URL = "https://emea.bff.cariad.digital/vehicle/v1/vehicles/{vin}/selectivestatus?jobs=charging"
PARKING_URL = "https://emea.bff.cariad.digital/vehicle/v1/vehicles/{vin}/parkingposition"
X_CLIENT_ID = "77869e21-e30a-4a92-b016-48ab7d3db1d8"

# Display configuration
DURATION_AT_HOME = 5  # seconds
DURATION_AWAY = 30  # seconds
DISPLAY_LIFETIME = 1800  # seconds (30 minutes)
DISPLAY_TEXT_CASE = 2  # AWTRIX text case (0=global setting, 1=uppercase, 2=as sent)
PROGRESS_BAR_COLOR_BG = "#333333"
SOC_DISPLAY_MAX = 80  # Show 100% progress bar at 80% SoC

# Distance thresholds
HOME_DISTANCE_THRESHOLD = 100  # meters

# Timeouts
HTTP_TIMEOUT = 5  # seconds

# Geocode cache
GEOCODE_CACHE_PRECISION = 4  # Decimal places for lat/lon (4 = ~11m precision)
GEOCODE_CACHE_MAX_ENTRIES = 10000  # Maximum cache entries (LRU eviction)

# Color definitions
COLOR_HIGH_SOC = "#00FF00"  # Green (>= 60%)
COLOR_MID_SOC = "#FF6600"  # Orange (21-59%)
COLOR_LOW_SOC = "#FF0000"  # Red (<= 20%)


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
        return COLOR_HIGH_SOC
    if pct > 20:
        return COLOR_MID_SOC
    return COLOR_LOW_SOC


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


def init_geocode_cache(cache_file: Path):
    """Initialize the geocode cache database."""
    conn = sqlite3.connect(cache_file)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS geocode_cache (
            lat REAL,
            lon REAL,
            location TEXT,
            timestamp INTEGER,
            PRIMARY KEY (lat, lon)
        )
    """)
    conn.commit()
    conn.close()


def get_cached_geocode(lat: float, lon: float, cache_file: Optional[Path]) -> Optional[str]:
    """Get cached reverse geocode result if available. Updates timestamp for LRU."""
    if cache_file is None:
        return None

    # Round coordinates for cache lookup
    lat_round = round(lat, GEOCODE_CACHE_PRECISION)
    lon_round = round(lon, GEOCODE_CACHE_PRECISION)

    try:
        conn = sqlite3.connect(cache_file)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT location FROM geocode_cache WHERE lat = ? AND lon = ?",
            (lat_round, lon_round)
        )
        result = cursor.fetchone()

        # Update timestamp on cache hit (LRU)
        if result:
            cursor.execute(
                "UPDATE geocode_cache SET timestamp = ? WHERE lat = ? AND lon = ?",
                (int(datetime.now().timestamp()), lat_round, lon_round)
            )
            conn.commit()

        conn.close()
        return result[0] if result else None
    except Exception:
        return None


def cache_geocode(lat: float, lon: float, location: str, cache_file: Optional[Path]):
    """Cache a reverse geocode result with LRU eviction."""
    if cache_file is None:
        return

    # Round coordinates for cache storage
    lat_round = round(lat, GEOCODE_CACHE_PRECISION)
    lon_round = round(lon, GEOCODE_CACHE_PRECISION)

    try:
        conn = sqlite3.connect(cache_file)
        cursor = conn.cursor()

        # Insert or update the entry
        cursor.execute(
            "INSERT OR REPLACE INTO geocode_cache (lat, lon, location, timestamp) VALUES (?, ?, ?, ?)",
            (lat_round, lon_round, location, int(datetime.now().timestamp()))
        )

        # Check cache size and evict oldest entries if needed
        cursor.execute("SELECT COUNT(*) FROM geocode_cache")
        count = cursor.fetchone()[0]

        if count > GEOCODE_CACHE_MAX_ENTRIES:
            # Delete oldest entries to get back under the limit
            entries_to_remove = count - GEOCODE_CACHE_MAX_ENTRIES
            cursor.execute(
                "DELETE FROM geocode_cache WHERE rowid IN "
                "(SELECT rowid FROM geocode_cache ORDER BY timestamp ASC LIMIT ?)",
                (entries_to_remove,)
            )

        conn.commit()
        conn.close()
        log(f"Cached location: {location} for ({lat_round}, {lon_round})")
    except Exception as e:
        log(f"Failed to cache geocode: {e}")


def reverse_geocode(lat: float, lon: float, cache_file: Optional[Path] = None) -> Optional[str]:
    """Get location name from coordinates using Nominatim API.

    Returns formatted location as "Street Name, Town" or None if request fails.
    Uses SQLite cache to reduce API calls if cache_file is provided.
    """
    # Check cache first
    cached = get_cached_geocode(lat, lon, cache_file)
    if cached is not None:
        log(f"Cache hit for ({lat:.4f}, {lon:.4f}): {cached}")
        return cached

    # Not in cache, fetch from API
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
        resp = requests.get(url, params=params, headers=headers, timeout=HTTP_TIMEOUT)
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

        location = ", ".join(parts) if parts else None

        # Cache the result if caching is enabled
        if location:
            cache_geocode(lat, lon, location, cache_file)

        return location
    except Exception:
        return None


def push_app(awtrix_url: str, name: str, soc: int, charging: bool, icon: str = None, location: str = None, duration: int = DURATION_AT_HOME):
    """Push a custom app to AWTRIX."""
    # Determine icon: use provided icon, or default to charging/battery icons
    if icon is None:
        icon = BATTERY_ICON_CHARGING if charging else soc_icon(soc)

    # Format text with optional location
    if location:
        text = f"{name} - {soc}% - {location}"
    else:
        text = f"{name} {soc}"

        # Add % unless it will cause a scroll
        if len(text) != 6:
            text += "%"

    payload = {
        "text": text,
        "icon": icon,
        "color": soc_color(soc),
        "progress": min(int(soc * 100 / SOC_DISPLAY_MAX), 100),
        "progressC": soc_color(soc),
        "progressBC": PROGRESS_BAR_COLOR_BG,
        "duration": duration,
        "textCase": DISPLAY_TEXT_CASE,
        "lifetime": DISPLAY_LIFETIME,
    }
    r = requests.post(f"{awtrix_url}?name={name.lower()}", json=payload, timeout=HTTP_TIMEOUT)
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
    parser.add_argument("--config", "-c", type=Path, help="Config file", required=True)
    parser.add_argument("--geocode-cache", "-g", type=Path, help="Geocode cache database file (optional)")
    args = parser.parse_args()

    # Initialize geocode cache if specified
    if args.geocode_cache:
        init_geocode_cache(args.geocode_cache)

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
                duration = DURATION_AT_HOME
                status_msg = f"charging={is_charging}"

                if home_lat is not None and home_lon is not None:
                    parking_data = await get_parking_position(audi, vin)
                    if parking_data is None:
                        # Car is driving - use current time
                        icon = BATTERY_ICON_DRIVING
                        time_suffix = datetime.now().strftime("%H%M")
                        location = time_suffix
                        duration = DURATION_AWAY
                        status_msg = f"driving - {time_suffix}"
                    else:
                        # Car is parked, check if away from home
                        car_lat = parking_data["data"]["lat"]
                        car_lon = parking_data["data"]["lon"]
                        distance = haversine_distance(home_lat, home_lon, car_lat, car_lon)

                        if distance > HOME_DISTANCE_THRESHOLD:
                            icon = BATTERY_ICON_PARKED
                            duration = DURATION_AWAY
                            # Get location name for display
                            location = reverse_geocode(car_lat, car_lon, args.geocode_cache)

                            # Get timestamp from parking data (API returns UTC)
                            parking_time_str = parking_data["data"]["carCapturedTimestamp"]
                            parking_time_utc = datetime.fromisoformat(parking_time_str.replace('Z', '+00:00'))
                            parking_time = parking_time_utc.astimezone()

                            # Format based on date (in local time)
                            now = datetime.now(parking_time.tzinfo)
                            days_diff = (now.date() - parking_time.date()).days

                            if days_diff == 0:
                                time_suffix = parking_time.strftime("%H%M")
                            elif days_diff == 1:
                                time_suffix = "yesterday"
                            else:
                                time_suffix = f"{days_diff} days ago"

                            if location:
                                location = f"{location} - {time_suffix}"
                                status_msg = f"parked - {location}"
                            else:
                                location = time_suffix
                                status_msg = f"parked {int(distance)}m from home - {time_suffix}"
                        else:
                            status_msg = f"at home, {status_msg}"

                push_app(awtrix_url, name, soc, is_charging, icon=icon, location=location, duration=duration)
                log(f"{name}: {soc}% {status_msg} -> AWTRIX OK")

            except Exception as e:
                log(f"{name} ({vin}): ERROR - {e}")
                sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
