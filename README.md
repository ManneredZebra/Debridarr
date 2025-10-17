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

1. Add your Real Debrid API token in config.json:

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

## Usage

1. Configure Sonarr to save magnet links as `.magnet` files in the `content/sonarr_magnets` folder
2. Configure Radarr to save magnet links as `.magnet` files in the `content/radarr_magnets` folder
3. The application will automatically process new magnet files and download content to the respective completed folders

## Folder Structure

```
Debridarr/
├── scripts/
│   └── app.py           # Main application
├── requirements.txt     # Python dependencies
├── config.json          # Your configuration
├── setup.bat            # Setup script
├── Debridarr.exe        # Executable (created by setup)
└── content/
    ├── sonarr_magnets/  # Input folder for TV show magnets
    ├── radarr_magnets/  # Input folder for movie magnets
    ├── sonarr_completed/ # Output folder for TV shows
    └── radarr_completed/ # Output folder for movies
```

## Logs

Logs are displayed in the console window when running the application.

## Troubleshooting

- Ensure your Real Debrid API token is valid
- Check that magnet files contain valid magnet links
- Monitor logs in the console window
- Verify folder permissions for the application
- Run setup.bat as Administrator if needed