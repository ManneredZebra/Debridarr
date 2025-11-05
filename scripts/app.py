#!/usr/bin/env python3
import os
import yaml
import time
import logging
import requests
import sys
import threading
import json
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler
from web_ui import WebUI

class MagnetHandler(FileSystemEventHandler):
    def __init__(self, config_path, completed_folder, magnets_folder, completed_magnets_folder, in_progress_folder, failed_magnets_folder, performance_mode='medium', client_name=''):
        self.config_path = config_path
        self.completed_folder = completed_folder
        self.magnets_folder = magnets_folder
        self.completed_magnets_folder = completed_magnets_folder
        self.in_progress_folder = in_progress_folder
        self.failed_magnets_folder = failed_magnets_folder
        self.client_name = client_name
        
        # Set performance parameters
        perf_settings = {
            'low': {'workers': 1, 'chunk_size': 4096},
            'medium': {'workers': 2, 'chunk_size': 8192},
            'high': {'workers': 4, 'chunk_size': 16384}
        }
        settings = perf_settings.get(performance_mode, perf_settings['medium'])
        self.max_workers = settings['workers']
        self.chunk_size = settings['chunk_size']
        
        self.executor = ThreadPoolExecutor(max_workers=self.max_workers)
        self.processing_files = set()
        self.queued_files = []  # Ordered list of queued files
        self.download_progress = {}  # Track progress for each magnet file
        self.file_downloads = {}  # Track individual file downloads within torrents
        self.retry_attempts = {}  # Track retry attempts for failed magnets
        self.retry_cooldown = {}  # Track cooldown timestamps for retries
        self.torrent_ids = {}  # Track torrent IDs for each magnet file
        
    def on_created(self, event):
        if hasattr(event, 'is_directory') and event.is_directory:
            return
        if not event.src_path.endswith('.magnet'):
            return
        
        if event.src_path in self.processing_files or event.src_path in self.queued_files:
            logging.debug(f"Already processing or queued: {event.src_path}")
            return
            
        if len(self.processing_files) >= self.max_workers:
            logging.info(f"Maximum concurrent downloads reached ({self.max_workers}), queuing: {event.src_path}")
            self.queued_files.append(event.src_path)
            return
            
        logging.info(f"New magnet file detected: {event.src_path}")
        self.processing_files.add(event.src_path)
        self.download_progress[event.src_path] = {'status': 'Starting', 'progress': 0, 'cache_progress': 0, 'download_progress': 0}
        self.executor.submit(self._process_magnet_wrapper, event.src_path)
    
    def _process_magnet_wrapper(self, file_path):
        try:
            self.process_magnet(file_path)
        finally:
            self.processing_files.discard(file_path)
            self.download_progress.pop(file_path, None)
            self.file_downloads.pop(file_path, None)
            self.torrent_ids.pop(file_path, None)
            self._process_next_queued()
    
    def process_magnet(self, file_path):
        try:
            # Wait for file to be fully written and stable
            time.sleep(3)
            
            # Check if file still exists (might have been processed by another thread)
            if not os.path.exists(file_path):
                logging.info(f"File no longer exists, skipping: {file_path}")
                return
            
            # Check if magnet already processed
            filename = os.path.basename(file_path)
            completed_magnet_path = os.path.join(self.completed_magnets_folder, filename)
            if os.path.exists(completed_magnet_path):
                logging.info(f"Magnet already processed, removing duplicate: {filename}")
                os.remove(file_path)
                return
            
            with open(file_path, 'r') as f:
                magnet_link = f.read().strip()
            
            self.download_progress[file_path] = {'status': 'Checking existing torrents', 'progress': 5, 'cache_progress': 2, 'download_progress': 0}
            torrent_id = self.check_or_add_torrent(magnet_link, file_path)
            if not torrent_id:
                return
            if torrent_id == 'FAILED':
                # Report failure and move magnet to failed folder
                self.report_failure_to_arr(filename)
                try:
                    os.makedirs(self.failed_magnets_folder, exist_ok=True)
                    os.rename(file_path, os.path.join(self.failed_magnets_folder, filename))
                    logging.info(f"Moved failed magnet: {os.path.basename(file_path)}")
                except:
                    pass
                return
            
            # Store torrent ID for abort handling
            self.torrent_ids[file_path] = torrent_id
            
            self.download_progress[file_path] = {'status': 'Selecting files', 'progress': 20, 'cache_progress': 10, 'download_progress': 0}
            if not self.select_files(torrent_id):
                self.delete_torrent(torrent_id)
                self.report_failure_to_arr(filename)
                try:
                    os.makedirs(self.failed_magnets_folder, exist_ok=True)
                    os.rename(file_path, os.path.join(self.failed_magnets_folder, filename))
                    logging.info(f"Moved failed magnet: {os.path.basename(file_path)}")
                except:
                    pass
                return
            
            self.download_progress[file_path] = {'status': 'Caching to Real-Debrid', 'progress': 30, 'cache_progress': 15, 'download_progress': 0}
            results = self.wait_for_torrent(torrent_id, file_path)
            if not results:
                self.delete_torrent(torrent_id)
                self.report_failure_to_arr(filename)
                try:
                    os.makedirs(self.failed_magnets_folder, exist_ok=True)
                    os.rename(file_path, os.path.join(self.failed_magnets_folder, filename))
                    logging.info(f"Moved failed magnet: {os.path.basename(file_path)}")
                except:
                    pass
                return
            
            # Initialize individual file progress bars
            self.file_downloads[file_path] = []
            for i, (download_link, filename) in enumerate(results):
                if download_link and filename:
                    file_info = {'filename': filename, 'progress': 0, 'status': 'Queued'}
                    self.file_downloads[file_path].append(file_info)
            
            # Update progress with file count
            total_files = len(self.file_downloads[file_path])
            self.download_progress[file_path] = {'status': f'Cached in Real-Debrid ({total_files} files)', 'progress': 50, 'cache_progress': 100, 'files_progress': 0}
            
            # Download all files from the torrent
            hoster_unavailable = False
            for i, (download_link, filename) in enumerate(results):
                # Check if download was aborted
                if file_path not in self.processing_files:
                    logging.info(f"Download aborted, stopping file downloads: {file_path}")
                    self.delete_torrent(torrent_id)
                    break
                if download_link == 'HOSTER_UNAVAILABLE':
                    hoster_unavailable = True
                    break
                if download_link and filename:
                    self.download_file(download_link, filename, file_path, i)
            
            # Handle hoster unavailable
            if hoster_unavailable:
                self.retry_attempts[file_path] = self.retry_attempts.get(file_path, 0) + 1
                if self.retry_attempts[file_path] >= 3:
                    logging.error(f"Hoster unavailable after 3 attempts, moving to failed: {os.path.basename(file_path)}")
                    try:
                        os.makedirs(self.failed_magnets_folder, exist_ok=True)
                        os.rename(file_path, os.path.join(self.failed_magnets_folder, os.path.basename(file_path)))
                    except:
                        pass
                    self.retry_attempts.pop(file_path, None)
                    self.retry_cooldown.pop(file_path, None)
                    return
                else:
                    logging.warning(f"Hoster unavailable, will retry in 10 minutes (attempt {self.retry_attempts[file_path]}/3): {os.path.basename(file_path)}")
                    self.retry_cooldown[file_path] = time.time() + 600  # 10 minutes
                    return
            
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
                config = yaml.safe_load(f)
            return config['real_debrid_api_token'].strip().strip('"').strip("'")
        except Exception as e:
            logging.error(f"Error reading config: {e}")
            return None
    
    def check_or_add_torrent(self, magnet_link, file_path):
        # First check if torrent already exists
        existing_id = self.check_existing_torrent(magnet_link)
        if existing_id:
            logging.info(f"Found existing torrent: {existing_id}")
            self.download_progress[file_path] = {'status': 'Using existing torrent', 'progress': 15, 'cache_progress': 10, 'download_progress': 0}
            return existing_id
        
        # If not found, add new torrent
        self.download_progress[file_path] = {'status': 'Adding torrent', 'progress': 10, 'cache_progress': 5, 'download_progress': 0}
        return self.add_torrent(magnet_link)
    
    def check_existing_torrent(self, magnet_link):
        try:
            api_token = self.get_api_token()
            if not api_token:
                return None
                
            url = "https://api.real-debrid.com/rest/1.0/torrents"
            headers = {"Authorization": f"Bearer {api_token}"}
            
            response = requests.get(url, headers=headers, timeout=30)
            if response.status_code == 200:
                torrents = response.json()
                # Extract hash from magnet link
                import re
                hash_match = re.search(r'btih:([a-fA-F0-9]{40})', magnet_link)
                if hash_match:
                    target_hash = hash_match.group(1).lower()
                    for torrent in torrents:
                        if torrent.get('hash', '').lower() == target_hash:
                            return torrent['id']
            return None
        except Exception as e:
            logging.error(f"Error checking existing torrents: {e}")
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
            elif response.status_code == 429:
                logging.warning("Rate limit exceeded, waiting 1 minute...")
                time.sleep(60)
                return None
            else:
                try:
                    error_data = response.json()
                    error_code = error_data.get('error_code')
                    if error_code == 35:
                        logging.error(f"Infringing file detected: {response.text}")
                        return 'FAILED'
                    elif error_code in [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34]:
                        logging.error(f"Real-Debrid error {error_code}: {response.text}")
                        return 'FAILED'
                except:
                    pass
                logging.error(f"Failed to add torrent (status {response.status_code}): {response.text}")
                return 'FAILED'
        except requests.RequestException as e:
            logging.error(f"Network error adding torrent: {e}")
            return None
    
    def select_files(self, torrent_id):
        api_token = self.get_api_token()
        if not api_token:
            logging.error("No API token available for file selection")
            return False
            
        url = f"https://api.real-debrid.com/rest/1.0/torrents/selectFiles/{torrent_id}"
        headers = {"Authorization": f"Bearer {api_token}"}
        data = {"files": "all"}
        
        logging.debug(f"Selecting all files for torrent: {torrent_id}")
        response = requests.post(url, headers=headers, data=data)
        if response.status_code == 204:
            logging.info(f"Files selected for torrent: {torrent_id}")
            return True
        elif response.status_code == 202:
            logging.info(f"Files selected for torrent: {torrent_id}")
            return True
        elif response.status_code == 404:
            logging.error(f"Torrent not found (404): {torrent_id}")
            return False
        else:
            logging.warning(f"File selection failed (status {response.status_code}): {torrent_id}")
            return False
    
    def wait_for_torrent(self, torrent_id, file_path=None):
        api_token = self.get_api_token()
        if not api_token:
            logging.error("No API token available for torrent status check")
            return None
            
        url = f"https://api.real-debrid.com/rest/1.0/torrents/info/{torrent_id}"
        headers = {"Authorization": f"Bearer {api_token}"}
        
        logging.info(f"Waiting for torrent to complete: {torrent_id}")
        for attempt in range(60):
            response = requests.get(url, headers=headers)
            if response.status_code == 404:
                # Torrent was deleted from Real-Debrid, re-add it
                logging.warning(f"Torrent {torrent_id} not found in Real-Debrid, re-adding...")
                if file_path:
                    try:
                        with open(file_path, 'r') as f:
                            magnet_link = f.read().strip()
                        new_torrent_id = self.add_torrent(magnet_link)
                        if new_torrent_id and new_torrent_id != 'FAILED':
                            self.torrent_ids[file_path] = new_torrent_id
                            if not self.select_files(new_torrent_id):
                                return None
                            return self.wait_for_torrent(new_torrent_id, file_path)
                    except:
                        pass
                return None
            if response.status_code == 200:
                data = response.json()
                status = data.get('status', 'unknown')
                progress = data.get('progress', 0)
                
                if attempt % 6 == 0:  # Log every minute
                    logging.info(f"Torrent {torrent_id} status: {status}, progress: {progress}%")
                
                if file_path and file_path in self.download_progress:
                    if status == 'downloaded':
                        self.download_progress[file_path] = {'status': 'Cached in Real-Debrid', 'progress': 50, 'cache_progress': 100, 'download_progress': 0}
                    else:
                        self.download_progress[file_path] = {'status': f'Caching to Real-Debrid ({status})', 'progress': 30 + int(progress * 0.2), 'cache_progress': progress, 'download_progress': 0}
                
                if status == 'downloaded':
                    logging.info(f"Torrent {torrent_id} completed successfully")
                    # Return all links with actual filenames from torrent info
                    links = data.get('links', [])
                    files = data.get('files', [])
                    results = []
                    for i, link in enumerate(links):
                        filename = files[i]['path'].split('/')[-1] if i < len(files) else self.get_filename_from_link(link)
                        results.append((self.unrestrict_link(link), filename))
                    return results
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
        elif response.status_code != 200:
            try:
                error_data = response.json()
                if error_data.get('error_code') == 19:
                    logging.error(f"Hoster unavailable for link (error 19): {link}")
                    return 'HOSTER_UNAVAILABLE'
            except:
                pass
        return None
    
    def download_file(self, download_url, rd_filename=None, file_path=None, file_index=None):
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
                if any(ext in url_filename.lower() for ext in ['.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v', '.flv', '.webm']) and not url_filename.lower().endswith('.rartv'):
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
            
            # Download to configured in_progress folder first
            os.makedirs(self.in_progress_folder, exist_ok=True)
            temp_path = os.path.join(self.in_progress_folder, filename)
            
            logging.info(f"Downloading to temporary location: {temp_path}")
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            with open(temp_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=self.chunk_size):
                    # Check if download was aborted
                    if file_path and file_path not in self.download_progress:
                        logging.info(f"Download aborted during file transfer: {filename}")
                        f.close()
                        if os.path.exists(temp_path):
                            os.remove(temp_path)
                        return
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        progress = (downloaded / total_size) * 100
                        if file_path and file_path in self.download_progress:
                            # Update individual file progress
                            if file_index is not None and file_path in self.file_downloads:
                                if file_index < len(self.file_downloads[file_path]):
                                    self.file_downloads[file_path][file_index]['progress'] = int(progress)
                                    self.file_downloads[file_path][file_index]['status'] = 'Downloading'
                            
                            # Update overall files progress (percentage of files completed)
                            if file_path in self.file_downloads:
                                total_files = len(self.file_downloads[file_path])
                                completed_files = len([f for f in self.file_downloads[file_path] if f['progress'] == 100])
                                files_progress = (completed_files / total_files * 100) if total_files > 0 else 0
                                self.download_progress[file_path] = {'status': f'Downloading files ({completed_files}/{total_files} complete)', 'progress': 50 + int(files_progress * 0.5), 'cache_progress': 100, 'files_progress': int(files_progress)}
                        if downloaded % (1024*1024*10) == 0:  # Log every 10MB
                            logging.debug(f"Download progress: {progress:.1f}%")
            
            # Ensure file is fully written before moving
            time.sleep(2)
            
            # Move to completed folder after download finishes with retry logic
            os.makedirs(self.completed_folder, exist_ok=True)
            final_path = os.path.join(self.completed_folder, filename)
            
            # Check if file already exists in completed folder
            if os.path.exists(final_path):
                logging.info(f"File already exists in completed folder, removing from in_progress: {filename}")
                os.remove(temp_path)
                return
            
            # Retry file move up to 5 times
            for attempt in range(5):
                try:
                    os.rename(temp_path, final_path)
                    logging.info(f"Download completed successfully: {final_path}")
                    # Mark file as completed
                    if file_index is not None and file_path and file_path in self.file_downloads:
                        if file_index < len(self.file_downloads[file_path]):
                            self.file_downloads[file_path][file_index]['progress'] = 100
                            self.file_downloads[file_path][file_index]['status'] = 'Completed'
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
    
    def report_failure_to_arr(self, magnet_filename):
        try:
            with open(self.config_path, 'r') as f:
                config = yaml.safe_load(f)
            
            client_config = config.get('download_clients', {}).get(self.client_name, {})
            arr_url = client_config.get('arr_url', '')
            arr_api_key = client_config.get('arr_api_key', '')
            
            if not arr_url or not arr_api_key:
                return
            
            # Extract download ID from filename (format varies but usually contains ID)
            import re
            id_match = re.search(r'[_-]([0-9]+)[_\.]', magnet_filename)
            if not id_match:
                logging.debug(f"Could not extract download ID from: {magnet_filename}")
                return
            
            download_id = id_match.group(1)
            
            # Try to find and remove from queue
            headers = {'X-Api-Key': arr_api_key}
            queue_url = f"{arr_url.rstrip('/')}/api/v3/queue"
            
            response = requests.get(queue_url, headers=headers, timeout=10)
            if response.status_code == 200:
                queue_items = response.json().get('records', [])
                for item in queue_items:
                    if str(item.get('id')) == download_id or download_id in item.get('title', ''):
                        delete_url = f"{queue_url}/{item['id']}?blocklist=true&removeFromClient=true"
                        del_response = requests.delete(delete_url, headers=headers, timeout=10)
                        if del_response.status_code in [200, 204]:
                            logging.info(f"Reported failure to {self.client_name}: {magnet_filename}")
                        else:
                            logging.warning(f"Failed to report to {self.client_name}: {del_response.status_code}")
                        return
        except Exception as e:
            logging.debug(f"Error reporting failure to {self.client_name}: {e}")
    
    def _process_next_queued(self):
        if self.queued_files and len(self.processing_files) < self.max_workers:
            next_file = self.queued_files.pop(0)
            if os.path.exists(next_file):
                logging.info(f"Processing queued magnet: {next_file}")
                self.processing_files.add(next_file)
                self.download_progress[next_file] = {'status': 'Starting', 'progress': 0, 'cache_progress': 0, 'download_progress': 0}
                self.executor.submit(self._process_magnet_wrapper, next_file)
    
    def move_queue_item(self, file_path, direction):
        if file_path not in self.queued_files:
            return False
        idx = self.queued_files.index(file_path)
        if direction == 'up' and idx > 0:
            self.queued_files[idx], self.queued_files[idx-1] = self.queued_files[idx-1], self.queued_files[idx]
            return True
        elif direction == 'down' and idx < len(self.queued_files) - 1:
            self.queued_files[idx], self.queued_files[idx+1] = self.queued_files[idx+1], self.queued_files[idx]
            return True
        return False

def process_existing_magnets(magnets_folder, handler):
    """Process any existing magnet files in the folder"""
    try:
        magnet_files = [f for f in os.listdir(magnets_folder) if f.endswith('.magnet')]
        if magnet_files:
            logging.info(f"Found {len(magnet_files)} magnet files to process in {magnets_folder}")
        
        for filename in magnet_files:
            file_path = os.path.join(magnets_folder, filename)
            
            if file_path in handler.processing_files or file_path in handler.queued_files:
                logging.debug(f"Already processing or queued: {filename}")
                continue
            
            # Check if file is in cooldown
            if file_path in handler.retry_cooldown:
                if time.time() < handler.retry_cooldown[file_path]:
                    continue
                else:
                    handler.retry_cooldown.pop(file_path, None)
                
            if len(handler.processing_files) >= handler.max_workers:
                logging.debug(f"Maximum concurrent downloads reached ({handler.max_workers}), queuing: {filename}")
                if file_path not in handler.queued_files:
                    handler.queued_files.append(file_path)
                continue
                
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

def setup_handlers(config_path, observer):
    """Setup or reload handlers based on current config"""
    base_dir = 'C:\\ProgramData\\Debridarr'
    
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
    except:
        logging.error("Failed to load config for handler setup")
        return []
    
    # Stop existing handlers
    observer.unschedule_all()
    
    handlers = []
    download_clients = config.get('download_clients', {})
    
    for client_name, client_config in download_clients.items():
        magnets_folder = os.path.expandvars(client_config['magnets_folder'])
        in_progress_folder = os.path.expandvars(client_config['in_progress_folder'])
        completed_magnets_folder = os.path.expandvars(client_config['completed_magnets_folder'])
        completed_downloads_folder = os.path.expandvars(client_config['completed_downloads_folder'])
        failed_magnets_folder = os.path.expandvars(client_config.get('failed_magnets_folder', os.path.join(os.path.dirname(magnets_folder), 'failed_magnets')))
        
        # Create directories
        os.makedirs(magnets_folder, exist_ok=True)
        os.makedirs(in_progress_folder, exist_ok=True)
        os.makedirs(completed_magnets_folder, exist_ok=True)
        os.makedirs(completed_downloads_folder, exist_ok=True)
        os.makedirs(failed_magnets_folder, exist_ok=True)
        
        # Get performance mode
        performance_mode = config.get('performance_mode', 'medium')
        
        # Create handler
        handler = MagnetHandler(config_path, completed_downloads_folder, magnets_folder, completed_magnets_folder, in_progress_folder, failed_magnets_folder, performance_mode, client_name)
        handlers.append((client_name, handler, magnets_folder))
        
        # Schedule observer
        observer.schedule(handler, magnets_folder, recursive=False)
        
        logging.info(f"Configured client: {client_name}")
    
    return handlers

class DebridDownloadsManager:
    def __init__(self, config_path, base_dir):
        self.config_path = config_path
        self.db_path = os.path.join(base_dir, 'debrid_downloads.json')
        self.downloads = self.load_downloads()
        self.download_progress = {}
    
    def extract_media_info(self, filename):
        """Extract title, season, episode from filename"""
        import re
        # Remove extension and common separators
        name = os.path.splitext(filename)[0].lower()
        name = re.sub(r'[._-]', ' ', name)
        
        # Extract season/episode patterns (S01E01, 1x01, etc)
        season_ep = re.search(r's(\d+)\s*e(\d+)', name, re.I)
        if not season_ep:
            season_ep = re.search(r'(\d+)x(\d+)', name)
        
        # Extract year
        year = re.search(r'(19\d{2}|20\d{2})', name)
        
        # Remove quality/codec info
        name = re.sub(r'\b(1080p|720p|2160p|4k|x264|x265|hevc|bluray|webrip|web dl|hdtv|proper|repack)\b.*', '', name, flags=re.I)
        
        # Clean up title
        title = name.strip()
        
        return {
            'title': title,
            'season': season_ep.group(1) if season_ep else None,
            'episode': season_ep.group(2) if season_ep else None,
            'year': year.group(1) if year else None
        }
    
    def smart_match(self, rd_filename, media_files):
        """Smart matching for renamed files"""
        rd_info = self.extract_media_info(rd_filename)
        
        for media_file in media_files:
            media_info = self.extract_media_info(media_file)
            
            # Check if titles match (fuzzy)
            rd_words = set(rd_info['title'].split())
            media_words = set(media_info['title'].split())
            common_words = rd_words & media_words
            
            # Require at least 50% word overlap for title match
            if len(common_words) >= max(len(rd_words), len(media_words)) * 0.5:
                # For TV shows, match season/episode
                if rd_info['season'] and rd_info['episode']:
                    if rd_info['season'] == media_info['season'] and rd_info['episode'] == media_info['episode']:
                        return True
                # For movies, match year if available
                elif rd_info['year']:
                    if rd_info['year'] == media_info['year']:
                        return True
                # If no season/episode/year, strong title match is enough
                elif len(common_words) >= max(len(rd_words), len(media_words)) * 0.7:
                    return True
        
        return False
        
    def load_downloads(self):
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, 'r') as f:
                    return json.load(f)
            except:
                return []
        return []
    
    def save_downloads(self):
        with open(self.db_path, 'w') as f:
            json.dump(self.downloads, f, indent=2)
    
    def sync_from_api(self):
        try:
            with open(self.config_path, 'r') as f:
                config = yaml.safe_load(f)
            api_token = config.get('real_debrid_api_token', '').strip().strip('"').strip("'")
            if not api_token or api_token == 'YOUR_API_TOKEN_HERE':
                return {'success': False, 'message': 'No valid API token'}
            
            limit = config.get('debrid_sync_limit', 100)
            url = f'https://api.real-debrid.com/rest/1.0/downloads?limit={limit}'
            headers = {'Authorization': f'Bearer {api_token}'}
            response = requests.get(url, headers=headers, timeout=30)
            
            if response.status_code != 200:
                return {'success': False, 'message': f'API error: {response.status_code}'}
            
            rd_downloads = response.json()
            
            # Get manual downloads folder
            manual_folder = config.get('manual_downloads_folder', '')
            if not manual_folder:
                manual_folder = os.path.join(os.path.expanduser('~'), 'Downloads', 'Debridarr_Manual')
            manual_folder = os.path.expandvars(manual_folder)
            manual_files = set()
            if os.path.exists(manual_folder):
                manual_files = set(os.listdir(manual_folder))
            
            # Check media directory if configured
            media_root = config.get('media_root_directory', '')
            media_files = []
            if media_root and os.path.exists(media_root):
                for root, dirs, files in os.walk(media_root):
                    media_files.extend(files)
            
            # Process downloads (deduplicate by filename)
            new_downloads = []
            seen_filenames = set()
            for item in rd_downloads:
                filename = item.get('filename', '')
                file_id = item.get('id', '')
                
                # Skip duplicates
                if filename in seen_filenames:
                    continue
                seen_filenames.add(filename)
                
                # Determine status with smart matching
                status = 'Not Downloaded'
                if filename in manual_files:
                    status = 'Already in Manual Downloads'
                elif media_root and (filename in media_files or self.smart_match(filename, media_files)):
                    status = 'Already in Media Library'
                elif not media_root:
                    status = 'Unknown'
                
                new_downloads.append({
                    'id': file_id,
                    'filename': filename,
                    'filesize': item.get('filesize', 0),
                    'link': item.get('link', ''),
                    'host': item.get('host', ''),
                    'generated': item.get('generated', ''),
                    'status': status
                })
            
            self.downloads = new_downloads
            self.save_downloads()
            
            return {'success': True, 'message': f'Synced {len(new_downloads)} downloads', 'count': len(new_downloads)}
        except Exception as e:
            logging.error(f'Sync error: {e}')
            return {'success': False, 'message': str(e)}
    
    def get_downloads(self, search='', sort_by='date_desc', status_filter='all'):
        filtered = self.downloads
        
        # Filter by search - flexible matching
        if search:
            search_terms = search.lower().split()
            filtered = [d for d in filtered if all(term in d['filename'].lower().replace('.', ' ').replace('_', ' ') for term in search_terms)]
        
        # Filter by status
        if status_filter != 'all':
            filtered = [d for d in filtered if d['status'] == status_filter]
        
        # Sort
        if sort_by == 'date_desc':
            filtered.sort(key=lambda x: x.get('generated', ''), reverse=True)
        elif sort_by == 'date_asc':
            filtered.sort(key=lambda x: x.get('generated', ''))
        elif sort_by == 'name_asc':
            filtered.sort(key=lambda x: x['filename'])
        elif sort_by == 'name_desc':
            filtered.sort(key=lambda x: x['filename'], reverse=True)
        elif sort_by == 'size_desc':
            filtered.sort(key=lambda x: x.get('filesize', 0), reverse=True)
        elif sort_by == 'size_asc':
            filtered.sort(key=lambda x: x.get('filesize', 0))
        
        return filtered
    
    def download_file(self, file_id):
        try:
            # Find the download
            download = next((d for d in self.downloads if d['id'] == file_id), None)
            if not download:
                return {'success': False, 'message': 'Download not found'}
            
            with open(self.config_path, 'r') as f:
                config = yaml.safe_load(f)
            
            api_token = config.get('real_debrid_api_token', '').strip().strip('"').strip("'")
            if not api_token:
                return {'success': False, 'message': 'No API token'}
            
            # Unrestrict the link first
            self.download_progress[file_id] = {'progress': 0, 'status': 'Unrestricting link'}
            unrestrict_url = 'https://api.real-debrid.com/rest/1.0/unrestrict/link'
            headers = {'Authorization': f'Bearer {api_token}'}
            data = {'link': download['link']}
            
            response = requests.post(unrestrict_url, headers=headers, data=data, timeout=30)
            if response.status_code != 200:
                self.download_progress.pop(file_id, None)
                return {'success': False, 'message': f'Failed to unrestrict link: {response.status_code}'}
            
            download_url = response.json()['download']
            
            # Get manual downloads folder
            manual_folder = config.get('manual_downloads_folder', '')
            if not manual_folder:
                manual_folder = os.path.join(os.path.expanduser('~'), 'Downloads', 'Debridarr_Manual')
            manual_folder = os.path.expandvars(manual_folder)
            os.makedirs(manual_folder, exist_ok=True)
            
            # Download the file
            self.download_progress[file_id] = {'progress': 0, 'status': 'Downloading'}
            response = requests.get(download_url, stream=True, timeout=30)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            filepath = os.path.join(manual_folder, download['filename'])
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        progress = int((downloaded / total_size) * 100)
                        self.download_progress[file_id] = {'progress': progress, 'status': 'Downloading'}
            
            # Update status
            download['status'] = 'Already in Manual Downloads'
            self.save_downloads()
            self.download_progress.pop(file_id, None)
            
            return {'success': True, 'message': f'Downloaded to {filepath}'}
        except Exception as e:
            logging.error(f'Download error: {e}')
            self.download_progress.pop(file_id, None)
            return {'success': False, 'message': str(e)}

def main(shutdown_event=None):
    # All data in ProgramData for write access and preservation
    base_dir = 'C:\\ProgramData\\Debridarr'
    
    # Setup logging
    logs_dir = os.path.join(base_dir, 'logs')
    os.makedirs(logs_dir, exist_ok=True)
    log_file = os.path.join(logs_dir, 'debridarr.log')
    
    # Setup rotating log handler (100KB max, 3 backup files)
    log_handler = RotatingFileHandler(log_file, maxBytes=100*1024, backupCount=3)
    log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    
    logging.basicConfig(
        level=logging.INFO,
        handlers=[log_handler]
    )
    
    config_path = os.path.join(base_dir, 'config.yaml')
    observer = Observer()
    handlers = []
    
    # Initialize Debrid Downloads Manager
    debrid_manager = DebridDownloadsManager(config_path, base_dir)
    
    # Setup initial handlers
    handlers = setup_handlers(config_path, observer)
    if not handlers:
        logging.error("Failed to setup handlers. Check config.yaml")
        return
    
    # Reload callback for when settings change
    def reload_handlers():
        nonlocal handlers
        logging.info("Reloading configuration...")
        time.sleep(1)  # Brief delay to ensure config is written
        handlers = setup_handlers(config_path, observer)
        logging.info("Configuration reloaded successfully")
    
    # Process existing magnet files for all clients
    for client_name, handler, magnets_folder in handlers:
        process_existing_magnets(magnets_folder, handler)
    
    try:
        observer.start()
        logging.info("Debridarr started - monitoring for magnet files")
        
        # Start web UI in separate thread with reload callback
        web_ui = WebUI(config_path, handlers, debrid_manager=debrid_manager, reload_callback=reload_handlers)
        web_thread = threading.Thread(target=web_ui.run, daemon=True)
        web_thread.start()
        logging.info("Web UI started on http://127.0.0.1:3636")
        
        while True:
            if shutdown_event and shutdown_event.is_set():
                break
            time.sleep(30)
            # Retry processing any remaining magnet files for all clients
            for client_name, handler, magnets_folder in handlers:
                process_existing_magnets(magnets_folder, handler)
    except KeyboardInterrupt:
        logging.info("Shutting down...")
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
    finally:
        observer.stop()
        observer.join()

if __name__ == "__main__":
    main()