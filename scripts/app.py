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
    def __init__(self, config_path, completed_folder, magnets_folder, completed_magnets_folder, in_progress_folder, failed_magnets_folder, performance_mode='medium', client_name='', file_types=None, auto_extract=True):
        self.config_path = config_path
        self.completed_folder = completed_folder
        self.magnets_folder = magnets_folder
        self.completed_magnets_folder = completed_magnets_folder
        self.in_progress_folder = in_progress_folder
        self.failed_magnets_folder = failed_magnets_folder
        self.client_name = client_name
        self.auto_extract = auto_extract
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
        self.processing_files = set()  # Files currently being processed (upload phase)
        self.downloading_files = set()  # Files currently downloading (download phase)
        self.queued_for_download = {}  # Files queued for download: {file_path: results}
        self.ready_to_download = {}  # Files ready for download: {file_path: results}
        self.download_progress = {}  # Track progress for each magnet file
        self.file_downloads = {}  # Track individual file downloads within torrents
        self.retry_attempts = {}  # Track retry attempts for failed magnets
        self.retry_cooldown = {}  # Track cooldown timestamps for retries
        self.torrent_ids = {}  # Track torrent IDs for each magnet file
        self.progress_lock = threading.Lock()  # Lock for thread-safe progress updates
        self.upload_timestamps = {}  # Track when files started uploading for ordering
    
    def reload_file_types(self):
        """Reload configuration - kept for compatibility but no longer filters by file type"""
        try:
            logging.info(f"Configuration reloaded for {self.client_name} - file type filtering disabled")
        except Exception as e:
            logging.error(f"Error reloading configuration: {e}")
        
    def on_created(self, event):
        if hasattr(event, 'is_directory') and event.is_directory:
            return
        if not (event.src_path.endswith('.magnet') or event.src_path.endswith('.torrent')):
            return
        
        logging.info(f"File watcher detected new file: {event.src_path}")
        
        if event.src_path in self.processing_files or event.src_path in self.ready_to_download:
            logging.debug(f"Already processing: {event.src_path}")
            return
            
        # Always start upload phase immediately - no limit on uploads
        logging.info(f"New magnet/torrent file detected: {event.src_path}")
        self.processing_files.add(event.src_path)
        self.upload_timestamps[event.src_path] = time.time()  # Record start time for ordering
        self.download_progress[event.src_path] = {'status': 'Starting', 'progress': 0, 'cache_progress': 0, 'download_progress': 0}
        
        try:
            self.executor.submit(self._process_magnet_wrapper, event.src_path)
            logging.info(f"Submitted file to executor: {event.src_path}")
        except Exception as e:
            logging.error(f"Failed to submit file to executor: {e}")
            self.processing_files.discard(event.src_path)
            self.download_progress.pop(event.src_path, None)
    
    def _process_magnet_wrapper(self, file_path):
        try:
            self.process_magnet(file_path)
        except Exception as e:
            logging.error(f"Error in magnet processing wrapper: {e}")
            # Only clean up on error - successful processing will be cleaned up by download completion
            self.processing_files.discard(file_path)
            self.downloading_files.discard(file_path)
            self.queued_for_download.pop(file_path, None)
            self.download_progress.pop(file_path, None)
            self.file_downloads.pop(file_path, None)
            self.torrent_ids.pop(file_path, None)
            self.ready_to_download.pop(file_path, None)
            self.upload_timestamps.pop(file_path, None)
            self._process_download_queue()  # Try to start next queued download
    
    def process_magnet(self, file_path):
        try:
            # PHASE 1: Upload and cache to Real-Debrid (unlimited)
            results = self._upload_and_cache_magnet(file_path)
            if not results:
                return  # Failed during upload/cache phase
            
            # PHASE 2: Queue for download (handled by queue system)
            # The upload/cache phase queues the item automatically
            
        except PermissionError as e:
            logging.error(f"Permission denied accessing {file_path}: {e}")
        except Exception as e:
            logging.error(f"Error processing {file_path}: {e}")
    
    def _upload_and_cache_magnet(self, file_path):
        """Phase 1: Upload magnet to Real-Debrid and wait for caching (unlimited)"""
        try:
            # Wait for file to be fully written and stable
            time.sleep(3)
            
            # Check if file still exists (might have been processed by another thread)
            if not os.path.exists(file_path):
                logging.info(f"File no longer exists, skipping: {file_path}")
                return None
            
            # Check if magnet already processed
            filename = os.path.basename(file_path)
            completed_magnet_path = os.path.join(self.completed_magnets_folder, filename)
            if os.path.exists(completed_magnet_path):
                logging.info(f"Magnet already processed, removing duplicate: {filename}")
                os.remove(file_path)
                return None
            
            logging.info(f"Reading content from file: {file_path}")
            
            # Determine if this is a magnet link or torrent file
            if file_path.endswith('.magnet'):
                # Read magnet link from file
                with open(file_path, 'r') as f:
                    magnet_link = f.read().strip()
                logging.info(f"Magnet link read successfully, length: {len(magnet_link)}")
                
                self.download_progress[file_path] = {'status': 'Checking existing torrents', 'progress': 5, 'cache_progress': 2, 'download_progress': 0}
                torrent_id = self.check_or_add_torrent(magnet_link, file_path)
                
            elif file_path.endswith('.torrent'):
                # Upload torrent file directly
                logging.info(f"Processing torrent file: {file_path}")
                
                self.download_progress[file_path] = {'status': 'Uploading torrent file', 'progress': 5, 'cache_progress': 2, 'download_progress': 0}
                torrent_id = self.add_torrent_file(file_path)
                
            else:
                logging.error(f"Unsupported file type: {file_path}")
                return None
            if not torrent_id:
                return None
            if torrent_id == 'FAILED':
                # Move magnet to failed folder
                try:
                    os.makedirs(self.failed_magnets_folder, exist_ok=True)
                    os.rename(file_path, os.path.join(self.failed_magnets_folder, filename))
                    logging.info(f"Moved failed magnet: {os.path.basename(file_path)}")
                except:
                    pass
                return None
            
            # Store torrent ID for abort handling
            self.torrent_ids[file_path] = torrent_id
            
            self.download_progress[file_path] = {'status': 'Selecting files', 'progress': 20, 'cache_progress': 10, 'download_progress': 0}
            if not self.select_files(torrent_id):
                self.delete_torrent(torrent_id)
                # Move magnet to failed folder
                try:
                    os.makedirs(self.failed_magnets_folder, exist_ok=True)
                    os.rename(file_path, os.path.join(self.failed_magnets_folder, filename))
                    logging.info(f"Moved failed magnet: {os.path.basename(file_path)}")
                except:
                    pass
                return None
            
            self.download_progress[file_path] = {'status': 'Caching to Real-Debrid', 'progress': 30, 'cache_progress': 15, 'download_progress': 0}
            results = self.wait_for_torrent(torrent_id, file_path)
            if results == 'DEAD':
                # Dead magnet (0% for 10 minutes) - delete from RD
                self.delete_torrent(torrent_id)
                try:
                    os.makedirs(self.failed_magnets_folder, exist_ok=True)
                    os.rename(file_path, os.path.join(self.failed_magnets_folder, filename))
                    logging.info(f"Moved dead magnet: {os.path.basename(file_path)}")
                except:
                    pass
                return None
            if not results:
                self.delete_torrent(torrent_id)
                # Move magnet to failed folder
                try:
                    os.makedirs(self.failed_magnets_folder, exist_ok=True)
                    os.rename(file_path, os.path.join(self.failed_magnets_folder, filename))
                    logging.info(f"Moved failed magnet: {os.path.basename(file_path)}")
                except:
                    pass
                return None
            
            # Initialize individual file progress bars
            self.file_downloads[file_path] = []
            for i, (download_link, filename) in enumerate(results):
                if download_link and filename:
                    file_info = {'filename': filename, 'progress': 0, 'status': 'Queued'}
                    self.file_downloads[file_path].append(file_info)
            
            # Update progress with file count and queue for download
            total_files = len(self.file_downloads[file_path])
            self.download_progress[file_path] = {'status': f'Cached in Real-Debrid ({total_files} files) - Queued for download', 'progress': 50, 'cache_progress': 100, 'files_progress': 0}
            
            # Queue for download instead of downloading immediately
            self.queued_for_download[file_path] = results
            logging.info(f"Queued for download: {os.path.basename(file_path)} ({total_files} files)")
            
            # Try to start download if slots available
            self._process_download_queue()
            
            return results
            
        except Exception as e:
            logging.error(f"Error in upload/cache phase for {file_path}: {e}")
            return None
    
    def _wait_and_download_files(self, file_path, results):
        """Phase 2: This is now handled by the download queue system"""
        # The download queue system will handle this automatically
        pass
    
    def _process_download_queue(self):
        """Process the download queue, starting downloads when slots are available"""
        # Check how many download slots are available
        available_slots = self.max_workers - len(self.downloading_files)
        
        if available_slots <= 0 or not self.queued_for_download:
            return
        
        # Get queued items sorted by upload timestamp (oldest first)
        queued_items = []
        for file_path, results in self.queued_for_download.items():
            if file_path in self.processing_files:  # Make sure it wasn't aborted
                timestamp = self.upload_timestamps.get(file_path, 0)
                queued_items.append((timestamp, file_path, results))
        
        # Sort by timestamp (oldest first)
        queued_items.sort(key=lambda x: x[0])
        
        # Start downloads for available slots
        for i in range(min(available_slots, len(queued_items))):
            timestamp, file_path, results = queued_items[i]
            
            # Move from queued to downloading
            self.queued_for_download.pop(file_path, None)
            self.downloading_files.add(file_path)
            
            # Start the download in a separate thread
            self.executor.submit(self._download_files_from_queue, file_path, results)
            
            logging.info(f"Started download from queue: {os.path.basename(file_path)}")
    
    def _download_files_from_queue(self, file_path, results):
        """Download files for a queued item"""
        try:
            total_files = len(self.file_downloads[file_path])
            self.download_progress[file_path] = {'status': f'Downloading files ({total_files} files)', 'progress': 50, 'cache_progress': 100, 'files_progress': 0}
            
            # Download all files from the torrent (parallel downloads)
            hoster_unavailable = False
            
            # Check for hoster unavailable first
            for download_link, filename in results:
                if download_link == 'HOSTER_UNAVAILABLE':
                    hoster_unavailable = True
                    break
            
            if not hoster_unavailable:
                # Create a separate executor for file downloads within this torrent
                # Use the same max_workers as the main executor to respect performance mode
                total_files = len([r for r in results if r[0] and r[1]])
                concurrent_downloads = min(self.max_workers, total_files)
                logging.info(f"Starting parallel download of {total_files} files with {concurrent_downloads} concurrent downloads")
                
                with ThreadPoolExecutor(max_workers=concurrent_downloads) as file_executor:
                    # Submit all file downloads as futures
                    download_futures = []
                    for i, (download_link, filename) in enumerate(results):
                        if download_link and filename:
                            future = file_executor.submit(self.download_file, download_link, filename, file_path, i)
                            download_futures.append(future)
                    
                    # Wait for all downloads to complete or check for abort
                    completed_count = 0
                    while completed_count < len(download_futures):
                        # Check if download was aborted
                        if file_path not in self.processing_files:
                            logging.info(f"Download aborted, cancelling remaining file downloads: {file_path}")
                            # Cancel remaining futures
                            for future in download_futures:
                                future.cancel()
                            torrent_id = self.torrent_ids.get(file_path)
                            if torrent_id:
                                self.delete_torrent(torrent_id)
                            return
                        
                        # Count completed downloads
                        completed_count = sum(1 for future in download_futures if future.done())
                        
                        # Update overall progress based on completed files (thread-safe)
                        with self.progress_lock:
                            if file_path in self.file_downloads:
                                total_files = len(self.file_downloads[file_path])
                                files_progress = (completed_count / total_files * 100) if total_files > 0 else 0
                                self.download_progress[file_path] = {
                                    'status': f'Downloading files ({completed_count}/{total_files} complete)', 
                                    'progress': 50 + int(files_progress * 0.5), 
                                    'cache_progress': 100, 
                                    'files_progress': int(files_progress)
                                }
                        
                        time.sleep(1)  # Check every second
                    
                    # Wait for all futures to complete and handle any exceptions
                    for future in download_futures:
                        try:
                            future.result()  # This will raise any exception that occurred
                        except Exception as e:
                            logging.error(f"File download failed: {e}")
                            # Continue with other downloads
                    
                    logging.info(f"Completed parallel download of {len(download_futures)} files")
            
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
            
            torrent_id = self.torrent_ids.get(file_path)
            if torrent_id:
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
                        
        except Exception as e:
            logging.error(f"Error in download phase for {file_path}: {e}")
        finally:
            # Always remove from downloading_files and clean up all tracking
            self.downloading_files.discard(file_path)
            self.processing_files.discard(file_path)
            self.queued_for_download.pop(file_path, None)
            self.download_progress.pop(file_path, None)
            self.file_downloads.pop(file_path, None)
            self.torrent_ids.pop(file_path, None)
            self.ready_to_download.pop(file_path, None)
            self.upload_timestamps.pop(file_path, None)
            self._process_download_queue()  # Try to start next queued download
    
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
    
    def add_torrent_file(self, file_path):
        """Upload a .torrent file to Real-Debrid"""
        try:
            api_token = self.get_api_token()
            if not api_token:
                logging.error("No API token available")
                return None
                
            # Use the correct endpoint for torrent file uploads
            url = "https://api.real-debrid.com/rest/1.0/torrents/addTorrent"
            headers = {
                "Authorization": f"Bearer {api_token}",
                "Content-Type": "application/x-bittorrent"
            }
            
            # Read the torrent file as raw binary data and send directly in request body
            with open(file_path, 'rb') as f:
                torrent_data = f.read()
                
                logging.debug(f"Adding torrent file to Real Debrid as raw data: {file_path}")
                # Send raw torrent data in the request body
                response = requests.put(url, headers=headers, data=torrent_data, timeout=30)
            
            if response.status_code == 201:
                torrent_id = response.json()['id']
                logging.info(f"Torrent file added successfully: {torrent_id}")
                return torrent_id
            elif response.status_code == 429:
                logging.warning("Rate limit exceeded, waiting 1 minute...")
                time.sleep(60)
                return None
            else:
                try:
                    error_data = response.json()
                    error_code = error_data.get('error_code')
                    error_details = error_data.get('error_details', '')
                    if error_code == 35:
                        logging.error(f"Infringing file detected: {response.text}")
                        return 'FAILED'
                    elif error_code == 2:
                        logging.error(f"Invalid torrent file parameter: {error_details}")
                        return 'FAILED'
                    elif error_code == 30:
                        logging.error(f"Torrent file format error: {error_details}")
                        return 'FAILED'
                    elif error_code in [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 31, 32, 33, 34]:
                        logging.error(f"Real-Debrid error {error_code}: {response.text}")
                        return 'FAILED'
                except:
                    pass
                logging.error(f"Failed to add torrent file (status {response.status_code}): {response.text}")
                return 'FAILED'
        except requests.RequestException as e:
            logging.error(f"Network error adding torrent file: {e}")
            return None
        except Exception as e:
            logging.error(f"Error reading torrent file {file_path}: {e}")
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
        zero_progress_count = 0
        attempt = 0
        
        while True:  # Remove hard timeout, only rely on dead magnet detection
            attempt += 1
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
                
                # Check for dead magnet (0% progress for 10 minutes)
                if progress == 0 and status not in ['downloaded', 'downloading']:
                    zero_progress_count += 1
                    if zero_progress_count >= 60:  # 60 attempts * 10 seconds = 10 minutes
                        logging.error(f"Torrent {torrent_id} stuck at 0% for 10 minutes, marking as dead")
                        return 'DEAD'
                else:
                    # Reset counter if progress > 0% - torrent is active
                    zero_progress_count = 0
                
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
                    
                    logging.info(f"Processing {len(links)} links and {len(files)} files from torrent")
                    logging.debug(f"Raw torrent files data: {files}")
                    
                    # Always extract filenames directly from download URLs to avoid mismatches
                    # This ensures the filename matches the actual file being downloaded
                    logging.info(f"Extracting filenames directly from {len(links)} download URLs")
                    
                    for i, link in enumerate(links):
                        # Always get filename from unrestricted URL to ensure accuracy
                        unrestricted_url = self.unrestrict_link(link)
                        if unrestricted_url:
                            filename = self.extract_filename_from_url(unrestricted_url)
                            logging.info(f"Link {i}: Extracted filename from download URL: {filename}")
                        else:
                            filename = f'download_{i+1}.mkv'
                            logging.warning(f"Link {i}: Could not unrestrict, using default: {filename}")
                        
                        results.append((unrestricted_url or link, filename))
                    
                    return results
            time.sleep(10)
    
    def get_filename_from_link(self, link):
        """Extract filename from Real Debrid link"""
        filename = None
        
        try:
            # First try to get filename from the link URL itself (before unrestricting)
            # Real-Debrid links sometimes contain the original filename
            import urllib.parse
            parsed_url = urllib.parse.urlparse(link)
            url_filename = parsed_url.path.split('/')[-1]
            if url_filename and '.' in url_filename:
                filename = urllib.parse.unquote(url_filename)
                logging.info(f"Extracted filename from URL path: {filename}")
                return self.basic_sanitize_filename(filename)
        except Exception as e:
            logging.warning(f"Failed to extract filename from URL path: {e}")
        
        try:
            # Make a HEAD request to get filename from headers
            response = requests.head(link, timeout=10)
            cd_header = response.headers.get('content-disposition', '')
            if 'filename=' in cd_header:
                filename = cd_header.split('filename=')[-1].strip('"').strip("'")
                logging.info(f"Extracted filename from headers: {filename}")
                return self.basic_sanitize_filename(filename)
        except Exception as e:
            logging.warning(f"Failed to get filename from headers: {e}")
        
        try:
            # Try unrestricting the link to get the actual download URL which might have a better filename
            unrestricted_url = self.unrestrict_link(link)
            if unrestricted_url and unrestricted_url != link:
                import urllib.parse
                parsed_url = urllib.parse.urlparse(unrestricted_url)
                url_filename = parsed_url.path.split('/')[-1].split('?')[0]
                if url_filename and '.' in url_filename:
                    if '%' in url_filename:
                        url_filename = urllib.parse.unquote(url_filename)
                    filename = url_filename
                    logging.info(f"Extracted filename from unrestricted URL: {filename}")
                    return self.basic_sanitize_filename(filename)
        except Exception as e:
            logging.warning(f"Failed to extract filename from unrestricted URL: {e}")
        
        # Final fallback to URL parsing
        try:
            url_filename = link.split('/')[-1].split('?')[0]
            if '%' in url_filename:
                import urllib.parse
                url_filename = urllib.parse.unquote(url_filename)
            filename = url_filename if url_filename else 'download'
            
            # If still no extension, try to add a generic one based on content-type
            if '.' not in filename:
                try:
                    response = requests.head(link, timeout=5)
                    content_type = response.headers.get('content-type', '').lower()
                    if 'video' in content_type:
                        filename += '.mp4'
                    elif 'audio' in content_type:
                        filename += '.mp3'
                    elif 'application/zip' in content_type:
                        filename += '.zip'
                    elif 'application/x-rar' in content_type:
                        filename += '.rar'
                    else:
                        filename += '.bin'  # Generic binary extension
                    logging.info(f"Added extension based on content-type: {filename}")
                except:
                    filename += '.bin'  # Default fallback
                    logging.info(f"Using fallback filename with generic extension: {filename}")
            else:
                logging.info(f"Using fallback filename: {filename}")
        except:
            filename = 'download.bin'
            
        return self.basic_sanitize_filename(filename)
    
    def extract_filename_from_url(self, url):
        """Extract filename from a URL (typically an unrestricted download URL)"""
        try:
            import urllib.parse
            parsed_url = urllib.parse.urlparse(url)
            url_filename = parsed_url.path.split('/')[-1].split('?')[0]
            if url_filename and '.' in url_filename:
                if '%' in url_filename:
                    url_filename = urllib.parse.unquote(url_filename)
                return url_filename
        except:
            pass
        return 'download.mkv'
    
    def basic_sanitize_filename(self, filename):
        """Basic filename sanitization - only remove dangerous characters and limit length, preserve extension"""
        if not filename:
            return 'download'
        
        # Remove or replace dangerous characters for Windows/filesystem compatibility
        import re
        # Replace dangerous characters with underscores
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
        # Remove control characters
        filename = re.sub(r'[\x00-\x1f\x7f]', '', filename)
        
        # Limit filename length while preserving extension
        max_length = 200  # Windows path limit consideration
        if len(filename) > max_length:
            name, ext = os.path.splitext(filename)
            # Keep the extension, truncate the name part
            name = name[:max_length - len(ext) - 1]  # -1 for safety
            filename = name + ext
            
        return filename
    
    def sanitize_filename(self, filename):
        """Legacy method - now just calls basic_sanitize_filename"""
        return self.basic_sanitize_filename(filename)
    
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
            
            response = requests.get(download_url, stream=True, timeout=30)
            response.raise_for_status()
            
            # Use the provided filename if available, otherwise extract from download URL
            if rd_filename:
                filename = rd_filename
                logging.info(f"Using provided filename: {filename}")
            else:
                # Extract filename directly from the download URL
                try:
                    url_filename = download_url.split('/')[-1].split('?')[0]
                    if '%' in url_filename:
                        import urllib.parse
                        url_filename = urllib.parse.unquote(url_filename)
                    filename = url_filename if url_filename else 'download.mkv'
                    logging.info(f"Extracted filename from download URL: {filename}")
                except:
                    filename = 'download.mkv'
                    logging.info(f"Using fallback filename: {filename}")
            
            # Only do basic filename sanitization (remove dangerous characters, limit length)
            original_filename = filename
            filename = self.basic_sanitize_filename(filename)
            
            if original_filename != filename:
                logging.info(f"Filename sanitized: '{original_filename}' -> '{filename}'")
            else:
                logging.info(f"Using filename as-is: '{filename}'")
            
            # Download to configured in_progress folder first
            os.makedirs(self.in_progress_folder, exist_ok=True)
            temp_path = os.path.join(self.in_progress_folder, filename)
            
            logging.info(f"Downloading to temporary location: {temp_path}")
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            with open(temp_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=self.chunk_size):
                    # Check if download was aborted - use processing_files as the source of truth
                    if file_path and file_path not in self.processing_files:
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
                            # Thread-safe update of individual file progress
                            with self.progress_lock:
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
            
            # Check if file is a compressed archive and extract it before moving to completed folder
            if self.auto_extract and self.is_archive_file(temp_path):
                logging.info(f"Detected archive file, attempting extraction: {temp_path}")
                
                # Validate archive before extraction
                if not self.validate_archive(temp_path):
                    logging.warning(f"Archive validation failed, treating as regular file: {temp_path}")
                else:
                    extraction_success = self.extract_archive_to_completed(temp_path, self.completed_folder)
                    if extraction_success:
                        logging.info(f"Archive extracted successfully to completed folder, removing original: {temp_path}")
                        try:
                            os.remove(temp_path)
                            # Mark file as completed (thread-safe)
                            with self.progress_lock:
                                if file_index is not None and file_path and file_path in self.file_downloads:
                                    if file_index < len(self.file_downloads[file_path]):
                                        self.file_downloads[file_path][file_index]['progress'] = 100
                                        self.file_downloads[file_path][file_index]['status'] = 'Extracted'
                            return  # Don't move the original file since we extracted it
                        except Exception as e:
                            logging.warning(f"Failed to remove original archive: {e}")
                    else:
                        logging.warning(f"Archive extraction failed, moving original to completed folder: {temp_path}")
                        # Still remove the failed archive from in_progress to avoid clutter
                        try:
                            failed_archive_path = os.path.join(self.completed_folder, f"FAILED_EXTRACT_{filename}")
                            os.rename(temp_path, failed_archive_path)
                            logging.info(f"Moved failed archive to: {failed_archive_path}")
                            with self.progress_lock:
                                if file_index is not None and file_path and file_path in self.file_downloads:
                                    if file_index < len(self.file_downloads[file_path]):
                                        self.file_downloads[file_path][file_index]['progress'] = 100
                                        self.file_downloads[file_path][file_index]['status'] = 'Failed Extraction'
                            return
                        except Exception as e:
                            logging.error(f"Failed to move failed archive: {e}")
            
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
                    
                    # Mark file as completed (thread-safe)
                    with self.progress_lock:
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
    
    def is_archive_file(self, file_path):
        """Check if file is a supported archive format"""
        archive_extensions = {
            '.zip', '.rar', '.7z', '.tar', '.tar.gz', '.tgz', 
            '.tar.bz2', '.tbz2', '.tar.xz', '.txz', '.gz', '.bz2', '.xz'
        }
        
        file_lower = file_path.lower()
        return any(file_lower.endswith(ext) for ext in archive_extensions)
    
    def validate_archive(self, archive_path):
        """Validate archive file before extraction to avoid corrupted/fake files"""
        try:
            file_size = os.path.getsize(archive_path)
            
            # Skip very small files (likely fake)
            if file_size < 1024:  # Less than 1KB
                logging.warning(f"Archive too small ({file_size} bytes), likely fake: {archive_path}")
                return False
            
            # Check for suspicious executable content in what should be media archives
            filename = os.path.basename(archive_path).lower()
            if any(suspicious in filename for suspicious in ['.exe', '.bat', '.cmd', '.scr', '.com']):
                logging.warning(f"Archive contains suspicious executable reference: {archive_path}")
                return False
            
            # Basic file header validation
            with open(archive_path, 'rb') as f:
                header = f.read(10)
                
                # ZIP file signature
                if archive_path.lower().endswith('.zip'):
                    if not header.startswith(b'PK'):
                        logging.warning(f"Invalid ZIP header: {archive_path}")
                        return False
                
                # RAR file signature
                elif archive_path.lower().endswith('.rar'):
                    if not (header.startswith(b'Rar!') or header.startswith(b'RE~^')):
                        logging.warning(f"Invalid RAR header: {archive_path}")
                        return False
                
                # 7z file signature
                elif archive_path.lower().endswith('.7z'):
                    if not header.startswith(b'7z\xbc\xaf\x27\x1c'):
                        logging.warning(f"Invalid 7z header: {archive_path}")
                        return False
            
            return True
            
        except Exception as e:
            logging.error(f"Archive validation error: {e}")
            return False
    
    def extract_archive(self, archive_path):
        """Extract archive file to the same directory and return success status"""
        extract_dir = os.path.dirname(archive_path)
        return self._extract_archive_internal(archive_path, extract_dir)
    
    def extract_archive_to_completed(self, archive_path, completed_folder):
        """Extract archive file directly to completed folder and return success status"""
        return self._extract_archive_internal(archive_path, completed_folder)
    
    def _extract_archive_internal(self, archive_path, extract_dir):
        """Internal method to extract archive file to specified directory"""
        try:
            import zipfile
            import tarfile
            import subprocess
            
            archive_name = os.path.splitext(os.path.basename(archive_path))[0]
            
            # Handle different archive types
            if archive_path.lower().endswith('.zip'):
                return self._extract_zip(archive_path, extract_dir)
            elif archive_path.lower().endswith('.rar'):
                return self._extract_rar(archive_path, extract_dir)
            elif archive_path.lower().endswith('.7z'):
                return self._extract_7z(archive_path, extract_dir)
            elif any(archive_path.lower().endswith(ext) for ext in ['.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tar.xz', '.txz']):
                return self._extract_tar(archive_path, extract_dir)
            elif any(archive_path.lower().endswith(ext) for ext in ['.gz', '.bz2', '.xz']):
                return self._extract_compressed(archive_path, extract_dir)
            else:
                logging.warning(f"Unsupported archive format: {archive_path}")
                return False
                
        except Exception as e:
            logging.error(f"Error extracting archive {archive_path}: {e}")
            return False
    
    def _extract_zip(self, archive_path, extract_dir):
        """Extract ZIP file"""
        try:
            import zipfile
            
            with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                # Extract directly to the specified directory
                zip_ref.extractall(extract_dir)
                
            logging.info(f"ZIP extraction completed: {archive_path} -> {extract_dir}")
            return True
            
        except Exception as e:
            logging.error(f"ZIP extraction failed: {e}")
            return False
    
    def _extract_rar(self, archive_path, extract_dir):
        """Extract RAR file using unrar command"""
        try:
            import subprocess
            
            # Try to use unrar command first (more reliable)
            os.makedirs(extract_dir, exist_ok=True)
            
            # Try different unrar commands
            unrar_commands = ['unrar', 'rar']
            
            for cmd in unrar_commands:
                try:
                    # Use -o+ to overwrite files and -inul to reduce output
                    result = subprocess.run([cmd, 'x', '-y', '-o+', '-inul', archive_path, extract_dir], 
                                          capture_output=True, text=True, timeout=300)
                    if result.returncode == 0:
                        logging.info(f"RAR extraction completed: {archive_path} -> {extract_dir}")
                        return True
                    else:
                        error_msg = result.stderr.strip() or result.stdout.strip()
                        logging.warning(f"RAR extraction failed with {cmd}: {error_msg}")
                        
                        # Check for specific error types
                        if 'CRC failed' in error_msg or 'checksum error' in error_msg.lower():
                            logging.error(f"RAR file appears corrupted (CRC failure): {archive_path}")
                            return False
                        elif 'password' in error_msg.lower():
                            logging.error(f"RAR file is password protected: {archive_path}")
                            return False
                            
                except FileNotFoundError:
                    continue
                except subprocess.TimeoutExpired:
                    logging.error(f"RAR extraction timed out: {archive_path}")
                    return False
            
            # If unrar is not available, try Python rarfile library
            try:
                import rarfile
                
                # Configure rarfile to be more permissive with CRC errors
                rarfile.UNRAR_TOOL = None  # Force use of Python implementation if available
                
                with rarfile.RarFile(archive_path) as rf:
                    # Check if password protected
                    if rf.needs_password():
                        logging.error(f"RAR file is password protected: {archive_path}")
                        return False
                    
                    # Test the archive first
                    try:
                        rf.testrar()
                    except rarfile.BadRarFile as e:
                        if 'CRC' in str(e) or 'checksum' in str(e).lower():
                            logging.error(f"RAR file corrupted (CRC failure): {archive_path} - {e}")
                            return False
                        else:
                            logging.warning(f"RAR file test failed, attempting extraction anyway: {e}")
                    
                    # Extract files
                    rf.extractall(extract_dir)
                    
                logging.info(f"RAR extraction completed with rarfile: {archive_path} -> {extract_dir}")
                return True
                
            except ImportError:
                logging.warning("RAR extraction failed: unrar command and rarfile library not available")
                return False
            except rarfile.BadRarFile as e:
                logging.error(f"RAR file is corrupted or invalid: {archive_path} - {e}")
                return False
            except Exception as e:
                error_str = str(e)
                if 'CRC' in error_str or 'checksum' in error_str.lower():
                    logging.error(f"RAR extraction failed due to corruption: {archive_path} - {e}")
                else:
                    logging.error(f"RAR extraction failed with rarfile: {archive_path} - {e}")
                return False
                
        except Exception as e:
            logging.error(f"RAR extraction failed: {e}")
            return False
    
    def _extract_7z(self, archive_path, extract_dir):
        """Extract 7z file using 7z command"""
        try:
            import subprocess
            
            os.makedirs(extract_dir, exist_ok=True)
            
            # Try different 7z commands
            sevenz_commands = ['7z', '7za', '7zr']
            
            for cmd in sevenz_commands:
                try:
                    result = subprocess.run([cmd, 'x', f'-o{extract_dir}', '-y', archive_path], 
                                          capture_output=True, text=True, timeout=300)
                    if result.returncode == 0:
                        logging.info(f"7z extraction completed: {archive_path} -> {extract_dir}")
                        return True
                    else:
                        logging.warning(f"7z extraction failed with {cmd}: {result.stderr}")
                except FileNotFoundError:
                    continue
                except subprocess.TimeoutExpired:
                    logging.error(f"7z extraction timed out: {archive_path}")
                    return False
            
            logging.warning("7z extraction failed: 7z command not available")
            return False
            
        except Exception as e:
            logging.error(f"7z extraction failed: {e}")
            return False
    
    def _extract_tar(self, archive_path, extract_dir):
        """Extract TAR file (including compressed variants)"""
        try:
            import tarfile
            
            os.makedirs(extract_dir, exist_ok=True)
            
            # Determine compression mode
            mode = 'r'
            if archive_path.lower().endswith(('.tar.gz', '.tgz')):
                mode = 'r:gz'
            elif archive_path.lower().endswith(('.tar.bz2', '.tbz2')):
                mode = 'r:bz2'
            elif archive_path.lower().endswith(('.tar.xz', '.txz')):
                mode = 'r:xz'
            
            with tarfile.open(archive_path, mode) as tar_ref:
                tar_ref.extractall(extract_dir)
                
            logging.info(f"TAR extraction completed: {archive_path} -> {extract_dir}")
            return True
            
        except Exception as e:
            logging.error(f"TAR extraction failed: {e}")
            return False
    
    def _extract_compressed(self, archive_path, extract_dir):
        """Extract single compressed files (gz, bz2, xz)"""
        try:
            import gzip
            import bz2
            import lzma
            
            # Determine output filename (remove compression extension)
            base_name = os.path.basename(archive_path)
            if base_name.lower().endswith('.gz'):
                output_name = base_name[:-3]
                opener = gzip.open
            elif base_name.lower().endswith('.bz2'):
                output_name = base_name[:-4]
                opener = bz2.open
            elif base_name.lower().endswith('.xz'):
                output_name = base_name[:-3]
                opener = lzma.open
            else:
                return False
            
            output_path = os.path.join(extract_dir, output_name)
            
            with opener(archive_path, 'rb') as compressed_file:
                with open(output_path, 'wb') as output_file:
                    output_file.write(compressed_file.read())
            
            logging.info(f"Compressed file extraction completed: {archive_path}")
            return True
            
        except Exception as e:
            logging.error(f"Compressed file extraction failed: {e}")
            return False

    
    def move_queue_item(self, file_path, direction):
        # Queue movement no longer supported since uploads are unlimited
        return False

def process_existing_magnets(magnets_folder, handler):
    """Process any existing magnet files in the folder"""
    try:
        magnet_files = [f for f in os.listdir(magnets_folder) if f.endswith('.magnet') or f.endswith('.torrent')]
        if magnet_files:
            logging.info(f"Found {len(magnet_files)} magnet/torrent files to process in {magnets_folder}")
        
        for filename in magnet_files:
            file_path = os.path.join(magnets_folder, filename)
            
            if file_path in handler.processing_files or file_path in handler.ready_to_download:
                logging.debug(f"Already processing: {filename}")
                continue
            
            # Check if file is in cooldown
            if file_path in handler.retry_cooldown:
                if time.time() < handler.retry_cooldown[file_path]:
                    continue
                else:
                    handler.retry_cooldown.pop(file_path, None)
                
            # Check if file is accessible
            try:
                with open(file_path, 'r') as f:
                    pass  # Just test if we can open it
                logging.info(f"Processing existing magnet: {filename}")
                handler.processing_files.add(file_path)
                handler.download_progress[file_path] = {'status': 'Starting', 'progress': 0, 'cache_progress': 0, 'download_progress': 0}
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
        
        # Get performance mode and auto extraction setting
        performance_mode = config.get('performance_mode', 'medium')
        auto_extract = config.get('auto_extract_archives', True)
        
        # Create handler
        handler = MagnetHandler(config_path, completed_downloads_folder, magnets_folder, completed_magnets_folder, in_progress_folder, failed_magnets_folder, performance_mode, client_name, None, auto_extract)
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
            
            # Determine destination folder based on media type
            filename = download['filename']
            media_info = self.extract_media_info(filename)
            
            download_clients = config.get('download_clients', {})
            destination_folder = None
            
            # Check if it's a TV show (has season/episode)
            if media_info['season'] and media_info['episode']:
                # TV show - use Sonarr if available
                if 'sonarr' in download_clients:
                    destination_folder = os.path.expandvars(download_clients['sonarr']['completed_downloads_folder'])
            # Check if it's a movie (has year, no season/episode)
            elif media_info['year']:
                # Movie - use Radarr if available
                if 'radarr' in download_clients:
                    destination_folder = os.path.expandvars(download_clients['radarr']['completed_downloads_folder'])
            
            # Fallback to manual downloads folder
            if not destination_folder:
                destination_folder = config.get('manual_downloads_folder', '')
                if not destination_folder:
                    destination_folder = os.path.join(os.path.expanduser('~'), 'Downloads', 'Debridarr_Manual')
                destination_folder = os.path.expandvars(destination_folder)
            
            os.makedirs(destination_folder, exist_ok=True)
            
            # Download the file
            self.download_progress[file_id] = {'progress': 0, 'status': 'Downloading'}
            response = requests.get(download_url, stream=True, timeout=30)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            filepath = os.path.join(destination_folder, filename)
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
    
    def locate_file(self, file_id):
        try:
            download = next((d for d in self.downloads if d['id'] == file_id), None)
            if not download:
                return {'success': False, 'message': 'Download not found'}
            
            with open(self.config_path, 'r') as f:
                config = yaml.safe_load(f)
            
            filename = download['filename']
            
            # Check manual downloads folder
            manual_folder = config.get('manual_downloads_folder', '')
            if not manual_folder:
                manual_folder = os.path.join(os.path.expanduser('~'), 'Downloads', 'Debridarr_Manual')
            manual_folder = os.path.expandvars(manual_folder)
            manual_path = os.path.join(manual_folder, filename)
            
            if os.path.exists(manual_path):
                import subprocess
                subprocess.Popen(f'explorer /select,"{manual_path}"')
                return {'success': True, 'message': 'Opened in Explorer'}
            
            # Check media library
            media_root = config.get('media_root_directory', '')
            if media_root and os.path.exists(media_root):
                for root, dirs, files in os.walk(media_root):
                    if filename in files:
                        file_path = os.path.join(root, filename)
                        import subprocess
                        subprocess.Popen(f'explorer /select,"{file_path}"')
                        return {'success': True, 'message': 'Opened in Explorer'}
            
            return {'success': False, 'message': 'File not found'}
        except Exception as e:
            logging.error(f'Locate error: {e}')
            return {'success': False, 'message': str(e)}

def main(shutdown_event=None):
    # All data in ProgramData for write access and preservation
    base_dir = 'C:\\ProgramData\\Debridarr'
    
    # Setup logging
    logs_dir = os.path.join(base_dir, 'logs')
    os.makedirs(logs_dir, exist_ok=True)
    log_file = os.path.join(logs_dir, 'debridarr.log')
    
    # Write startup marker
    with open(log_file, 'a') as f:
        f.write(f"\n\n=== APP STARTING {time.time()} ===\n")
    
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
        # Reload file types for existing handlers
        for client_name, handler, magnets_folder in handlers:
            handler.reload_file_types()
        # Setup new handlers (for new clients)
        handlers = setup_handlers(config_path, observer)
        web_ui.handlers = handlers  # Update WebUI's handlers reference
        logging.info("Configuration reloaded successfully")
    
    # Process existing magnet files for all clients
    for client_name, handler, magnets_folder in handlers:
        process_existing_magnets(magnets_folder, handler)
    
    try:
        observer.start()
        logging.info("Debridarr started - monitoring for magnet files")
        
        # Start web UI in separate thread with reload callback
        def run_web_ui():
            try:
                # Wait for port to be fully released from previous instance
                import socket
                for i in range(30):
                    try:
                        test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        test_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        test_sock.bind(('0.0.0.0', 3636))
                        test_sock.close()
                        logging.info(f"Port 3636 available after {i} seconds")
                        break
                    except OSError:
                        if i == 29:
                            logging.error("Port 3636 still in use after 30 seconds")
                            return
                        time.sleep(1)
                
                logging.info("Web UI thread starting Flask...")
                web_ui.run()
                logging.info("Flask run() returned (should not happen)")
            except Exception as e:
                logging.error(f"Web UI crashed: {e}", exc_info=True)
                raise
        
        logging.info("Creating WebUI instance...")
        web_ui = WebUI(config_path, handlers, debrid_manager=debrid_manager, reload_callback=reload_handlers, shutdown_event=shutdown_event)
        logging.info("Starting web thread...")
        web_thread = threading.Thread(target=run_web_ui, daemon=True)
        web_thread.start()
        logging.info("Web UI thread started, waiting for Flask to bind...")
        
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
        logging.info("Stopping observer...")
        observer.stop()
        observer.join()
        logging.info("Shutdown complete")

if __name__ == "__main__":
    main()