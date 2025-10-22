#!/usr/bin/env python3
import os
import json
import time
import logging
import requests
import sys
import threading
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from concurrent.futures import ThreadPoolExecutor

class MagnetHandler(FileSystemEventHandler):
    def __init__(self, config_path, completed_folder, magnets_folder, completed_magnets_folder):
        self.config_path = config_path
        self.completed_folder = completed_folder
        self.magnets_folder = magnets_folder
        self.completed_magnets_folder = completed_magnets_folder
        self.executor = ThreadPoolExecutor(max_workers=3)
        self.processing_files = set()
        
    def on_created(self, event):
        if hasattr(event, 'is_directory') and event.is_directory:
            return
        if not event.src_path.endswith('.magnet'):
            return
        
        if event.src_path in self.processing_files:
            logging.debug(f"Already processing: {event.src_path}")
            return
            
        if len(self.processing_files) >= 3:
            logging.info(f"Maximum concurrent downloads reached (3), queuing: {event.src_path}")
            return
            
        logging.info(f"New magnet file detected: {event.src_path}")
        self.processing_files.add(event.src_path)
        self.executor.submit(self._process_magnet_wrapper, event.src_path)
    
    def _process_magnet_wrapper(self, file_path):
        try:
            self.process_magnet(file_path)
        finally:
            self.processing_files.discard(file_path)
    
    def process_magnet(self, file_path):
        try:
            # Wait for file to be fully written and stable
            time.sleep(3)
            
            # Check if file still exists (might have been processed by another thread)
            if not os.path.exists(file_path):
                logging.info(f"File no longer exists, skipping: {file_path}")
                return
            
            with open(file_path, 'r') as f:
                magnet_link = f.read().strip()
            
            torrent_id = self.add_torrent(magnet_link)
            if not torrent_id:
                return
            
            self.select_files(torrent_id)
            
            results = self.wait_for_torrent(torrent_id)
            if not results:
                return
            
            # Download all files from the torrent
            for download_link, filename in results:
                if download_link and filename:
                    self.download_file(download_link, filename)
            
            self.delete_torrent(torrent_id)
            
            # Move magnet file to completed folder
            os.makedirs(self.completed_magnets_folder, exist_ok=True)
            filename = os.path.basename(file_path)
            
            # Try multiple times to move the file
            for attempt in range(5):
                try:
                    # Check if file still exists before moving
                    if not os.path.exists(file_path):
                        logging.info(f"Magnet file already processed: {filename}")
                        break
                    os.rename(file_path, os.path.join(self.completed_magnets_folder, filename))
                    logging.info(f"Moved magnet file to completed: {filename}")
                    break
                except (OSError, IOError) as e:
                    if attempt < 4:
                        logging.warning(f"Magnet move attempt {attempt + 1} failed, retrying in 2 seconds: {e}")
                        time.sleep(2)
                    else:
                        logging.error(f"Failed to move magnet file after 5 attempts: {filename}")
            
        except PermissionError as e:
            logging.error(f"Permission denied accessing {file_path}: {e}")
        except Exception as e:
            logging.error(f"Error processing {file_path}: {e}")
    
    def get_api_token(self):
        try:
            with open(self.config_path, 'r') as f:
                config = json.load(f)
            return config['real_debrid_api_token'].strip().strip('"').strip("'")
        except Exception as e:
            logging.error(f"Error reading config: {e}")
            return None
    
    def add_torrent(self, magnet_link):
        try:
            api_token = self.get_api_token()
            if not api_token:
                logging.error("No API token available")
                return None
                
            url = "https://api.real-debrid.com/rest/1.0/torrents/addMagnet"
            headers = {
                "Authorization": f"Bearer {api_token}",
                "Content-Type": "application/x-www-form-urlencoded"
            }
            data = {"magnet": magnet_link}
            
            logging.debug(f"Adding torrent to Real Debrid...")
            response = requests.post(url, headers=headers, data=data, timeout=30)
            
            if response.status_code == 201:
                torrent_id = response.json()['id']
                logging.info(f"Torrent added successfully: {torrent_id}")
                return torrent_id
            
            logging.error(f"Failed to add torrent (status {response.status_code}): {response.text}")
            return None
        except requests.RequestException as e:
            logging.error(f"Network error adding torrent: {e}")
            return None
    
    def select_files(self, torrent_id):
        api_token = self.get_api_token()
        if not api_token:
            logging.error("No API token available for file selection")
            return
            
        url = f"https://api.real-debrid.com/rest/1.0/torrents/selectFiles/{torrent_id}"
        headers = {"Authorization": f"Bearer {api_token}"}
        data = {"files": "all"}
        
        logging.debug(f"Selecting all files for torrent: {torrent_id}")
        response = requests.post(url, headers=headers, data=data)
        if response.status_code == 204:
            logging.info(f"Files selected for torrent: {torrent_id}")
        else:
            logging.warning(f"File selection response: {response.status_code}")
    
    def wait_for_torrent(self, torrent_id):
        api_token = self.get_api_token()
        if not api_token:
            logging.error("No API token available for torrent status check")
            return None
            
        url = f"https://api.real-debrid.com/rest/1.0/torrents/info/{torrent_id}"
        headers = {"Authorization": f"Bearer {api_token}"}
        
        logging.info(f"Waiting for torrent to complete: {torrent_id}")
        for attempt in range(60):
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                status = data.get('status', 'unknown')
                progress = data.get('progress', 0)
                
                if attempt % 6 == 0:  # Log every minute
                    logging.info(f"Torrent {torrent_id} status: {status}, progress: {progress}%")
                
                if status == 'downloaded':
                    logging.info(f"Torrent {torrent_id} completed successfully")
                    # Return all links for multi-file torrents
                    links = data.get('links', [])
                    return [(self.unrestrict_link(link), self.get_filename_from_link(link)) for link in links]
            time.sleep(10)
        
        logging.error(f"Torrent {torrent_id} not ready after 10 minutes")
        return None
    
    def get_filename_from_link(self, link):
        """Extract filename from Real Debrid link"""
        try:
            # Make a HEAD request to get filename from headers
            response = requests.head(link, timeout=10)
            cd_header = response.headers.get('content-disposition', '')
            if 'filename=' in cd_header:
                filename = cd_header.split('filename=')[-1].strip('"').strip("'")
                return self.sanitize_filename(filename)
        except:
            pass
        
        # Fallback to URL parsing
        filename = link.split('/')[-1].split('?')[0]
        return self.sanitize_filename(filename) if filename else 'download'
    
    def sanitize_filename(self, filename):
        """Ensure filename has proper extension and length"""
        if not filename:
            return 'download'
            
        # Get file extension
        name, ext = os.path.splitext(filename)
        
        # Ensure we have an extension for video files
        if not ext and any(vid_ext in filename.lower() for vid_ext in ['.mkv', '.mp4', '.avi', '.mov', '.wmv']):
            for vid_ext in ['.mkv', '.mp4', '.avi', '.mov', '.wmv']:
                if vid_ext in filename.lower():
                    ext = vid_ext
                    name = filename.lower().split(vid_ext)[0]
                    break
        
        # Limit filename length while preserving extension
        max_length = 200  # Windows path limit consideration
        if len(filename) > max_length:
            name = name[:max_length - len(ext)]
            filename = name + ext
            
        return filename
    
    def unrestrict_link(self, link):
        api_token = self.get_api_token()
        if not api_token:
            return None
            
        url = "https://api.real-debrid.com/rest/1.0/unrestrict/link"
        headers = {"Authorization": f"Bearer {api_token}"}
        data = {"link": link}
        
        response = requests.post(url, headers=headers, data=data)
        if response.status_code == 200:
            return response.json()['download']
        return None
    
    def download_file(self, download_url, rd_filename=None):
        try:
            logging.info(f"Starting download from: {download_url}")
            
            # Get filename from URL if rd_filename is not a proper filename
            if not rd_filename or not any(ext in rd_filename.lower() for ext in ['.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v', '.flv', '.webm']):
                # Extract filename from URL
                url_filename = download_url.split('/')[-1].split('?')[0]
                if '%' in url_filename:
                    import urllib.parse
                    url_filename = urllib.parse.unquote(url_filename)
                
                # Use URL filename if it's a video file
                if any(ext in url_filename.lower() for ext in ['.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v', '.flv', '.webm']):
                    rd_filename = url_filename
                else:
                    logging.info(f"Skipping non-video file: {rd_filename or url_filename}")
                    return
                
            response = requests.get(download_url, stream=True, timeout=30)
            response.raise_for_status()
            
            # Use provided filename or extract from headers/URL
            filename = rd_filename
            if not filename:
                cd_header = response.headers.get('content-disposition', '')
                if 'filename=' in cd_header:
                    filename = cd_header.split('filename=')[-1].strip('"').strip("'")
            if not filename:
                filename = download_url.split('/')[-1].split('?')[0]
            if not filename:
                filename = 'download'
            
            # Sanitize filename
            filename = self.sanitize_filename(filename)
            
            # Download to in_progress folder first
            in_progress_folder = os.path.join(os.path.dirname(self.completed_folder), 'in_progress')
            os.makedirs(in_progress_folder, exist_ok=True)
            temp_path = os.path.join(in_progress_folder, filename)
            
            logging.info(f"Downloading to temporary location: {temp_path}")
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            with open(temp_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0 and downloaded % (1024*1024*10) == 0:  # Log every 10MB
                        progress = (downloaded / total_size) * 100
                        logging.debug(f"Download progress: {progress:.1f}%")
            
            # Ensure file is fully written before moving
            time.sleep(2)
            
            # Move to completed folder after download finishes with retry logic
            os.makedirs(self.completed_folder, exist_ok=True)
            final_path = os.path.join(self.completed_folder, filename)
            
            # Retry file move up to 5 times
            for attempt in range(5):
                try:
                    os.rename(temp_path, final_path)
                    logging.info(f"Download completed successfully: {final_path}")
                    break
                except (OSError, IOError) as e:
                    if attempt < 4:
                        logging.warning(f"File move attempt {attempt + 1} failed, retrying in 3 seconds: {e}")
                        time.sleep(3)
                    else:
                        logging.error(f"Failed to move file after 5 attempts: {e}")
                        raise
        except requests.RequestException as e:
            logging.error(f"Download failed: {e}")
        except IOError as e:
            logging.error(f"File write error: {e}")
    
    def delete_torrent(self, torrent_id):
        api_token = self.get_api_token()
        if not api_token:
            logging.error("No API token available for torrent deletion")
            return
            
        url = f"https://api.real-debrid.com/rest/1.0/torrents/delete/{torrent_id}"
        headers = {"Authorization": f"Bearer {api_token}"}
        
        logging.debug(f"Deleting torrent from Real Debrid: {torrent_id}")
        response = requests.delete(url, headers=headers)
        if response.status_code == 204:
            logging.info(f"Torrent deleted successfully: {torrent_id}")
        else:
            logging.warning(f"Torrent deletion response: {response.status_code}")

def process_existing_magnets(magnets_folder, handler):
    """Process any existing magnet files in the folder"""
    try:
        magnet_files = [f for f in os.listdir(magnets_folder) if f.endswith('.magnet')]
        if magnet_files:
            logging.info(f"Found {len(magnet_files)} magnet files to process in {magnets_folder}")
        
        for filename in magnet_files:
            file_path = os.path.join(magnets_folder, filename)
            
            if file_path in handler.processing_files:
                logging.debug(f"Already processing: {filename}")
                continue
                
            if len(handler.processing_files) >= 3:
                logging.debug(f"Maximum concurrent downloads reached (3), skipping: {filename}")
                break
                
            # Check if file is accessible
            try:
                with open(file_path, 'r') as f:
                    pass  # Just test if we can open it
                logging.info(f"Queuing existing magnet: {filename}")
                handler.processing_files.add(file_path)
                handler.executor.submit(handler._process_magnet_wrapper, file_path)
            except PermissionError:
                logging.warning(f"Skipping locked file: {filename}")
    except Exception as e:
        logging.error(f"Error scanning magnet folder {magnets_folder}: {e}")

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
    
    # Define organized folder paths
    content_dir = os.path.join(base_dir, 'content')
    
    sonarr_dir = os.path.join(content_dir, 'sonarr')
    sonarr_magnets = os.path.join(sonarr_dir, 'magnets')
    sonarr_completed_magnets = os.path.join(sonarr_dir, 'completed_magnets')
    sonarr_completed = os.path.join(sonarr_dir, 'completed_downloads')
    
    radarr_dir = os.path.join(content_dir, 'radarr')
    radarr_magnets = os.path.join(radarr_dir, 'magnets')
    radarr_completed_magnets = os.path.join(radarr_dir, 'completed_magnets')
    radarr_completed = os.path.join(radarr_dir, 'completed_downloads')
    
    in_progress_dir = os.path.join(content_dir, 'in_progress')
    
    os.makedirs(sonarr_magnets, exist_ok=True)
    os.makedirs(sonarr_completed_magnets, exist_ok=True)
    os.makedirs(sonarr_completed, exist_ok=True)
    os.makedirs(radarr_magnets, exist_ok=True)
    os.makedirs(radarr_completed_magnets, exist_ok=True)
    os.makedirs(radarr_completed, exist_ok=True)
    os.makedirs(in_progress_dir, exist_ok=True)
    
    # Create handlers
    sonarr_handler = MagnetHandler(config_path, sonarr_completed, sonarr_magnets, sonarr_completed_magnets)
    radarr_handler = MagnetHandler(config_path, radarr_completed, radarr_magnets, radarr_completed_magnets)
    
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