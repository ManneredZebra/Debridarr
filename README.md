# Debridarr

A lightweight Windows application that monitors folders for magnet links from Sonarr and Radarr, processes them through Real Debrid, and downloads the content.

## Features

- Monitors `sonarr_magnets` and `radarr_magnets` folders for `.magnet` files
- Automatically processes magnet links through Real Debrid API
- Downloads completed files to `sonarr_completed` and `radarr_completed` folders
- Cleans up torrents from Real Debrid after download
- Comprehensive logging for troubleshooting
- Lightweight and responsive

## Setup

### 1. Get Real Debrid API Token

1. Go to [Real Debrid API](https://real-debrid.com/apitoken)
2. Generate your API token

### 2. Configuration

After running setup.bat, edit your Real Debrid API token in:
`%LOCALAPPDATA%\Debridarr\config.json`

```json
{
  "real_debrid_api_token": "YOUR_API_TOKEN_HERE"
}
```

### 3. Installation

1. Run the setup script:
```cmd
setup.bat
```

2. Start the application:
```cmd
Debridarr.exe
```

3. To uninstall:
```cmd
uninstall.bat
```

## Usage

1. Configure Sonarr to save magnet links as `.magnet` files in `%LOCALAPPDATA%\Debridarr\content\sonarr\magnets`
2. Configure Radarr to save magnet links as `.magnet` files in `%LOCALAPPDATA%\Debridarr\content\radarr\magnets`
3. The application will automatically process new magnet files and download content to the respective completed folders

## Folder Structure

```
Debridarr/
├── scripts/
│   └── app.py           # Main application
├── requirements.txt     # Python dependencies
├── setup.bat            # Setup script
└── uninstall.bat        # Uninstall script
```

## Logs

Logs are displayed in the console window and saved to `%LOCALAPPDATA%\Debridarr\logs\debridarr.log`.

## Troubleshooting

- Ensure your Real Debrid API token is valid
- Check that magnet files contain valid magnet links
- Monitor logs in the console window
- Verify folder permissions for the application
- Run setup.bat as Administrator if needed