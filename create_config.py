import os
import yaml

# All data in ProgramData for write access and preservation
content_dir = 'C:\\ProgramData\\Debridarr'

config = {
    'real_debrid_api_token': 'YOUR_API_TOKEN_HERE',
    'download_clients': {
        'sonarr': {
            'magnets_folder': os.path.join(content_dir, 'sonarr', 'magnets'),
            'in_progress_folder': os.path.join(content_dir, 'sonarr', 'in_progress'),
            'completed_magnets_folder': os.path.join(content_dir, 'sonarr', 'completed_magnets'),
            'completed_downloads_folder': os.path.join(content_dir, 'sonarr', 'completed_downloads')
        },
        'radarr': {
            'magnets_folder': os.path.join(content_dir, 'radarr', 'magnets'),
            'in_progress_folder': os.path.join(content_dir, 'radarr', 'in_progress'),
            'completed_magnets_folder': os.path.join(content_dir, 'radarr', 'completed_magnets'),
            'completed_downloads_folder': os.path.join(content_dir, 'radarr', 'completed_downloads')
        }
    }
}

config_path = os.path.join(content_dir, 'config.yaml')
with open(config_path, 'w') as f:
    f.write("# Debridarr Configuration\n")
    f.write("# Comment out any clients you don't use\n\n")
    yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    f.write("\n# Example: Add custom download client\n")
    f.write("# lidarr:\n")
    f.write("#   magnets_folder: \"D:\\Downloads\\Lidarr\\Magnets\"\n")
    f.write("#   in_progress_folder: \"D:\\Downloads\\Lidarr\\InProgress\"\n")
    f.write("#   completed_magnets_folder: \"D:\\Downloads\\Lidarr\\CompletedMagnets\"\n")
    f.write("#   completed_downloads_folder: \"D:\\Downloads\\Lidarr\\Completed\"\n")

print("Config file created successfully")