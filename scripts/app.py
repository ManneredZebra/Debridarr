#!/usr/bin/env python3
import os
import json
import time
import logging
import requests
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class MagnetHandler(FileSystemEventHandler):
    def __init__(self, config, completed_folder):
        self.config = config
        self.completed_folder = completed_folder
        self.api_token = config['real_debrid_api_token']
        
    def on_created(self, event):
        if event.is_dir or not event.src_path.endswith('.magnet'):
            return
        
        logging.info(f"New magnet file detected: {event.src_path}")
        self.process_magnet(event.src_path)
    
    def process_magnet(self, file_path):
        try:
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
            os.remove(file_path)
            
        except Exception as e:
            logging.error(f"Error processing {file_path}: {e}")
    
    def add_torrent(self, magnet_link):
        url = "https://api.real-debrid.com/rest/1.0/torrents/addMagnet"
        headers = {"Authorization": f"Bearer {self.api_token}"}
        data = {"magnet": magnet_link}
        
        response = requests.post(url, headers=headers, data=data)
        if response.status_code == 201:
            torrent_id = response.json()['id']
            logging.info(f"Torrent added: {torrent_id}")
            return torrent_id
        
        logging.error(f"Failed to add torrent: {response.text}")
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
        response = requests.get(download_url, stream=True)
        filename = response.headers.get('content-disposition', '').split('filename=')[-1].strip('"')
        if not filename:
            filename = download_url.split('/')[-1]
        
        os.makedirs(self.completed_folder, exist_ok=True)
        output_path = os.path.join(self.completed_folder, filename)
        
        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        logging.info(f"Downloaded: {output_path}")
    
    def delete_torrent(self, torrent_id):
        url = f"https://api.real-debrid.com/rest/1.0/torrents/delete/{torrent_id}"
        headers = {"Authorization": f"Bearer {self.api_token}"}
        requests.delete(url, headers=headers)

def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    base_dir = os.path.dirname(os.path.dirname(__file__))
    config_path = os.path.join(base_dir, 'config.json')
    with open(config_path, 'r') as f:
        config = json.load(f)
    
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
    
    observer = Observer()
    observer.schedule(MagnetHandler(config, sonarr_completed), sonarr_magnets, recursive=False)
    observer.schedule(MagnetHandler(config, radarr_completed), radarr_magnets, recursive=False)
    
    observer.start()
    logging.info("Debridarr started - monitoring for magnet files")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

if __name__ == "__main__":
    main()