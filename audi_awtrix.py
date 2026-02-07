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

# API constants
STATUS_URL = "https://emea.bff.cariad.digital/vehicle/v1/vehicles/{vin}/selectivestatus?jobs=charging"
X_CLIENT_ID = "77869e21-e30a-4a92-b016-48ab7d3db1d8"


def load_config(config_file: Path) -> dict:
    """Load configuration from JSON file.

    Expected format:
    {
      "username": "your.email@example.com",
      "password": "your_password",
      "awtrix_ip": "192.168.1.x",
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
            f"Create a JSON file with: username, password, awtrix_ip, and vehicles dict"
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


def push_app(awtrix_url: str, name: str, soc: int, charging: bool):
    """Push a custom app to AWTRIX."""
    payload = {
        "text": f"{name} {soc}%",
        "icon": BATTERY_ICON_CHARGING if charging else soc_icon(soc),
        "color": soc_color(soc),
        "progress": min(int(soc * 100 / 80), 100),
        "progressC": soc_color(soc),
        "progressBC": "#333333",
        "duration": 8,
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


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Push Audi SoC to AWTRIX 3")
    parser.add_argument("--config", "-c", type=Path, help=f"Config file", required=True)
    args = parser.parse_args()

    # Load configuration
    config = load_config(args.config)
    awtrix_url = f"http://{config['awtrix_ip']}/api/custom"

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

                push_app(awtrix_url, name, soc, is_charging)
                print(f"{name}: {soc}% charging={is_charging} -> AWTRIX OK")

            except Exception as e:
                print(f"{name} ({vin}): ERROR - {e}", file=sys.stderr)
                sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
