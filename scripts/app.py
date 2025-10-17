#!/usr/bin/env python3
import os
import json
import time
import logging
import requests
import sys
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class MagnetHandler(FileSystemEventHandler):
    def __init__(self, config, completed_folder, magnets_folder):
        self.config = config
        self.completed_folder = completed_folder
        self.magnets_folder = magnets_folder
        self.api_token = config['real_debrid_api_token'].strip()
        
    def on_created(self, event):
        if hasattr(event, 'is_directory') and event.is_directory:
            return
        if not event.src_path.endswith('.magnet'):
            return
        
        logging.info(f"New magnet file detected: {event.src_path}")
        self.process_magnet(event.src_path)
    
    def process_magnet(self, file_path):
        try:
            # Wait a moment for file to be fully written
            time.sleep(1)
            
            with open(file_path, 'r') as f:
                magnet_link = f.read().strip()
            
            torrent_id = self.add_torrent(magnet_link)
            if not torrent_id:
                return
            
            self.select_files(torrent_id)
            
            download_link = self.wait_for_torrent(torrent_id)
            if not download_link:
                return
            
            self.download_file(download_link)
            
            self.delete_torrent(torrent_id)
            
            # Move magnet file to completed folder
            completed_magnets = os.path.join(os.path.dirname(self.magnets_folder), 'completed_magnets')
            os.makedirs(completed_magnets, exist_ok=True)
            filename = os.path.basename(file_path)
            
            # Try multiple times to move the file
            for attempt in range(3):
                try:
                    os.rename(file_path, os.path.join(completed_magnets, filename))
                    logging.info(f"Moved magnet file to completed: {filename}")
                    break
                except PermissionError:
                    if attempt < 2:
                        time.sleep(2)
                    else:
                        logging.error(f"Failed to move magnet file after 3 attempts: {filename}")
            
        except PermissionError as e:
            logging.error(f"Permission denied accessing {file_path}: {e}")
        except Exception as e:
            logging.error(f"Error processing {file_path}: {e}")
    
    def add_torrent(self, magnet_link):
        try:
            url = "https://api.real-debrid.com/rest/1.0/torrents/addMagnet"
            headers = {"Authorization": f"Bearer {self.api_token}"}
            data = {"magnet": magnet_link}
            
            response = requests.post(url, headers=headers, data=data, timeout=30)
            if response.status_code == 201:
                torrent_id = response.json()['id']
                logging.info(f"Torrent added: {torrent_id}")
                return torrent_id
            
            logging.error(f"Failed to add torrent: {response.text}")
            return None
        except requests.RequestException as e:
            logging.error(f"Network error adding torrent: {e}")
            return None
    
    def select_files(self, torrent_id):
        url = f"https://api.real-debrid.com/rest/1.0/torrents/selectFiles/{torrent_id}"
        headers = {"Authorization": f"Bearer {self.api_token}"}
        data = {"files": "all"}
        
        requests.post(url, headers=headers, data=data)
    
    def wait_for_torrent(self, torrent_id):
        url = f"https://api.real-debrid.com/rest/1.0/torrents/info/{torrent_id}"
        headers = {"Authorization": f"Bearer {self.api_token}"}
        
        for _ in range(60):
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                if data['status'] == 'downloaded':
                    link = data['links'][0]
                    return self.unrestrict_link(link)
            time.sleep(10)
        
        logging.error(f"Torrent {torrent_id} not ready after 10 minutes")
        return None
    
    def unrestrict_link(self, link):
        url = "https://api.real-debrid.com/rest/1.0/unrestrict/link"
        headers = {"Authorization": f"Bearer {self.api_token}"}
        data = {"link": link}
        
        response = requests.post(url, headers=headers, data=data)
        if response.status_code == 200:
            return response.json()['download']
        return None
    
    def download_file(self, download_url):
        try:
            response = requests.get(download_url, stream=True, timeout=30)
            response.raise_for_status()
            
            filename = response.headers.get('content-disposition', '').split('filename=')[-1].strip('"')
            if not filename:
                filename = download_url.split('/')[-1]
            
            os.makedirs(self.completed_folder, exist_ok=True)
            output_path = os.path.join(self.completed_folder, filename)
            
            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            logging.info(f"Downloaded: {output_path}")
        except requests.RequestException as e:
            logging.error(f"Download failed: {e}")
        except IOError as e:
            logging.error(f"File write error: {e}")
    
    def delete_torrent(self, torrent_id):
        url = f"https://api.real-debrid.com/rest/1.0/torrents/delete/{torrent_id}"
        headers = {"Authorization": f"Bearer {self.api_token}"}
        requests.delete(url, headers=headers)

def process_existing_magnets(magnets_folder, handler):
    """Process any existing magnet files in the folder"""
    try:
        for filename in os.listdir(magnets_folder):
            if filename.endswith('.magnet'):
                file_path = os.path.join(magnets_folder, filename)
                # Check if file is accessible
                try:
                    with open(file_path, 'r') as f:
                        pass  # Just test if we can open it
                    logging.info(f"Processing existing magnet: {file_path}")
                    handler.process_magnet(file_path)
                except PermissionError:
                    logging.warning(f"Skipping locked file: {filename}")
    except Exception as e:
        logging.error(f"Error scanning magnet folder: {e}")

def main():
    # Use LOCALAPPDATA for all user data
    base_dir = os.path.join(os.environ['LOCALAPPDATA'], 'Debridarr')
    
    # Setup logging
    logs_dir = os.path.join(base_dir, 'logs')
    os.makedirs(logs_dir, exist_ok=True)
    log_file = os.path.join(logs_dir, 'debridarr.log')
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file)
        ]
    )
    
    try:
        config_path = os.path.join(base_dir, 'config.json')
        with open(config_path, 'r') as f:
            config = json.load(f)
    except FileNotFoundError:
        logging.error("config.json not found. Please run setup.bat first.")
        return
    except json.JSONDecodeError:
        logging.error("Invalid JSON in config.json. Please check the file format.")
        return
    
    # Define fixed folder paths
    content_dir = os.path.join(base_dir, 'content')
    sonarr_magnets = os.path.join(content_dir, 'sonarr_magnets')
    sonarr_completed = os.path.join(content_dir, 'sonarr_completed')
    radarr_magnets = os.path.join(content_dir, 'radarr_magnets')
    radarr_completed = os.path.join(content_dir, 'radarr_completed')
    
    os.makedirs(content_dir, exist_ok=True)
    
    os.makedirs(sonarr_magnets, exist_ok=True)
    os.makedirs(sonarr_completed, exist_ok=True)
    os.makedirs(radarr_magnets, exist_ok=True)
    os.makedirs(radarr_completed, exist_ok=True)
    
    # Create handlers
    sonarr_handler = MagnetHandler(config, sonarr_completed, sonarr_magnets)
    radarr_handler = MagnetHandler(config, radarr_completed, radarr_magnets)
    
    # Process existing magnet files
    process_existing_magnets(sonarr_magnets, sonarr_handler)
    process_existing_magnets(radarr_magnets, radarr_handler)
    
    observer = Observer()
    observer.schedule(sonarr_handler, sonarr_magnets, recursive=False)
    observer.schedule(radarr_handler, radarr_magnets, recursive=False)
    
    try:
        observer.start()
        logging.info("Debridarr started - monitoring for magnet files")
        
        while True:
            time.sleep(30)
            # Retry processing any remaining magnet files
            process_existing_magnets(sonarr_magnets, sonarr_handler)
            process_existing_magnets(radarr_magnets, radarr_handler)
    except KeyboardInterrupt:
        logging.info("Shutting down...")
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
    finally:
        observer.stop()
        observer.join()

if __name__ == "__main__":
    main()