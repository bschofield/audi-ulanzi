# Audi AWTRIX Battery and Location Monitor

Display your Audi EV battery status on an AWTRIX 3 display (Ulanzi TC001) with location awareness.

![Example Display](awtrix.png)

## Features

- **Battery display**: Percentage, color-coded levels (green/orange/red), progress bar.
- **Location awareness**: Shows different icons and info when at home, driving, or parked away.
- **Reverse geocoding**: Displays street and town when parked away from home.
- **Multi-vehicle support**: Track multiple Audis with custom names.

## Requirements

- Ulanzi TC001 with AWTRIX 3 firmware.
- Audi EV with active Audi Connect.
- Python 3.7+ with `aiohttp` and `requests` packages.

## Installation

1. Install dependencies:

   ```bash
   sudo apt install python3-aiohttp python3-requests  # Ubuntu/Debian
   # or: pip install aiohttp requests
   ```

2. Download required AWTRIX icons (via AWTRIX web UI → Icons):
   - **Battery levels**: 6354, 6355, 6356, 6357, 6358
   - **Charging**: 21585
   - **Driving**: 1172
   - **Parked**: 70271

3. (Optional) Disable default AWTRIX apps:
   ```bash
   curl -X POST http://192.168.1.x/api/settings -H 'Content-Type: application/json' \
     -d '{"TIM": false, "DAT": false, "HUM": false, "TEMP": false, "BAT": false}'
   ```

## Configuration

Create `config.json`:

```json
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
```

- `home`: Your home coordinates (enables location features). Get from Google Maps or similar.
- `vehicles`: Map of VIN → display name.

## Usage

Run manually:

```bash
python3 audi_awtrix.py -c config.json
```

Run with geocode caching (recommended):

```bash
python3 audi_awtrix.py -c config.json -g ~/.audi_geocode_cache.db
```

Automate with cron (every 15 minutes, keeping last 10k lines of log):

```cron
*/15 * * * * /usr/bin/python3 /path/to/audi_awtrix.py -c /path/to/config.json -g ~/.audi_geocode_cache.db >> /tmp/audi_awtrix.log 2>&1; tail -10000 /tmp/audi_awtrix.log > /tmp/audi_awtrix.log.tmp && mv /tmp/audi_awtrix.log.tmp /tmp/audi_awtrix.log
```

## Display Behavior

### At Home

- Shows: `Q4 75%`.
- Icon: Battery level or charging icon.
- Duration: 5 seconds.

### Driving

- Shows: `Q4 - 75% - 1430` (current time).
- Icon: Car icon.
- Duration: 30 seconds.

### Parked Away

- Shows: `Q4 - 75% - High Street, Cambridge - 1430` (parked today).
- Shows: `Q4 - 75% - High Street, Cambridge - yesterday` (if parked yesterday).
- Shows: `Q4 - 75% - High Street, Cambridge - 3 days ago` (if older).
- Icon: Parking icon.
- Duration: 30 seconds.

## Configuration Constants

Edit the constants at the top of `audi_awtrix.py` to customize:

- `DURATION_AT_HOME` / `DURATION_AWAY`: Display durations.
- `HOME_DISTANCE_THRESHOLD`: Distance in meters to consider "at home" (default: 100m).
- `COLOR_HIGH_SOC` / `COLOR_MID_SOC` / `COLOR_LOW_SOC`: Status colors.
- `SOC_DISPLAY_MAX`: SoC percentage to show as "full" on progress bar (default: 80%).

## Troubleshooting

**Authentication fails**: Verify credentials and active Audi Connect subscription.

**Display not updating**: Check AWTRIX IP, network connectivity, and downloaded icons.

**Script errors**: Check `/tmp/audi_awtrix.log` and verify VINs in config.

## Caching

- **Authentication tokens**: Auto-cached in `~/.audi_tokens.json` to avoid repeated logins.
- **Reverse geocoding**: Optional SQLite LRU cache (max 10k entries) for location lookups. Enable with `-g` / `--geocode-cache` option to reduce API calls to OpenStreetMap Nominatim. Coordinates are rounded to ~11m precision for cache hits. Least recently used entries are automatically evicted when the cache is full.

## Security

- Keep config file secure (contains credentials).
- Tokens are auto-cached in `~/.audi_tokens.json` with restricted permissions.

## Credits

Built with Claude. Audi Connect API client adapted from [audiconnect](https://github.com/arjenvrh/audi_connect_ha).

## License

MIT.
