# Debridarr

A lightweight Windows application that monitors folders for magnet links from Sonarr and Radarr, processes them through Real Debrid, and downloads the content.

## Features

- Web-based UI for monitoring and management
- Monitors folders for `.magnet` files from Sonarr, Radarr, and other clients
- Automatically processes magnet links through Real Debrid API
- Real-time download progress tracking with individual file progress
- Downloads completed files to configured folders
- Cleans up torrents from Real Debrid after download
- Comprehensive logging for troubleshooting
- System tray application - runs in background
- Lightweight and responsive

## Setup

### 1. Get Real Debrid API Token

1. Go to [Real Debrid API](https://real-debrid.com/apitoken)
2. Generate your API token

### 2. Configuration

After running setup.bat, the Web UI will automatically open at http://127.0.0.1:3636

#### Option A: Configure via Web UI (Recommended)

1. Click on the **Settings** tab in the Web UI
2. Enter your Real Debrid API token (will be masked after saving)
3. Modify folder paths for Sonarr/Radarr or add new download clients
4. Click **Save Configuration** - changes apply immediately

#### Option B: Edit Config File Directly

Alternatively, you can edit the config file directly at:
`C:\ProgramData\Debridarr\config.yaml`

```yaml
real_debrid_api_token: "YOUR_API_TOKEN_HERE"

download_clients:
  sonarr:
    magnets_folder: "C:/ProgramData/Debridarr/sonarr/magnets"
    in_progress_folder: "C:/ProgramData/Debridarr/sonarr/in_progress"
    completed_magnets_folder: "C:/ProgramData/Debridarr/sonarr/completed_magnets"
    completed_downloads_folder: "C:/ProgramData/Debridarr/sonarr/completed_downloads"
  radarr:
    magnets_folder: "C:/ProgramData/Debridarr/radarr/magnets"
    in_progress_folder: "C:/ProgramData/Debridarr/radarr/in_progress"
    completed_magnets_folder: "C:/ProgramData/Debridarr/radarr/completed_magnets"
    completed_downloads_folder: "C:/ProgramData/Debridarr/radarr/completed_downloads"
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

## Web UI

The Web UI is accessible at **http://127.0.0.1:3636** and provides:

### Overview Tab
- System health monitoring with automatic checks
- View active download counts for each client
- See folder file counts (Magnets, In Progress, Completed)
- **View Details** button to jump to active downloads when downloads are active
- **Clean Up** button to remove leftover files not actively downloading

### Active Downloads Tab
- Badge showing total active download count
- Real-time progress tracking with dual progress bars:
  - Real-Debrid Cache progress
  - Files Complete progress
- Individual file progress for multi-file torrents with clean filenames
- **Abort** button at top of each download to immediately cancel and stop all queued files

### History Tab
- View completed magnet files
- **Retry** button to reprocess failed downloads

### Completed Downloads Tab
- View all downloaded video files
- **Delete** button to remove files

### Logs Tab
- View last 100 log entries
- Real-time log monitoring

### Settings Tab
- Warning badge (⚠) appears when configuration issues detected
- Configure Real Debrid API token (masked after saving)
- Manage download clients (Sonarr, Radarr, etc.)
- Add/remove custom download clients
- Edit folder paths for each client
- Changes apply immediately without restart

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

#### Via Web UI:
1. Go to the **Settings** tab
2. Click **Add New Client**
3. Enter client name and configure folder paths
4. Click **Save Configuration**

#### Via Config File:
Add them to your config.yaml:

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

Logs can be viewed in the **Logs** tab of the Web UI or directly at:
`C:\ProgramData\Debridarr\logs\debridarr.log`

## System Health Monitoring

Debridarr automatically monitors system health and displays warnings on the Overview tab when issues are detected:

- **API Connectivity**: Validates Real-Debrid API is reachable with your token
- **Directory Access**: Checks all configured folders exist and are writable
- **Automatic Checks**: Runs on startup, every 10 minutes, and immediately after settings changes
- **Warning Badge**: Yellow triangle (⚠) appears on Settings tab when issues need attention

Each warning includes specific guidance on how to resolve the issue.

## Troubleshooting

- Access the Web UI at http://127.0.0.1:3636 to monitor downloads
- Check the **Overview** tab for system health warnings with solutions
- Review the **Logs** tab in the Web UI for detailed error messages
- Ensure your Real Debrid API token is valid (Settings tab will show warning)
- Use the **Clean Up** button to remove leftover files from failed downloads
- Verify folder permissions for `C:\ProgramData\Debridarr`
- Run setup.bat as Administrator if needed
- Ensure Sonarr/Radarr can write to the configured magnet folders
- User data in `C:\ProgramData\Debridarr` is preserved during uninstall