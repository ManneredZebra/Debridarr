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
`%LOCALAPPDATA%\Debridarr\config.yaml`

```yaml
real_debrid_api_token: "YOUR_API_TOKEN_HERE"

download_clients:
  sonarr:
    magnets_folder: "C:/Users/YourUser/AppData/Local/Debridarr/content/sonarr/magnets"
    in_progress_folder: "C:/Users/YourUser/AppData/Local/Debridarr/content/sonarr/in_progress"
    completed_magnets_folder: "C:/Users/YourUser/AppData/Local/Debridarr/content/sonarr/completed_magnets"
    completed_downloads_folder: "C:/Users/YourUser/AppData/Local/Debridarr/content/sonarr/completed_downloads"
  radarr:
    magnets_folder: "C:/Users/YourUser/AppData/Local/Debridarr/content/radarr/magnets"
    in_progress_folder: "C:/Users/YourUser/AppData/Local/Debridarr/content/radarr/in_progress"
    completed_magnets_folder: "C:/Users/YourUser/AppData/Local/Debridarr/content/radarr/completed_magnets"
    completed_downloads_folder: "C:/Users/YourUser/AppData/Local/Debridarr/content/radarr/completed_downloads"
```

### 3. Installation

1. Run the setup script as Administrator:
```cmd
setup.bat
```

2. The application will be installed to `C:\Program Files\Debridarr\bin\Debridarr.exe`

3. To uninstall:
```cmd
uninstall.bat
```

## Usage

### Configure Sonarr

1. Go to Settings > Download Clients
2. Add a new Download Client with type "Torrent Blackhole"
3. Set the following settings:
   - **Torrent Folder**: Use the `magnets_folder` path from your config.yaml
   - **Watch Folder**: Use the `completed_downloads_folder` path from your config.yaml
   - **Save Magnet Links**: ✓ (checked)
   - **Magnet File Extension**: `.magnet`
   - **Read Only**: ☐ (unchecked)

### Configure Radarr

1. Go to Settings > Download Clients
2. Add a new Download Client with type "Torrent Blackhole"
3. Set the following settings:
   - **Torrent Folder**: Use the `magnets_folder` path from your config.yaml
   - **Watch Folder**: Use the `completed_downloads_folder` path from your config.yaml
   - **Save Magnet Links**: ✓ (checked)
   - **Magnet File Extension**: `.magnet`
   - **Read Only**: ☐ (unchecked)

### Add Custom Download Clients

To add additional download clients (like Lidarr), add them to your config.yaml:

```yaml
download_clients:
  # Existing clients...
  lidarr:
    magnets_folder: "D:/Downloads/Lidarr/Magnets"
    in_progress_folder: "D:/Downloads/Lidarr/InProgress"
    completed_magnets_folder: "D:/Downloads/Lidarr/CompletedMagnets"
    completed_downloads_folder: "D:/Downloads/Lidarr/Completed"
```

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

Logs are saved to `%LOCALAPPDATA%\Debridarr\logs\debridarr.log`.

## Troubleshooting

- Ensure your Real Debrid API token is valid
- Check that magnet files contain valid magnet links
- Monitor logs at `%LOCALAPPDATA%\Debridarr\logs\debridarr.log`
- Check config.yaml format is valid YAML
- Verify folder permissions for the application
- Run setup.bat as Administrator if needed
- Ensure Sonarr/Radarr can access the configured folder paths