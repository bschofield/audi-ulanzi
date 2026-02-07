# Audi AWTRIX Battery Monitor

Display your Audi electric vehicle's battery status on an AWTRIX 3 display (Ulanzi TC001).

This project polls your Audi's State of Charge (SoC) from the Audi Connect API and displays it on your AWTRIX clock with custom battery icons and color-coded status.

This was thrown together using Claude.

![Example Display](awtrix.png)

## Features

- Battery percentage display
- Visual battery level icons / charging status
- Color-coded battery levels (green / orange / red)
- Progress bar showing charge level
- Support for multiple vehicles
- Automatic token management and refresh

## Requirements

### Hardware

- **Ulanzi TC001** (AWTRIX 3 compatible display)
- One or more **Audi electric vehicles** with Audi Connect

### Software

- Python 3.7+
- Required Python packages:

  **Ubuntu/Debian:**

  ```bash
  sudo apt install python3-aiohttp python3-requests
  ```

  **Other systems (via pip):**

  ```bash
  pip install aiohttp requests
  ```

### Audi Connect

- Active Audi Connect subscription
- Valid Audi Connect account credentials

## Installation

1. Clone this repository:

   ```bash
   git clone https://github.com/yourusername/audi-awtrix.git
   cd audi-awtrix
   ```

2. Install dependencies:

   **Ubuntu/Debian:**

   ```bash
   sudo apt install python3-aiohttp python3-requests
   ```

   **Other systems (via pip):**

   ```bash
   pip install aiohttp requests
   ```

3. Create a configuration file e.g. `audi_awtrix_config.json`:

   ```json
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
   ```

   Replace:
   - `username` and `password` with your Audi Connect credentials
   - `awtrix_ip` with your Ulanzi TC001's IP address
   - Vehicle VINs and friendly names in the `vehicles` section

## AWTRIX Setup

### Download Battery Icons

The script uses specific battery icons that need to be downloaded to your TC001 using the AWTRIX web UI ("Icons" section):

- 6354
- 6355
- 6356
- 6357
- 6538
- 21585

### Disable Default AWTRIX Apps

To prevent the default AWTRIX apps from interfering with your custom battery display:

```bash
curl -X POST http://192.168.1.x/api/settings -H 'Content-Type: application/json' -d '{"TIM": false, "DAT": false, "HUM": false, "TEMP": false, "BAT": false}'
```

This disables the built-in Time, Date, Humidity, Temperature, and Battery apps.

## Usage

Run the script manually:

```bash
python3 audi_awtrix.py -c audi_awtrix_config.json
```

### Automated Updates with Cron

To update your display every 15 minutes, add this to your crontab (`crontab -e`):

```cron
*/15 * * * * /usr/bin/python3 /path/to/audi_awtrix.py --config /path/to/audi_awtrix_config.json > /tmp/audi_awtrix.log 2>&1
```

Replace `/path/to/` with your actual paths.

## How It Works

1. **Authentication**: The script uses OAuth2 PKCE flow to authenticate with Audi Connect
2. **Token Management**: Access tokens are cached in `~/.audi_tokens.json` and automatically refreshed
3. **Data Polling**: Vehicle charging status is fetched from the Audi/Cariad API
4. **Display Update**: Battery data is pushed to AWTRIX as a custom app with:
   - Vehicle name and percentage
   - Appropriate battery/charging icon
   - Color-coded text and progress bar
   - 30-minute display lifetime

## Display Colors

- 🟢 **Green** (>40%): Good charge level
- 🟠 **Orange** (20-40%): Medium charge level
- 🔴 **Red** (<20%): Low battery

## Files

- `audi_awtrix.py` - Main script
- `audi_connect.py` - Audi Connect API client library
- `audi_awtrix_config.json` - Configuration file (not included, create your own)
- `~/.audi_tokens.json` - Cached authentication tokens (auto-generated)

## Troubleshooting

**Authentication fails:**

- Verify your Audi Connect credentials
- Ensure your Audi Connect subscription is active
- Check that you can log in to the myAudi app

**Display not updating:**

- Verify AWTRIX IP address in config
- Check network connectivity to TC001
- Ensure icons are downloaded (see AWTRIX Setup)

**Script errors:**

- Check logs: `cat /tmp/audi_awtrix.log`
- Verify VINs in config file match your vehicles
- Ensure Python dependencies are installed

## Security Notes

- Keep your config file secure (contains credentials)
- Tokens are stored in your home directory with user-only permissions

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Credits

Audi Connect API client extracted from the [audiconnect](https://github.com/arjenvrh/audi_connect_ha) Home Assistant integration and adapted for standalone use.

Errors in OAuth flow fixed by Claude.
