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

### Configure Sonarr

1. Go to Settings > Download Clients
2. Add a new Download Client with type "Torrent Blackhole"
3. Set the following settings:
   - **Torrent Folder**: `%LOCALAPPDATA%\Debridarr\content\sonarr\magnets`
   - **Watch Folder**: `%LOCALAPPDATA%\Debridarr\content\sonarr\completed_downloads`
   - **Save Magnet Links**: ✓ (checked)
   - **Magnet File Extension**: `.magnet`
   - **Read Only**: ☐ (unchecked)

### Configure Radarr

1. Go to Settings > Download Clients
2. Add a new Download Client with type "Torrent Blackhole"
3. Set the following settings:
   - **Torrent Folder**: `%LOCALAPPDATA%\Debridarr\content\radarr\magnets`
   - **Watch Folder**: `%LOCALAPPDATA%\Debridarr\content\radarr\completed_downloads`
   - **Save Magnet Links**: ✓ (checked)
   - **Magnet File Extension**: `.magnet`
   - **Read Only**: ☐ (unchecked)

### How it Works

1. Sonarr/Radarr saves magnet links as `.magnet` files in their respective magnet folders
2. Debridarr automatically processes new magnet files through Real Debrid
3. Downloaded content appears in the completed_downloads folders for Sonarr/Radarr to import

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
- Monitor logs at `%LOCALAPPDATA%\Debridarr\logs\debridarr.log`
- Verify folder permissions for the application
- Run setup.bat as Administrator if needed
- Ensure Sonarr/Radarr can access the configured folder paths