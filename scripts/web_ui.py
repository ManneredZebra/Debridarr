#!/usr/bin/env python3
import os
import sys
import yaml
import json
import threading
import requests
from flask import Flask, render_template_string, jsonify, request
from datetime import datetime

class WebUI:
    def __init__(self, config_path, handlers, debrid_manager=None, reload_callback=None, shutdown_event=None):
        self.config_path = config_path
        self.handlers = handlers
        self.debrid_manager = debrid_manager
        self.reload_callback = reload_callback
        self.shutdown_event = shutdown_event
        self.app = Flask(__name__)
        self.server = None
        self.setup_routes()
        
    def setup_routes(self):
        @self.app.route('/')
        def index():
            return render_template_string(HTML_TEMPLATE)
            
        @self.app.route('/favicon.ico')
        def favicon():
            try:
                # Check multiple possible locations
                possible_paths = [
                    os.path.join(os.path.dirname(sys.executable), 'icon.png'),  # Same dir as exe
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'icon.png'),  # From scripts
                    'icon.png'  # Current directory
                ]
                
                for icon_path in possible_paths:
                    if os.path.exists(icon_path):
                        from flask import send_file
                        return send_file(os.path.abspath(icon_path), mimetype='image/png')
                        
                return '', 404
            except:
                return '', 404
            
        @self.app.route('/api/status')
        def get_status():
            status = {}
            for client_name, handler, _ in self.handlers:
                downloads = []
                for file_path in handler.processing_files:
                    filename = os.path.basename(file_path)
                    progress_info = handler.download_progress.get(file_path, {'status': 'Processing', 'progress': 0, 'cache_progress': 0, 'download_progress': 0})
                    file_downloads = handler.file_downloads.get(file_path, [])
                    
                    # Determine if this is uploading/caching or downloading
                    is_downloading = file_path in handler.downloading_files
                    phase = "Downloading" if is_downloading else "Uploading/Caching"
                    
                    downloads.append({
                        'filename': filename,
                        'filepath': file_path,
                        'status': progress_info['status'],
                        'progress': progress_info['progress'],
                        'cache_progress': progress_info.get('cache_progress', 0),
                        'files_progress': progress_info.get('files_progress', 0),
                        'files': file_downloads,
                        'queued': False,
                        'phase': phase
                    })
                # No longer using queued_files since uploads are unlimited
                status[client_name] = {
                    'active_downloads': len(handler.processing_files),
                    'downloading_count': len(handler.downloading_files),
                    'uploading_count': len(handler.processing_files) - len(handler.downloading_files),
                    'downloads': downloads
                }
            return jsonify(status)
            
        @self.app.route('/api/logs')
        def get_logs():
            try:
                base_dir = 'C:\\ProgramData\\Debridarr'
                log_file = os.path.join(base_dir, 'logs', 'debridarr.log')
                with open(log_file, 'r') as f:
                    content = f.read()
                    lines = content.split('\n')[-100:]  # Last 100 lines
                return jsonify({'logs': lines})
            except:
                return jsonify({'logs': ['No logs available']})
                
        @self.app.route('/api/health')
        def get_health():
            issues = []
            
            try:
                with open(self.config_path, 'r') as f:
                    config = yaml.safe_load(f)
            except:
                return jsonify({'issues': issues})
            
            # Check API reachability
            api_token = config.get('real_debrid_api_token', '')
            if api_token and api_token != 'YOUR_API_TOKEN_HERE':
                try:
                    response = requests.get(
                        'https://api.real-debrid.com/rest/1.0/user',
                        headers={'Authorization': f'Bearer {api_token}'},
                        timeout=5
                    )
                    if response.status_code == 401:
                        issues.append({
                            'message': 'Real-Debrid API authentication failed',
                            'solution': 'Update your API token in Settings tab with a valid token from https://real-debrid.com/apitoken'
                        })
                    elif response.status_code != 200:
                        issues.append({
                            'message': 'Cannot reach Real-Debrid API',
                            'solution': 'Check your internet connection and verify Real-Debrid service is online'
                        })
                except requests.RequestException:
                    issues.append({
                        'message': 'Network error connecting to Real-Debrid',
                        'solution': 'Check your internet connection and firewall settings'
                    })
            
            # Check directories are valid and reachable
            for client_name, client_config in config.get('download_clients', {}).items():
                for folder_key in ['magnets_folder', 'in_progress_folder', 'completed_magnets_folder', 'completed_downloads_folder']:
                    folder_path = os.path.expandvars(client_config.get(folder_key, ''))
                    if folder_path:
                        if not os.path.exists(folder_path):
                            issues.append({
                                'message': f'{client_name}: {folder_key.replace("_", " ").title()} not found',
                                'solution': f'Create directory: {folder_path}'
                            })
                        elif not os.access(folder_path, os.W_OK):
                            issues.append({
                                'message': f'{client_name}: Cannot write to {folder_key.replace("_", " ").title()}',
                                'solution': f'Grant write permissions to: {folder_path}'
                            })
            
            return jsonify({'issues': issues})
                
        @self.app.route('/api/abort/<client_name>/<path:filename>')
        def abort_download(client_name, filename):
            for name, handler, _ in self.handlers:
                if name == client_name:
                    file_path = None
                    for processing_file in handler.processing_files:
                        if filename in processing_file:
                            file_path = processing_file
                            break
                    if file_path:
                        # Delete torrent from Real-Debrid if it exists
                        torrent_id = handler.torrent_ids.get(file_path)
                        if torrent_id:
                            handler.delete_torrent(torrent_id)
                        
                        # Remove from all tracking
                        handler.processing_files.discard(file_path)
                        handler.downloading_files.discard(file_path)
                        handler.download_progress.pop(file_path, None)
                        handler.file_downloads.pop(file_path, None)
                        handler.torrent_ids.pop(file_path, None)
                        handler.ready_to_download.pop(file_path, None)
                        
                        # Remove magnet file if it exists
                        try:
                            if os.path.exists(file_path):
                                os.remove(file_path)
                        except:
                            pass
                        
                        return jsonify({'success': True, 'message': f'Aborted {filename}'})
            return jsonify({'success': False, 'message': 'Download not found'})
            
        @self.app.route('/api/history')
        def get_history():
            try:
                with open(self.config_path, 'r') as f:
                    config = yaml.safe_load(f)
                
                sort_by = request.args.get('sort', 'date_desc')  # date_desc, date_asc, name_asc, name_desc
                page = int(request.args.get('page', 1))
                per_page = 50
                
                all_files = []
                for client_name, client_config in config.get('download_clients', {}).items():
                    completed_magnets_folder = os.path.expandvars(client_config['completed_magnets_folder'])
                    if os.path.exists(completed_magnets_folder):
                        for filename in os.listdir(completed_magnets_folder):
                            if filename.endswith('.magnet'):
                                file_path = os.path.join(completed_magnets_folder, filename)
                                mtime = os.path.getmtime(file_path)
                                all_files.append({
                                    'client': client_name,
                                    'filename': filename,
                                    'timestamp': mtime
                                })
                
                # Sort files
                if sort_by == 'date_desc':
                    all_files.sort(key=lambda x: x['timestamp'], reverse=True)
                elif sort_by == 'date_asc':
                    all_files.sort(key=lambda x: x['timestamp'])
                elif sort_by == 'name_asc':
                    all_files.sort(key=lambda x: x['filename'])
                elif sort_by == 'name_desc':
                    all_files.sort(key=lambda x: x['filename'], reverse=True)
                
                # Paginate
                total = len(all_files)
                start = (page - 1) * per_page
                end = start + per_page
                paginated = all_files[start:end]
                
                return jsonify({
                    'files': paginated,
                    'total': total,
                    'page': page,
                    'per_page': per_page,
                    'total_pages': (total + per_page - 1) // per_page
                })
            except:
                return jsonify({'files': [], 'total': 0, 'page': 1, 'per_page': 50, 'total_pages': 0})
                
        @self.app.route('/api/completed')
        def get_completed():
            try:
                with open(self.config_path, 'r') as f:
                    config = yaml.safe_load(f)
                
                completed = {}
                for client_name, client_config in config.get('download_clients', {}).items():
                    completed_downloads_folder = os.path.expandvars(client_config['completed_downloads_folder'])
                    if os.path.exists(completed_downloads_folder):
                        files = [f for f in os.listdir(completed_downloads_folder) if os.path.isfile(os.path.join(completed_downloads_folder, f))]
                        completed[client_name] = files
                    else:
                        completed[client_name] = []
                return jsonify(completed)
            except:
                return jsonify({})
                
        @self.app.route('/api/retry/<client_name>/<path:filename>')
        def retry_download(client_name, filename):
            try:
                with open(self.config_path, 'r') as f:
                    config = yaml.safe_load(f)
                
                client_config = config.get('download_clients', {}).get(client_name)
                if not client_config:
                    return jsonify({'success': False, 'message': 'Client not found'})
                    
                completed_magnets_folder = os.path.expandvars(client_config['completed_magnets_folder'])
                failed_magnets_folder = os.path.expandvars(client_config.get('failed_magnets_folder', ''))
                magnets_folder = os.path.expandvars(client_config['magnets_folder'])
                
                # Check both completed and failed folders
                src_path = os.path.join(completed_magnets_folder, filename)
                if not os.path.exists(src_path) and failed_magnets_folder:
                    src_path = os.path.join(failed_magnets_folder, filename)
                
                dst_path = os.path.join(magnets_folder, filename)
                
                if os.path.exists(src_path):
                    import shutil
                    shutil.move(src_path, dst_path)
                    return jsonify({'success': True, 'message': f'Retrying {filename}'})
                else:
                    return jsonify({'success': False, 'message': 'File not found'})
            except Exception as e:
                return jsonify({'success': False, 'message': str(e)})
                
        @self.app.route('/api/delete/<client_name>/<path:filename>')
        def delete_file(client_name, filename):
            try:
                with open(self.config_path, 'r') as f:
                    config = yaml.safe_load(f)
                
                client_config = config.get('download_clients', {}).get(client_name)
                if not client_config:
                    return jsonify({'success': False, 'message': 'Client not found'})
                    
                completed_downloads_folder = os.path.expandvars(client_config['completed_downloads_folder'])
                file_path = os.path.join(completed_downloads_folder, filename)
                
                if os.path.exists(file_path):
                    os.remove(file_path)
                    return jsonify({'success': True, 'message': f'Deleted {filename}'})
                else:
                    return jsonify({'success': False, 'message': 'File not found'})
            except Exception as e:
                return jsonify({'success': False, 'message': str(e)})
        
        @self.app.route('/api/failed')
        def get_failed():
            try:
                with open(self.config_path, 'r') as f:
                    config = yaml.safe_load(f)
                
                failed = {}
                for client_name, client_config in config.get('download_clients', {}).items():
                    failed_magnets_folder = os.path.expandvars(client_config.get('failed_magnets_folder', ''))
                    if failed_magnets_folder and os.path.exists(failed_magnets_folder):
                        files = [f for f in os.listdir(failed_magnets_folder) if os.path.isfile(os.path.join(failed_magnets_folder, f))]
                        failed[client_name] = files
                    else:
                        failed[client_name] = []
                return jsonify(failed)
            except:
                return jsonify({})
        
        @self.app.route('/api/delete-failed/<client_name>/<path:filename>')
        def delete_failed(client_name, filename):
            try:
                with open(self.config_path, 'r') as f:
                    config = yaml.safe_load(f)
                
                client_config = config.get('download_clients', {}).get(client_name)
                if not client_config:
                    return jsonify({'success': False, 'message': 'Client not found'})
                    
                failed_magnets_folder = os.path.expandvars(client_config.get('failed_magnets_folder', ''))
                if not failed_magnets_folder:
                    return jsonify({'success': False, 'message': 'Failed folder not configured'})
                
                file_path = os.path.join(failed_magnets_folder, filename)
                
                if os.path.exists(file_path):
                    os.remove(file_path)
                    return jsonify({'success': True, 'message': f'Deleted {filename}'})
                else:
                    return jsonify({'success': False, 'message': 'File not found'})
            except Exception as e:
                return jsonify({'success': False, 'message': str(e)})
                
        @self.app.route('/api/cleanup/<client_name>')
        def cleanup_client(client_name):
            try:
                with open(self.config_path, 'r') as f:
                    config = yaml.safe_load(f)
                
                client_config = config.get('download_clients', {}).get(client_name)
                if not client_config:
                    return jsonify({'success': False, 'message': 'Client not found'})
                
                # Find handler for this client
                handler = None
                for name, h, _ in self.handlers:
                    if name == client_name:
                        handler = h
                        break
                
                if not handler:
                    return jsonify({'success': False, 'message': 'Handler not found'})
                
                # Collect all actively downloading filenames
                active_files = set()
                for file_path, files_list in handler.file_downloads.items():
                    for file_info in files_list:
                        # Extract just the filename from the path
                        filename = file_info['filename'].split('/')[-1].split('\\')[-1]
                        active_files.add(filename)
                
                deleted_count = 0
                
                # Clean magnets folder - remove all .magnet files not being processed
                magnets_folder = os.path.expandvars(client_config['magnets_folder'])
                if os.path.exists(magnets_folder):
                    for filename in os.listdir(magnets_folder):
                        file_path = os.path.join(magnets_folder, filename)
                        if file_path not in handler.processing_files:
                            try:
                                if os.path.isfile(file_path):
                                    os.remove(file_path)
                                    deleted_count += 1
                            except:
                                pass
                
                # Clean in_progress, completed_downloads, and failed_magnets - remove all files not actively downloading
                for folder_key in ['in_progress_folder', 'completed_downloads_folder', 'failed_magnets_folder']:
                    folder_path = os.path.expandvars(client_config.get(folder_key, ''))
                    if folder_path and os.path.exists(folder_path):
                        for filename in os.listdir(folder_path):
                            # Only remove if NOT in active downloads
                            if filename not in active_files:
                                file_path = os.path.join(folder_path, filename)
                                try:
                                    if os.path.isfile(file_path):
                                        os.remove(file_path)
                                        deleted_count += 1
                                except:
                                    pass
                
                return jsonify({'success': True, 'message': f'Cleaned up {deleted_count} files'})
            except Exception as e:
                return jsonify({'success': False, 'message': str(e)})
                
        @self.app.route('/api/config')
        def get_config():
            try:
                with open(self.config_path, 'r') as f:
                    config = yaml.safe_load(f)
                # Mask API token for display
                if 'real_debrid_api_token' in config:
                    token = config['real_debrid_api_token']
                    config['real_debrid_api_token'] = token[:8] + '...' if len(token) > 8 else '***'
                # Ensure file_categories exists for backward compatibility but not used
                if 'file_categories' not in config:
                    config['file_categories'] = {
                        'video': ['.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v', '.flv', '.webm', '.mpg', '.mpeg', '.ts'],
                        'audio': ['.mp3', '.flac', '.m4a', '.aac', '.ogg', '.opus', '.wav', '.wma'],
                        'audiobook': ['.m4b', '.mp3', '.m4a', '.aa', '.aax', '.flac'],
                        'ebook': ['.epub', '.mobi', '.azw', '.azw3', '.pdf', '.cbz', '.cbr']
                    }
                return jsonify(config)
            except Exception as e:
                return jsonify({'error': str(e)})
                
        @self.app.route('/api/folder-counts')
        def get_folder_counts():
            try:
                with open(self.config_path, 'r') as f:
                    config = yaml.safe_load(f)
                
                counts = {}
                for client_name, client_config in config.get('download_clients', {}).items():
                    counts[client_name] = {
                        'magnets': 0,
                        'in_progress': 0,
                        'completed_downloads': 0,
                        'failed_magnets': 0
                    }
                    
                    magnets_folder = os.path.expandvars(client_config['magnets_folder'])
                    if os.path.exists(magnets_folder):
                        counts[client_name]['magnets'] = len([f for f in os.listdir(magnets_folder) if os.path.isfile(os.path.join(magnets_folder, f)) and (f.endswith('.magnet') or f.endswith('.torrent'))])
                    
                    in_progress_folder = os.path.expandvars(client_config['in_progress_folder'])
                    if os.path.exists(in_progress_folder):
                        counts[client_name]['in_progress'] = len([f for f in os.listdir(in_progress_folder) if os.path.isfile(os.path.join(in_progress_folder, f))])
                    
                    completed_downloads_folder = os.path.expandvars(client_config['completed_downloads_folder'])
                    if os.path.exists(completed_downloads_folder):
                        counts[client_name]['completed_downloads'] = len([f for f in os.listdir(completed_downloads_folder) if os.path.isfile(os.path.join(completed_downloads_folder, f))])
                    
                    failed_magnets_folder = os.path.expandvars(client_config.get('failed_magnets_folder', ''))
                    if failed_magnets_folder and os.path.exists(failed_magnets_folder):
                        counts[client_name]['failed_magnets'] = len([f for f in os.listdir(failed_magnets_folder) if os.path.isfile(os.path.join(failed_magnets_folder, f))])
                
                return jsonify(counts)
            except Exception as e:
                return jsonify({})
        
        @self.app.route('/api/config', methods=['POST'])
        def save_config():
            try:
                new_config = request.json
                # Read existing config to preserve full API token if masked
                with open(self.config_path, 'r') as f:
                    existing_config = yaml.safe_load(f)
                
                # If API token ends with '...' or contains '...', keep the existing one
                if 'real_debrid_api_token' in new_config:
                    if '...' in new_config['real_debrid_api_token']:
                        new_config['real_debrid_api_token'] = existing_config.get('real_debrid_api_token', '')
                
                # Ensure file_categories exists for backward compatibility but not used
                if 'file_categories' not in new_config:
                    if 'file_categories' in existing_config:
                        new_config['file_categories'] = existing_config['file_categories']
                    else:
                        new_config['file_categories'] = {
                            'video': ['.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v', '.flv', '.webm', '.mpg', '.mpeg', '.ts'],
                            'audio': ['.mp3', '.flac', '.m4a', '.aac', '.ogg', '.opus', '.wav', '.wma'],
                            'audiobook': ['.m4b', '.mp3', '.m4a', '.aa', '.aax', '.flac'],
                            'ebook': ['.epub', '.mobi', '.azw', '.azw3', '.pdf', '.cbz', '.cbr']
                        }
                
                # Ensure auto_extract_archives has a default value
                if 'auto_extract_archives' not in new_config:
                    new_config['auto_extract_archives'] = True
                
                # Write updated config
                with open(self.config_path, 'w') as f:
                    yaml.dump(new_config, f, default_flow_style=False, sort_keys=False)
                
                # Trigger reload of handlers
                if self.reload_callback:
                    threading.Thread(target=self.reload_callback, daemon=True).start()
                
                return jsonify({'success': True, 'message': 'Configuration saved successfully.', 'recheck': True})
            except Exception as e:
                return jsonify({'success': False, 'message': str(e), 'recheck': False})
        
        @self.app.route('/api/debrid-downloads/sync', methods=['POST'])
        def sync_debrid_downloads():
            if self.debrid_manager:
                result = self.debrid_manager.sync_from_api()
                return jsonify(result)
            return jsonify({'success': False, 'message': 'Manager not available'})
        
        @self.app.route('/api/debrid-downloads')
        def get_debrid_downloads():
            if self.debrid_manager:
                search = request.args.get('search', '')
                sort_by = request.args.get('sort', 'date_desc')
                status_filter = request.args.get('status', 'all')
                page = int(request.args.get('page', 1))
                per_page = 50
                
                downloads = self.debrid_manager.get_downloads(search, sort_by, status_filter)
                total = len(downloads)
                start = (page - 1) * per_page
                end = start + per_page
                paginated = downloads[start:end]
                
                return jsonify({
                    'downloads': paginated, 
                    'total': total,
                    'page': page,
                    'per_page': per_page,
                    'total_pages': (total + per_page - 1) // per_page,
                    'progress': self.debrid_manager.download_progress
                })
            return jsonify({'downloads': [], 'total': 0, 'page': 1, 'per_page': 50, 'total_pages': 0, 'progress': {}})
        
        @self.app.route('/api/debrid-downloads/download/<file_id>', methods=['POST'])
        def download_debrid_file(file_id):
            if self.debrid_manager:
                result = self.debrid_manager.download_file(file_id)
                return jsonify(result)
            return jsonify({'success': False, 'message': 'Manager not available'})
        
        @self.app.route('/api/debrid-downloads/locate/<file_id>')
        def locate_debrid_file(file_id):
            if self.debrid_manager:
                result = self.debrid_manager.locate_file(file_id)
                return jsonify(result)
            return jsonify({'success': False, 'message': 'Manager not available'})
        
        @self.app.route('/api/queue/move/<client_name>/<direction>/<path:filename>')
        def move_queue(client_name, direction, filename):
            # Queue movement no longer supported since uploads are unlimited
            return jsonify({'success': False, 'message': 'Queue movement not supported - uploads are no longer queued'})
        
        @self.app.route('/api/test-arr', methods=['POST'])
        def test_arr_connection():
            try:
                data = request.json
                arr_url = data.get('url', '').rstrip('/')
                arr_api_key = data.get('api_key', '')
                
                if not arr_url or not arr_api_key:
                    return jsonify({'success': False, 'message': 'URL and API key required'})
                
                headers = {'X-Api-Key': arr_api_key}
                response = requests.get(f'{arr_url}/api/v3/system/status', headers=headers, timeout=10)
                
                if response.status_code == 200:
                    app_name = response.json().get('appName', 'Unknown')
                    version = response.json().get('version', 'Unknown')
                    return jsonify({'success': True, 'message': f'Connected to {app_name} v{version}'})
                elif response.status_code == 401:
                    return jsonify({'success': False, 'message': 'Invalid API key'})
                else:
                    return jsonify({'success': False, 'message': f'Connection failed: {response.status_code}'})
            except requests.RequestException as e:
                return jsonify({'success': False, 'message': f'Connection error: {str(e)}'})
            except Exception as e:
                return jsonify({'success': False, 'message': str(e)})
        
        @self.app.route('/api/manual-magnet', methods=['POST'])
        def submit_manual_magnet():
            try:
                data = request.json
                client_name = data.get('client_name', '')
                magnet_link = data.get('magnet_link', '').strip()
                
                if not client_name or not magnet_link:
                    return jsonify({'success': False, 'message': 'Client name and magnet link required'})
                
                if not magnet_link.startswith('magnet:'):
                    return jsonify({'success': False, 'message': 'Invalid magnet link format'})
                
                # Find the handler for this client
                handler = None
                magnets_folder = None
                for name, h, folder in self.handlers:
                    if name == client_name:
                        handler = h
                        magnets_folder = folder
                        break
                
                if not handler:
                    return jsonify({'success': False, 'message': f'Client "{client_name}" not found'})
                
                # Generate a unique filename for the magnet
                import hashlib
                import time
                magnet_hash = hashlib.md5(magnet_link.encode()).hexdigest()[:8]
                timestamp = int(time.time())
                filename = f"manual_{client_name}_{timestamp}_{magnet_hash}.magnet"
                
                # Write magnet file to the client's magnets folder
                magnet_file_path = os.path.join(magnets_folder, filename)
                with open(magnet_file_path, 'w') as f:
                    f.write(magnet_link)
                
                return jsonify({'success': True, 'message': f'Magnet submitted successfully to {client_name}'})
                
            except Exception as e:
                return jsonify({'success': False, 'message': str(e)})
    
    def run(self):
        import logging
        import socket
        from werkzeug.serving import make_server
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.WARNING)
        
        try:
            logging.info("Starting Flask on 0.0.0.0:3636...")
            server = make_server('0.0.0.0', 3636, self.app, threaded=True)
            server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            logging.info("Flask server bound, starting serve_forever...")
            server.serve_forever()
        except OSError as e:
            logging.error(f"Flask failed to bind to port 3636: {e}", exc_info=True)
            raise
        except Exception as e:
            logging.error(f"Flask startup error: {e}", exc_info=True)
            raise

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Debridarr Web UI</title>
    <link rel="icon" type="image/png" href="/favicon.ico">
    <style>
        body { margin: 0; font-family: Arial, sans-serif; background: #1a1a1a; color: #fff; font-size: 15px; }
        .container { display: flex; height: 100vh; }
        .sidebar { width: 250px; background: #2d2d2d; padding: 20px; }
        .content { flex: 1; padding: 20px; overflow-y: auto; }
        .nav-item { padding: 12px; margin: 5px 0; background: #3d3d3d; border-radius: 5px; cursor: pointer; font-size: 15px; }
        .nav-item:hover { background: #4d4d4d; }
        .nav-item.active { background: #007acc; }
        .section { display: none; }
        .section.active { display: block; }
        .download-item { background: #2d2d2d; padding: 15px; margin: 10px 0; border-radius: 5px; font-size: 15px; }
        .stat-card { flex: 1; min-width: 100px; background: #1a1a1a; padding: 12px; border-radius: 5px; text-align: center; }
        .stat-value { font-size: 24px; font-weight: bold; }
        .stat-label { font-size: 11px; color: #999; margin-top: 5px; }
        .abort-btn { background: #dc3545; color: white; border: none; padding: 8px 14px; border-radius: 3px; cursor: pointer; font-size: 14px; }
        .retry-btn { background: #28a745; color: white; border: none; padding: 8px 14px; border-radius: 3px; cursor: pointer; margin-right: 5px; font-size: 14px; }
        .delete-btn { background: #dc3545; color: white; border: none; padding: 8px 14px; border-radius: 3px; cursor: pointer; font-size: 14px; }
        .progress-container { display: flex; gap: 10px; margin: 10px 0; }
        .progress-bar { flex: 1; height: 24px; background: #444; border-radius: 10px; position: relative; }
        .progress-fill { height: 100%; border-radius: 10px; transition: width 0.3s; }
        .progress-text { position: absolute; top: 0; left: 0; right: 0; text-align: center; line-height: 24px; color: white; font-size: 14px; }
        .cache-progress .progress-fill { background: #28a745; }
        .download-progress .progress-fill { background: #007acc; }
        .progress-label { font-size: 13px; color: #ccc; margin-bottom: 2px; }
        .logs { background: #000; padding: 15px; border-radius: 5px; height: 400px; overflow-y: auto; font-family: monospace; font-size: 14px; }
        .status-good { color: #28a745; font-size: 15px; }
        .status-active { color: #ffc107; font-size: 15px; }
        .settings-group { background: #2d2d2d; padding: 20px; margin: 15px 0; border-radius: 5px; }
        .settings-group h3 { margin-top: 0; font-size: 20px; }
        .settings-group h4 { font-size: 18px; }
        .form-row { margin: 15px 0; }
        .form-row label { display: block; margin-bottom: 5px; color: #ccc; font-size: 14px; }
        .form-row input { width: 100%; padding: 10px; background: #1a1a1a; border: 1px solid #444; border-radius: 3px; color: #fff; box-sizing: border-box; font-size: 14px; }
        .save-btn { background: #007acc; color: white; border: none; padding: 12px 24px; border-radius: 3px; cursor: pointer; margin-top: 10px; font-size: 15px; }
        .add-client-btn { background: #28a745; color: white; border: none; padding: 10px 18px; border-radius: 3px; cursor: pointer; margin-top: 10px; font-size: 14px; }
        .remove-client-btn { background: #dc3545; color: white; border: none; padding: 6px 12px; border-radius: 3px; cursor: pointer; float: right; font-size: 13px; }
        .warning-box { background: #dc3545; color: white; padding: 15px; margin: 15px 0; border-radius: 5px; border-left: 5px solid #a02a2a; font-size: 15px; }
        h1 { font-size: 28px; }
        h3 { font-size: 20px; }
        select { font-size: 14px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="sidebar">
            <div style="display: flex; align-items: center; margin-bottom: 20px;">
                <img src="/favicon.ico" width="64" height="64" style="margin-right: 15px;">
                <h2 style="margin: 0; font-size: 32px;">Debridarr</h2>
            </div>
            <div class="nav-item active" onclick="showSection('overview')">Overview</div>
            <div class="nav-item" onclick="showSection('downloads')">Active Downloads</div>
            <div class="nav-item" onclick="showSection('manual-upload')">Manual Upload</div>
            <div class="nav-item" onclick="showSection('history')">History</div>
            <div class="nav-item" onclick="showSection('failed')">Failed Downloads</div>
            <div class="nav-item" onclick="showSection('debrid-downloads')">Debrid Downloads</div>
            <div class="nav-item" onclick="showSection('completed')">Completed Downloads</div>
            <div class="nav-item" onclick="showSection('logs')">Logs</div>
            <div class="nav-item" onclick="showSection('settings')" id="settings-nav">Settings <span id="settings-warning" style="display: none; color: #ffc107; margin-left: 5px;">⚠</span></div>
        </div>
        <div class="content">
            <div id="overview" class="section active">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
                    <h1 style="margin: 0;">System Overview</h1>
                    <button onclick="resetClientOrder()" style="background: #6c757d; color: white; border: none; padding: 8px 16px; border-radius: 3px; cursor: pointer; font-size: 14px;">
                        Reset Order
                    </button>
                </div>
                <div id="system-warnings"></div>
                <div id="status-cards"></div>
            </div>
            <div id="downloads" class="section">
                <h1>Active Downloads <span id="download-badge" style="background: #007acc; padding: 3px 10px; border-radius: 12px; font-size: 14px; margin-left: 10px;">0</span></h1>
                <div id="download-list"></div>
            </div>
            <div id="manual-upload" class="section">
                <h1>Manual Upload</h1>
                <div style="margin-bottom: 20px; color: #ccc;">
                    Submit magnet links directly to your download clients. This page doesn't auto-refresh, so your text won't disappear.
                </div>
                <div id="manual-upload-clients"></div>
            </div>
            <div id="history" class="section">
                <h1>Download History</h1>
                <div style="margin: 10px 0;">
                    <label style="color: #ccc; margin-right: 10px;">Sort by:</label>
                    <select id="history-sort" style="padding: 5px; background: #2d2d2d; color: #fff; border: 1px solid #444; border-radius: 3px;">
                        <option value="date_desc">Date (Newest First)</option>
                        <option value="date_asc">Date (Oldest First)</option>
                        <option value="name_asc">Name (A-Z)</option>
                        <option value="name_desc">Name (Z-A)</option>
                    </select>
                </div>
                <div id="history-list"></div>
                <div id="history-pagination" style="margin: 20px 0; text-align: center;"></div>
            </div>
            <div id="failed" class="section">
                <h1>Failed Downloads</h1>
                <div id="failed-list"></div>
            </div>
            <div id="debrid-downloads" class="section">
                <h1>Debrid Downloads</h1>
                <button class="save-btn" onclick="syncDebridDownloads()" style="margin-bottom: 15px;">Sync Debrid Downloads</button>
                <div style="margin: 15px 0; display: flex; gap: 10px; flex-wrap: wrap;">
                    <div>
                        <label style="color: #ccc; margin-right: 5px;">Search:</label>
                        <input type="text" id="debrid-search" placeholder="Search filename..." style="padding: 5px; background: #2d2d2d; color: #fff; border: 1px solid #444; border-radius: 3px; font-size: 14px;">
                    </div>
                    <div>
                        <label style="color: #ccc; margin-right: 5px;">Sort by:</label>
                        <select id="debrid-sort" style="padding: 5px; background: #2d2d2d; color: #fff; border: 1px solid #444; border-radius: 3px; font-size: 14px;">
                            <option value="date_desc">Date (Newest First)</option>
                            <option value="date_asc">Date (Oldest First)</option>
                            <option value="name_asc">Name (A-Z)</option>
                            <option value="name_desc">Name (Z-A)</option>
                            <option value="size_desc">Size (Largest First)</option>
                            <option value="size_asc">Size (Smallest First)</option>
                        </select>
                    </div>
                    <div>
                        <label style="color: #ccc; margin-right: 5px;">Status:</label>
                        <select id="debrid-status" style="padding: 5px; background: #2d2d2d; color: #fff; border: 1px solid #444; border-radius: 3px; font-size: 14px;">
                            <option value="all">All</option>
                            <option value="Not Downloaded">Not Downloaded</option>
                            <option value="Already in Manual Downloads">Already in Manual Downloads</option>
                            <option value="Already in Media Library">Already in Media Library</option>
                            <option value="Unknown">Unknown</option>
                        </select>
                    </div>
                </div>
                <div id="debrid-list"></div>
                <div id="debrid-pagination" style="margin: 20px 0; text-align: center;"></div>
            </div>
            <div id="completed" class="section">
                <h1>Completed Downloads</h1>
                <div id="completed-list"></div>
            </div>
            <div id="logs" class="section">
                <h1>System Logs</h1>
                <div id="log-content" class="logs"></div>
            </div>
            <div id="settings" class="section">
                <h1>Settings</h1>
                <div id="settings-content"></div>
            </div>
        </div>
    </div>

    <script>
        window.onerror = function(msg, url, line, col, error) {
            console.error('Global error:', msg, 'at line', line, ':', col, error);
            return false;
        };
        
        function showSection(section) {
            document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
            document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
            document.getElementById(section).classList.add('active');
            if (event && event.target) event.target.classList.add('active');
            
            // Remember active tab
            localStorage.setItem('activeTab', section);
            
            if (section === 'logs') loadLogs();
            if (section === 'history') loadHistory(1);
            if (section === 'failed') loadFailed();
            if (section === 'debrid-downloads') loadDebridDownloads();
            if (section === 'completed') loadCompleted();
            if (section === 'settings') loadSettings();
            if (section === 'manual-upload') loadManualUpload();
        }

        let healthCheckInterval = null;
        
        function loadHealth() {
            fetch('/api/health')
                .then(r => r.json())
                .then(healthData => {
                    const systemWarnings = document.getElementById('system-warnings');
                    const settingsWarning = document.getElementById('settings-warning');
                    
                    if (systemWarnings) {
                        systemWarnings.innerHTML = '';
                        
                        if (healthData.issues && healthData.issues.length > 0) {
                            const warningBox = document.createElement('div');
                            warningBox.className = 'warning-box';
                            let html = '<strong>⚠ System Issues:</strong>';
                            healthData.issues.forEach(issue => {
                                html += '<div style="margin: 10px 0; padding: 10px; background: rgba(0,0,0,0.2); border-radius: 3px;">';
                                html += '<div style="font-weight: bold;">' + issue.message + '</div>';
                                html += '<div style="margin-top: 5px; font-size: 13px;">→ ' + issue.solution + '</div>';
                                html += '</div>';
                            });
                            warningBox.innerHTML = html;
                            systemWarnings.appendChild(warningBox);
                        }
                    }
                    
                    // Show/hide warning badge on Settings tab
                    if (settingsWarning) {
                        settingsWarning.style.display = (healthData.issues && healthData.issues.length > 0) ? 'inline' : 'none';
                    }
                })
                .catch(err => console.error('loadHealth error:', err));
        }
        
        function loadStatus() {
            Promise.all([
                fetch('/api/status').then(r => r.json()),
                fetch('/api/folder-counts').then(r => r.json())
            ])
                .then(([data, counts]) => {
                    console.log('Status data:', data);
                    
                    // Store data for use in moveClient function
                    window.lastStatusData = data;
                    
                    const statusCards = document.getElementById('status-cards');
                    const downloadList = document.getElementById('download-list');
                    
                    statusCards.innerHTML = '';
                    downloadList.innerHTML = '';
                    
                    // Count total active downloads
                    let totalDownloads = 0;
                    
                    // Get client order from config or use default alphabetical order
                    const clientOrder = window.clientOrder || [];
                    
                    // Ensure all clients are in the order array (in case new clients were added)
                    const allClients = Object.keys(data);
                    const orderedClients = [...clientOrder.filter(c => allClients.includes(c))];
                    allClients.forEach(client => {
                        if (!orderedClients.includes(client)) {
                            orderedClients.push(client);
                        }
                    });
                    
                    console.log('Client ordering:', {
                        allClients,
                        savedOrder: clientOrder,
                        finalOrder: orderedClients
                    });
                    
                    orderedClients.forEach((client, index) => {
                        const status = data[client];
                        if (!status) return;
                        totalDownloads += status.active_downloads;
                        
                        // Status card
                        const card = document.createElement('div');
                        card.className = 'download-item';
                        const statusClass = status.active_downloads > 0 ? 'status-active' : 'status-good';
                        const clientCounts = counts[client] || {magnets: 0, in_progress: 0, completed_downloads: 0};
                        
                        card.innerHTML = `
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                                <h3 style="margin: 0;">${client.toUpperCase()}</h3>
                                <div style="color: #666; font-size: 12px;">Position ${index + 1}</div>
                            </div>
                            <div style="display: flex; gap: 15px; margin: 15px 0; flex-wrap: wrap;">
                                <div style="flex: 1; min-width: 100px; background: #1a1a1a; padding: 12px; border-radius: 5px; text-align: center;">
                                    <div style="font-size: 24px; font-weight: bold; color: #28a745;">${status.uploading_count || 0}</div>
                                    <div style="font-size: 11px; color: #999; margin-top: 5px;">Uploading</div>
                                </div>
                                <div style="flex: 1; min-width: 100px; background: #1a1a1a; padding: 12px; border-radius: 5px; text-align: center;">
                                    <div style="font-size: 24px; font-weight: bold; color: #007acc;">${status.downloading_count || 0}</div>
                                    <div style="font-size: 11px; color: #999; margin-top: 5px;">Downloading</div>
                                </div>
                                <div style="flex: 1; min-width: 100px; background: #1a1a1a; padding: 12px; border-radius: 5px; text-align: center;">
                                    <div style="font-size: 24px; font-weight: bold; color: #ffc107;">${clientCounts.magnets}</div>
                                    <div style="font-size: 11px; color: #999; margin-top: 5px;">Magnets</div>
                                </div>
                                <div style="flex: 1; min-width: 100px; background: #1a1a1a; padding: 12px; border-radius: 5px; text-align: center;">
                                    <div style="font-size: 24px; font-weight: bold; color: #17a2b8;">${clientCounts.in_progress}</div>
                                    <div style="font-size: 11px; color: #999; margin-top: 5px;">In Progress</div>
                                </div>
                                <div style="flex: 1; min-width: 100px; background: #1a1a1a; padding: 12px; border-radius: 5px; text-align: center;">
                                    <div style="font-size: 24px; font-weight: bold; color: #28a745;">${clientCounts.completed_downloads}</div>
                                    <div style="font-size: 11px; color: #999; margin-top: 5px;">Completed</div>
                                </div>
                                <div style="flex: 1; min-width: 100px; background: #1a1a1a; padding: 12px; border-radius: 5px; text-align: center;">
                                    <div style="font-size: 24px; font-weight: bold; color: #dc3545;">${clientCounts.failed_magnets || 0}</div>
                                    <div style="font-size: 11px; color: #999; margin-top: 5px;">Failed</div>
                                </div>
                            </div>
                        `;
                        
                        // Add reorder buttons container
                        const reorderContainer = document.createElement('div');
                        reorderContainer.style.cssText = 'display: flex; gap: 5px; margin: 10px 0; justify-content: flex-end;';
                        
                        // Up button
                        if (index > 0) {
                            const upBtn = document.createElement('button');
                            upBtn.className = 'retry-btn';
                            upBtn.textContent = '↑ Move Up';
                            upBtn.style.cssText = 'background: #007acc; color: white; border: none; padding: 6px 12px; border-radius: 3px; cursor: pointer; font-size: 12px;';
                            upBtn.onclick = function() { moveClient(client, 'up'); };
                            reorderContainer.appendChild(upBtn);
                        }
                        
                        // Down button
                        if (index < orderedClients.length - 1) {
                            const downBtn = document.createElement('button');
                            downBtn.className = 'retry-btn';
                            downBtn.textContent = '↓ Move Down';
                            downBtn.style.cssText = 'background: #007acc; color: white; border: none; padding: 6px 12px; border-radius: 3px; cursor: pointer; font-size: 12px;';
                            downBtn.onclick = function() { moveClient(client, 'down'); };
                            reorderContainer.appendChild(downBtn);
                        }
                        
                        card.appendChild(reorderContainer);
                        
                        if (status.active_downloads > 0) {
                            const viewBtn = document.createElement('button');
                            viewBtn.className = 'retry-btn';
                            viewBtn.textContent = 'View Details';
                            viewBtn.style.marginRight = '5px';
                            viewBtn.onclick = function() { showSection('downloads'); };
                            card.appendChild(viewBtn);
                        }
                        
                        const cleanupBtn = document.createElement('button');
                        cleanupBtn.className = 'retry-btn';
                        cleanupBtn.textContent = 'Clean Up';
                        cleanupBtn.onclick = function() { cleanupClient(client); };
                        card.appendChild(cleanupBtn);
                        
                        statusCards.appendChild(card);
                        
                        // Sort downloads: downloading first, then by oldest first (assuming filename contains timestamp or order)
                        const sortedDownloads = [...status.downloads].sort((a, b) => {
                            // First priority: downloading phase comes before uploading
                            if (a.phase === 'Downloading' && b.phase !== 'Downloading') return -1;
                            if (b.phase === 'Downloading' && a.phase !== 'Downloading') return 1;
                            
                            // Second priority: sort by filename (oldest first - assuming timestamp in filename)
                            return a.filename.localeCompare(b.filename);
                        });
                        
                        // Download items
                        sortedDownloads.forEach(download => {
                            const item = document.createElement('div');
                            item.className = 'download-item';
                            
                            // Add phase indicator styling
                            if (download.phase === 'Downloading') {
                                item.style.borderLeft = '4px solid #007acc';
                            } else {
                                item.style.borderLeft = '4px solid #28a745';
                            }
                            
                            // Add buttons at top
                            const btnContainer = document.createElement('div');
                            btnContainer.style.cssText = 'float: right; display: flex; gap: 5px;';
                            
                            const abortBtn = document.createElement('button');
                            abortBtn.className = 'abort-btn';
                            abortBtn.textContent = 'Abort';
                            abortBtn.onclick = function() { abortDownload(client, download.filename); };
                            btnContainer.appendChild(abortBtn);
                            
                            item.appendChild(btnContainer);
                            
                            const contentDiv = document.createElement('div');
                            contentDiv.innerHTML = `
                                <strong>${client.toUpperCase()}</strong>: ${download.filename}
                                <div><span style="color: ${download.phase === 'Downloading' ? '#007acc' : '#28a745'};">📁 ${download.phase}</span> - ${download.status}</div>
                                <div class="progress-container">
                                    <div style="flex: 1;">
                                        <div class="progress-label">Real-Debrid Cache</div>
                                        <div class="progress-bar cache-progress">
                                            <div class="progress-fill" style="width: ${download.cache_progress}%"></div>
                                            <div class="progress-text">${download.cache_progress}%</div>
                                        </div>
                                    </div>
                                    <div style="flex: 1;">
                                        <div class="progress-label">Files Complete</div>
                                        <div class="progress-bar download-progress">
                                            <div class="progress-fill" style="width: ${download.files_progress || 0}%"></div>
                                            <div class="progress-text">${Math.round(download.files_progress || 0)}%</div>
                                        </div>
                                    </div>
                                </div>
                            `;
                            item.appendChild(contentDiv);
                            
                            // Add individual file progress bars if files exist
                            if (download.files && download.files.length > 0) {
                                const filesDiv = document.createElement('div');
                                filesDiv.style.marginTop = '10px';
                                filesDiv.innerHTML = '<strong>Individual Files:</strong>';
                                
                                download.files.forEach(file => {
                                    const fileDiv = document.createElement('div');
                                    fileDiv.style.cssText = 'margin: 5px 0; padding: 5px; background: #333; border-radius: 3px;';
                                    
                                    const displayName = file.filename.split('/').pop().split(String.fromCharCode(92)).pop().replace(/^[a-f0-9]{32,}[._-]?/i, '');
                                    
                                    const nameDiv = document.createElement('div');
                                    nameDiv.style.cssText = 'font-size: 12px; margin-bottom: 3px;';
                                    nameDiv.textContent = displayName + ' (' + file.status + ')';
                                    
                                    const progressDiv = document.createElement('div');
                                    progressDiv.className = 'progress-bar download-progress';
                                    progressDiv.style.cssText = 'height: 15px; margin: 0;';
                                    
                                    const fillDiv = document.createElement('div');
                                    fillDiv.className = 'progress-fill';
                                    fillDiv.style.width = file.progress + '%';
                                    
                                    const textDiv = document.createElement('div');
                                    textDiv.className = 'progress-text';
                                    textDiv.style.cssText = 'line-height: 15px; font-size: 10px;';
                                    textDiv.textContent = file.progress + '%';
                                    
                                    progressDiv.appendChild(fillDiv);
                                    progressDiv.appendChild(textDiv);
                                    fileDiv.appendChild(nameDiv);
                                    fileDiv.appendChild(progressDiv);
                                    filesDiv.appendChild(fileDiv);
                                });
                                
                                contentDiv.appendChild(filesDiv);
                            }
                            
                            downloadList.appendChild(item);
                        });
                    });
                    
                    if (downloadList.innerHTML === '') {
                        downloadList.innerHTML = '<div class="download-item">No active downloads</div>';
                    }
                    
                    // Update download badge
                    const badge = document.getElementById('download-badge');
                    if (badge) {
                        badge.textContent = totalDownloads;
                        badge.style.background = totalDownloads > 0 ? '#007acc' : '#666';
                    }
                })
                .catch(err => console.error('loadStatus error:', err));
        }

        function loadLogs() {
            fetch('/api/logs')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('log-content').innerHTML = data.logs.join('<br>');
                })
                .catch(err => console.error('loadLogs error:', err));
        }

        function abortDownload(client, filename) {
            if (confirm(`Are you sure you want to abort the download of "${filename}"?`)) {
                fetch(`/api/abort/${client}/${filename}`)
                    .then(r => r.json())
                    .then(data => {
                        alert(data.message);
                        loadStatus();
                    });
            }
        }

        function submitManualMagnet(client) {
            const input = document.getElementById(`magnet-input-${client}`);
            const magnetLink = input.value.trim();
            
            if (!magnetLink) {
                alert('Please enter a magnet link');
                return;
            }
            
            if (!magnetLink.startsWith('magnet:')) {
                alert('Invalid magnet link format');
                return;
            }
            
            fetch('/api/manual-magnet', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    client_name: client,
                    magnet_link: magnetLink
                })
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    alert(data.message);
                    input.value = ''; // Clear the input
                    delete window.magnetInputValues[client]; // Clear preserved value
                    loadStatus(); // Refresh the status
                } else {
                    alert('Error: ' + data.message);
                }
            })
            .catch(err => {
                console.error('Manual magnet submission error:', err);
                alert('Error submitting magnet link');
            });
        }

        // Client order management
        window.clientOrder = JSON.parse(localStorage.getItem('clientOrder')) || [];
        
        function moveClient(clientName, direction) {
            console.log('moveClient called:', clientName, direction);
            
            // Get the current display order (not just the saved order)
            const allClients = Object.keys(window.lastStatusData || {});
            const currentOrder = window.clientOrder || [];
            
            console.log('Current state:', {
                allClients,
                savedOrder: currentOrder
            });
            
            // Build the complete ordered list (same logic as in loadStatus)
            const orderedClients = [...currentOrder.filter(c => allClients.includes(c))];
            allClients.forEach(client => {
                if (!orderedClients.includes(client)) {
                    orderedClients.push(client);
                }
            });
            
            console.log('Ordered clients before move:', orderedClients);
            
            const currentIndex = orderedClients.indexOf(clientName);
            if (currentIndex === -1) {
                console.error('Client not found in ordered list:', clientName);
                return;
            }
            
            const newIndex = direction === 'up' ? currentIndex - 1 : currentIndex + 1;
            
            console.log('Move from index', currentIndex, 'to', newIndex);
            
            if (newIndex >= 0 && newIndex < orderedClients.length) {
                // Swap positions in the complete ordered list
                [orderedClients[currentIndex], orderedClients[newIndex]] = [orderedClients[newIndex], orderedClients[currentIndex]];
                
                console.log('New order:', orderedClients);
                
                // Save the new complete order
                window.clientOrder = orderedClients;
                localStorage.setItem('clientOrder', JSON.stringify(orderedClients));
                
                // Refresh display
                loadStatus();
            } else {
                console.log('Cannot move - index out of bounds');
            }
        }
        
        function resetClientOrder() {
            window.clientOrder = [];
            localStorage.removeItem('clientOrder');
            loadStatus();
        }

        let currentHistoryPage = 1;
        let currentHistorySort = 'date_desc';
        
        function loadHistory(page = 1) {
            currentHistoryPage = page;
            const sortSelect = document.getElementById('history-sort');
            if (sortSelect) {
                currentHistorySort = sortSelect.value;
            }
            
            fetch(`/api/history?sort=${currentHistorySort}&page=${page}`)
                .then(r => r.json())
                .then(data => {
                    const historyList = document.getElementById('history-list');
                    const pagination = document.getElementById('history-pagination');
                    historyList.innerHTML = '';
                    
                    if (data.files && data.files.length > 0) {
                        data.files.forEach(fileData => {
                            const item = document.createElement('div');
                            item.className = 'download-item';
                            
                            const date = new Date(fileData.timestamp * 1000).toLocaleString();
                            
                            const label = document.createElement('span');
                            label.innerHTML = `<strong>${fileData.client.toUpperCase()}</strong>: ${fileData.filename}<br><span style="font-size: 11px; color: #999;">Completed: ${date}</span> `;
                            
                            const retryBtn = document.createElement('button');
                            retryBtn.className = 'retry-btn';
                            retryBtn.textContent = 'Retry';
                            retryBtn.onclick = function() { retryDownload(fileData.client, fileData.filename); };
                            
                            item.appendChild(label);
                            item.appendChild(retryBtn);
                            historyList.appendChild(item);
                        });
                        
                        // Pagination controls
                        if (data.total_pages > 1) {
                            pagination.innerHTML = '';
                            
                            if (page > 1) {
                                const prevBtn = document.createElement('button');
                                prevBtn.className = 'retry-btn';
                                prevBtn.textContent = 'Previous';
                                prevBtn.onclick = () => loadHistory(page - 1);
                                pagination.appendChild(prevBtn);
                            }
                            
                            const pageInfo = document.createElement('span');
                            pageInfo.style.margin = '0 15px';
                            pageInfo.textContent = `Page ${page} of ${data.total_pages} (${data.total} total)`;
                            pagination.appendChild(pageInfo);
                            
                            if (page < data.total_pages) {
                                const nextBtn = document.createElement('button');
                                nextBtn.className = 'retry-btn';
                                nextBtn.textContent = 'Next';
                                nextBtn.onclick = () => loadHistory(page + 1);
                                pagination.appendChild(nextBtn);
                            }
                        } else {
                            pagination.innerHTML = '';
                        }
                    } else {
                        historyList.innerHTML = '<div class="download-item">No download history</div>';
                        pagination.innerHTML = '';
                    }
                })
                .catch(err => console.error('loadHistory error:', err));
        }
        
        // Add event listener for sort change
        document.addEventListener('DOMContentLoaded', function() {
            const sortSelect = document.getElementById('history-sort');
            if (sortSelect) {
                sortSelect.addEventListener('change', () => loadHistory(1));
            }
        });

        function loadCompleted() {
            fetch('/api/completed')
                .then(r => r.json())
                .then(data => {
                    const completedList = document.getElementById('completed-list');
                    completedList.innerHTML = '';
                    
                    Object.entries(data).forEach(([client, files]) => {
                        files.forEach(file => {
                            const item = document.createElement('div');
                            item.className = 'download-item';
                            
                            const label = document.createElement('span');
                            label.innerHTML = `<strong>${client.toUpperCase()}</strong>: ${file} `;
                            
                            const deleteBtn = document.createElement('button');
                            deleteBtn.className = 'delete-btn';
                            deleteBtn.textContent = 'Delete';
                            deleteBtn.onclick = function() { deleteFile(client, file); };
                            
                            item.appendChild(label);
                            item.appendChild(deleteBtn);
                            completedList.appendChild(item);
                        });
                    });
                    
                    if (completedList.innerHTML === '') {
                        completedList.innerHTML = '<div class="download-item">No completed downloads</div>';
                    }
                })
                .catch(err => console.error('loadCompleted error:', err));
        }

        function retryDownload(client, filename) {
            if (confirm(`Are you sure you want to retry the download of "${filename}"? This will move it back to the magnets folder.`)) {
                fetch(`/api/retry/${client}/${filename}`)
                    .then(r => r.json())
                    .then(data => {
                        alert(data.message);
                        loadHistory();
                        loadFailed();
                    });
            }
        }

        function loadFailed() {
            fetch('/api/failed')
                .then(r => r.json())
                .then(data => {
                    const failedList = document.getElementById('failed-list');
                    failedList.innerHTML = '';
                    
                    Object.entries(data).forEach(([client, files]) => {
                        files.forEach(file => {
                            const item = document.createElement('div');
                            item.className = 'download-item';
                            
                            const label = document.createElement('span');
                            label.innerHTML = `<strong>${client.toUpperCase()}</strong>: ${file} `;
                            
                            const retryBtn = document.createElement('button');
                            retryBtn.className = 'retry-btn';
                            retryBtn.textContent = 'Retry';
                            retryBtn.onclick = function() { retryDownload(client, file); };
                            
                            const deleteBtn = document.createElement('button');
                            deleteBtn.className = 'delete-btn';
                            deleteBtn.textContent = 'Remove';
                            deleteBtn.onclick = function() { deleteFailed(client, file); };
                            
                            item.appendChild(label);
                            item.appendChild(retryBtn);
                            item.appendChild(deleteBtn);
                            failedList.appendChild(item);
                        });
                    });
                    
                    if (failedList.innerHTML === '') {
                        failedList.innerHTML = '<div class="download-item">No failed downloads</div>';
                    }
                })
                .catch(err => console.error('loadFailed error:', err));
        }

        function deleteFailed(client, filename) {
            if (confirm(`Are you sure you want to remove ${filename}?`)) {
                fetch(`/api/delete-failed/${client}/${filename}`)
                    .then(r => r.json())
                    .then(data => {
                        alert(data.message);
                        loadFailed();
                    });
            }
        }

        function deleteFile(client, filename) {
            if (confirm(`Are you sure you want to delete ${filename}?`)) {
                fetch(`/api/delete/${client}/${filename}`)
                    .then(r => r.json())
                    .then(data => {
                        alert(data.message);
                        loadCompleted();
                    });
            }
        }

        function cleanupClient(client) {
            const message = `Clean Up will remove all leftover files from:\n\n` +
                `- Magnets folder (unprocessed .magnet files)\n` +
                `- In Progress folder (incomplete downloads)\n` +
                `- Completed Downloads folder (old video files)\n` +
                `- Failed Magnets folder (failed .magnet files)\n\n` +
                `Active downloads will NOT be affected.\n\n` +
                `Continue with cleanup for ${client.toUpperCase()}?`;
            
            if (confirm(message)) {
                fetch(`/api/cleanup/${client}`)
                    .then(r => r.json())
                    .then(data => {
                        alert(data.message);
                        loadStatus();
                    })
                    .catch(err => {
                        console.error('Cleanup error:', err);
                        alert('Cleanup failed: ' + err);
                    });
            }
        }

        function loadManualUpload() {
            fetch('/api/config')
                .then(r => r.json())
                .then(config => {
                    const manualUploadClients = document.getElementById('manual-upload-clients');
                    manualUploadClients.innerHTML = '';
                    
                    const clients = config.download_clients || {};
                    
                    if (Object.keys(clients).length === 0) {
                        manualUploadClients.innerHTML = '<div class="download-item">No download clients configured. Please configure clients in the Settings tab first.</div>';
                        return;
                    }
                    
                    // Get client order from localStorage (same as overview tab)
                    const clientOrder = window.clientOrder || [];
                    const allClients = Object.keys(clients);
                    
                    // Build ordered list (same logic as overview tab)
                    const orderedClients = [...clientOrder.filter(c => allClients.includes(c))];
                    allClients.forEach(client => {
                        if (!orderedClients.includes(client)) {
                            orderedClients.push(client);
                        }
                    });
                    
                    // Create upload sections in the same order as overview tab
                    orderedClients.forEach(client => {
                        const clientDiv = document.createElement('div');
                        clientDiv.className = 'download-item';
                        clientDiv.style.marginBottom = '20px';
                        
                        clientDiv.innerHTML = `
                            <h3 style="margin: 0 0 15px 0; color: #fff;">${client.toUpperCase()}</h3>
                            <div style="margin-bottom: 10px;">
                                <label style="display: block; margin-bottom: 5px; color: #ccc; font-size: 14px;">Magnet Link:</label>
                                <textarea id="manual-magnet-${client}" placeholder="Paste your magnet link here..." 
                                         style="width: 100%; height: 80px; padding: 10px; background: #1a1a1a; border: 1px solid #444; border-radius: 3px; color: #fff; font-size: 14px; resize: vertical; box-sizing: border-box;"></textarea>
                            </div>
                            <div style="display: flex; gap: 10px; align-items: center;">
                                <button onclick="submitManualMagnetFromTab('${client}')" 
                                        style="background: #28a745; color: white; border: none; padding: 10px 20px; border-radius: 3px; cursor: pointer; font-size: 14px;">
                                    Submit Magnet
                                </button>
                                <button onclick="clearManualMagnet('${client}')" 
                                        style="background: #6c757d; color: white; border: none; padding: 10px 20px; border-radius: 3px; cursor: pointer; font-size: 14px;">
                                    Clear
                                </button>
                                <div id="manual-status-${client}" style="color: #28a745; font-size: 14px; margin-left: 10px;"></div>
                            </div>
                            <div style="font-size: 12px; color: #999; margin-top: 10px;">
                                You can also drop .torrent files directly into the magnets folder: <br>
                                <code style="background: #333; padding: 2px 4px; border-radius: 2px;">${clients[client].magnets_folder}</code>
                            </div>
                        `;
                        
                        manualUploadClients.appendChild(clientDiv);
                    });
                })
                .catch(err => {
                    console.error('loadManualUpload error:', err);
                    document.getElementById('manual-upload-clients').innerHTML = '<div class="download-item">Error loading manual upload interface</div>';
                });
        }

        function submitManualMagnetFromTab(client) {
            const textarea = document.getElementById(`manual-magnet-${client}`);
            const statusDiv = document.getElementById(`manual-status-${client}`);
            const magnetLink = textarea.value.trim();
            
            // Clear previous status
            statusDiv.textContent = '';
            
            if (!magnetLink) {
                statusDiv.textContent = 'Please enter a magnet link';
                statusDiv.style.color = '#dc3545';
                return;
            }
            
            if (!magnetLink.startsWith('magnet:')) {
                statusDiv.textContent = 'Invalid magnet link format';
                statusDiv.style.color = '#dc3545';
                return;
            }
            
            statusDiv.textContent = 'Submitting...';
            statusDiv.style.color = '#ffc107';
            
            fetch('/api/manual-magnet', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    client_name: client,
                    magnet_link: magnetLink
                })
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    statusDiv.textContent = 'Magnet submitted successfully!';
                    statusDiv.style.color = '#28a745';
                    textarea.value = ''; // Clear the textarea
                    
                    // Clear status after 3 seconds
                    setTimeout(() => {
                        statusDiv.textContent = '';
                    }, 3000);
                } else {
                    statusDiv.textContent = 'Error: ' + data.message;
                    statusDiv.style.color = '#dc3545';
                }
            })
            .catch(err => {
                console.error('Manual magnet submission error:', err);
                statusDiv.textContent = 'Error submitting magnet link';
                statusDiv.style.color = '#dc3545';
            });
        }

        function clearManualMagnet(client) {
            const textarea = document.getElementById(`manual-magnet-${client}`);
            const statusDiv = document.getElementById(`manual-status-${client}`);
            textarea.value = '';
            statusDiv.textContent = '';
        }

        function loadSettings() {
            fetch('/api/config')
                .then(r => r.json())
                .then(config => {
                    const settingsContent = document.getElementById('settings-content');
                    settingsContent.innerHTML = '';
                    
                    // API Token section
                    const apiGroup = document.createElement('div');
                    apiGroup.className = 'settings-group';
                    apiGroup.innerHTML = `
                        <h3>Real-Debrid API Token</h3>
                        <div class="form-row">
                            <label>API Token:</label>
                            <input type="password" id="api-token" value="${config.real_debrid_api_token || ''}">
                        </div>
                    `;
                    settingsContent.appendChild(apiGroup);
                    
                    // Manual downloads settings
                    const manualGroup = document.createElement('div');
                    manualGroup.className = 'settings-group';
                    manualGroup.innerHTML = `
                        <h3>Manual Downloads Settings</h3>
                        <div class="form-row">
                            <label>Manual Downloads Folder:</label>
                            <input type="text" id="manual-downloads-folder" value="${config.manual_downloads_folder || ''}" placeholder="Leave empty for default Downloads folder">
                        </div>
                        <div class="form-row">
                            <label>Media Root Directory (Optional):</label>
                            <input type="text" id="media-root-directory" value="${config.media_root_directory || ''}" placeholder="e.g., D:/Media - Used to check if files already exist">
                        </div>
                        <div class="form-row">
                            <label>Debrid Sync Limit:</label>
                            <input type="number" id="debrid-sync-limit" value="${config.debrid_sync_limit || 100}" placeholder="100" min="1" max="2500">
                        </div>
                    `;
                    settingsContent.appendChild(manualGroup);
                    
                    // Performance settings
                    const perfGroup = document.createElement('div');
                    perfGroup.className = 'settings-group';
                    perfGroup.innerHTML = `
                        <h3>Performance Mode</h3>
                        <div class="form-row">
                            <label>Performance Mode:</label>
                            <select id="performance-mode" style="padding: 8px; background: #1a1a1a; border: 1px solid #444; border-radius: 3px; color: #fff; width: 100%; font-size: 14px;">
                                <option value="low" ${config.performance_mode === 'low' ? 'selected' : ''}>Low</option>
                                <option value="medium" ${!config.performance_mode || config.performance_mode === 'medium' ? 'selected' : ''}>Medium (Default)</option>
                                <option value="high" ${config.performance_mode === 'high' ? 'selected' : ''}>High</option>
                            </select>
                            <div style="font-size: 12px; color: #999; margin-top: 8px;">
                                <strong>Low:</strong> 1 concurrent download, minimal resource usage<br>
                                <strong>Medium:</strong> 2 concurrent downloads, balanced performance<br>
                                <strong>High:</strong> 4 concurrent downloads, faster processing but higher CPU/network usage<br><br>
                                Higher modes download faster but may affect your computer's performance during active downloads.
                            </div>
                        </div>
                        
                        <div class="form-row">
                            <label style="display: flex; align-items: center; cursor: pointer;">
                                <input type="checkbox" id="auto-extract" ${config.auto_extract_archives !== false ? 'checked' : ''} 
                                       style="margin-right: 8px; transform: scale(1.2);">
                                Automatically extract compressed archives
                            </label>
                            <div style="font-size: 12px; color: #999; margin-top: 8px;">
                                When enabled, downloaded ZIP, RAR, 7Z, and TAR files will be automatically extracted and the original archive will be removed. 
                                Supports: .zip, .rar, .7z, .tar, .tar.gz, .tar.bz2, .tar.xz, .gz, .bz2, .xz
                            </div>
                        </div>
                    `;
                    settingsContent.appendChild(perfGroup);
                    
                    // Download clients section
                    const clientsGroup = document.createElement('div');
                    clientsGroup.className = 'settings-group';
                    clientsGroup.innerHTML = '<h3>Download Clients</h3>';
                    
                    const clientsDiv = document.createElement('div');
                    clientsDiv.id = 'clients-list';
                    
                    const fileCategories = config.file_categories || {};
                    
                    Object.entries(config.download_clients || {}).forEach(([name, clientConfig]) => {
                        const clientDiv = document.createElement('div');
                        clientDiv.className = 'settings-group';
                        clientDiv.style.background = '#3d3d3d';
                        
                        clientDiv.innerHTML = `
                            <button class="remove-client-btn" onclick="removeClient('${name}')">Remove</button>
                            <h4>${name.toUpperCase()}</h4>
                            <div class="form-row">
                                <label>Magnets Folder:</label>
                                <input type="text" class="client-field" data-client="${name}" data-field="magnets_folder" value="${clientConfig.magnets_folder}">
                            </div>
                            <div class="form-row">
                                <label>In Progress Folder:</label>
                                <input type="text" class="client-field" data-client="${name}" data-field="in_progress_folder" value="${clientConfig.in_progress_folder}">
                            </div>
                            <div class="form-row">
                                <label>Completed Magnets Folder:</label>
                                <input type="text" class="client-field" data-client="${name}" data-field="completed_magnets_folder" value="${clientConfig.completed_magnets_folder}">
                            </div>
                            <div class="form-row">
                                <label>Completed Downloads Folder:</label>
                                <input type="text" class="client-field" data-client="${name}" data-field="completed_downloads_folder" value="${clientConfig.completed_downloads_folder}">
                            </div>
                            <div class="form-row">
                                <label>Failed Magnets Folder:</label>
                                <input type="text" class="client-field" data-client="${name}" data-field="failed_magnets_folder" value="${clientConfig.failed_magnets_folder || ''}">
                            </div>
                            <div class="form-row">
                                <label>${name.charAt(0).toUpperCase() + name.slice(1)} URL (Optional - for failure reporting):</label>
                                <input type="text" class="client-field" data-client="${name}" data-field="arr_url" id="arr-url-${name}" value="${clientConfig.arr_url || ''}" placeholder="http://localhost:8989 or http://localhost:7878">
                            </div>
                            <div class="form-row">
                                <label>${name.charAt(0).toUpperCase() + name.slice(1)} API Key (Optional):</label>
                                <input type="password" class="client-field" data-client="${name}" data-field="arr_api_key" id="arr-key-${name}" value="${clientConfig.arr_api_key || ''}" placeholder="API key from ${name.charAt(0).toUpperCase() + name.slice(1)} settings">
                                <button class="retry-btn" onclick="testArrConnection('${name}')" style="margin-top: 5px;">Test Connection</button>
                            </div>
                        `;
                        clientsDiv.appendChild(clientDiv);
                    });
                    
                    clientsGroup.appendChild(clientsDiv);
                    
                    const addBtn = document.createElement('button');
                    addBtn.className = 'add-client-btn';
                    addBtn.textContent = 'Add New Client';
                    addBtn.onclick = addNewClient;
                    clientsGroup.appendChild(addBtn);
                    
                    settingsContent.appendChild(clientsGroup);
                    
                    // Save button
                    const saveBtn = document.createElement('button');
                    saveBtn.className = 'save-btn';
                    saveBtn.textContent = 'Save Configuration';
                    saveBtn.onclick = saveSettings;
                    settingsContent.appendChild(saveBtn);
                })
                .catch(err => console.error('loadSettings error:', err));
        }

        function saveSettings() {
            const config = {
                real_debrid_api_token: document.getElementById('api-token').value,
                manual_downloads_folder: document.getElementById('manual-downloads-folder').value,
                media_root_directory: document.getElementById('media-root-directory').value,
                debrid_sync_limit: parseInt(document.getElementById('debrid-sync-limit').value) || 100,
                performance_mode: document.getElementById('performance-mode').value,
                auto_extract_archives: document.getElementById('auto-extract').checked,
                download_clients: {}
            };
            
            document.querySelectorAll('.client-field').forEach(input => {
                const client = input.dataset.client;
                const field = input.dataset.field;
                if (!config.download_clients[client]) {
                    config.download_clients[client] = {};
                }
                config.download_clients[client][field] = input.value;
            });
            
            fetch('/api/config', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(config)
            })
                .then(r => r.json())
                .then(data => {
                    alert(data.message);
                    if (data.success) {
                        loadSettings();
                        loadHealth(); // Recheck health immediately after settings change
                    }
                })
                .catch(err => {
                    console.error('saveSettings error:', err);
                    alert('Failed to save settings');
                });
        }

        function addNewClient() {
            const name = prompt('Enter client name (e.g., lidarr, readarr):');
            if (!name) return;
            
            fetch('/api/config')
                .then(r => r.json())
                .then(config => {
                    const baseDir = 'C:/ProgramData/Debridarr/' + name.toLowerCase();
                    
                    const clientsDiv = document.getElementById('clients-list');
                    const clientDiv = document.createElement('div');
                    clientDiv.className = 'settings-group';
                    clientDiv.style.background = '#3d3d3d';
                    clientDiv.innerHTML = `
                        <button class="remove-client-btn" onclick="removeClient('${name}')">Remove</button>
                        <h4>${name.toUpperCase()}</h4>
                        <div class="form-row">
                            <label>Magnets Folder:</label>
                            <input type="text" class="client-field" data-client="${name}" data-field="magnets_folder" value="${baseDir}/magnets">
                        </div>
                        <div class="form-row">
                            <label>In Progress Folder:</label>
                            <input type="text" class="client-field" data-client="${name}" data-field="in_progress_folder" value="${baseDir}/in_progress">
                        </div>
                        <div class="form-row">
                            <label>Completed Magnets Folder:</label>
                            <input type="text" class="client-field" data-client="${name}" data-field="completed_magnets_folder" value="${baseDir}/completed_magnets">
                        </div>
                        <div class="form-row">
                            <label>Completed Downloads Folder:</label>
                            <input type="text" class="client-field" data-client="${name}" data-field="completed_downloads_folder" value="${baseDir}/completed_downloads">
                        </div>
                        <div class="form-row">
                            <label>Failed Magnets Folder:</label>
                            <input type="text" class="client-field" data-client="${name}" data-field="failed_magnets_folder" value="${baseDir}/failed_magnets">
                        </div>
                        <div class="form-row">
                            <label>${name.charAt(0).toUpperCase() + name.slice(1)} URL (Optional - for failure reporting):</label>
                            <input type="text" class="client-field" data-client="${name}" data-field="arr_url" id="arr-url-${name}" value="" placeholder="http://localhost:8989 or http://localhost:7878">
                        </div>
                        <div class="form-row">
                            <label>${name.charAt(0).toUpperCase() + name.slice(1)} API Key (Optional):</label>
                            <input type="password" class="client-field" data-client="${name}" data-field="arr_api_key" id="arr-key-${name}" value="" placeholder="API key from ${name.charAt(0).toUpperCase() + name.slice(1)} settings">
                            <button class="retry-btn" onclick="testArrConnection('${name}')" style="margin-top: 5px;">Test Connection</button>
                        </div>
                    `;
                    clientsDiv.appendChild(clientDiv);
                });
        }

        function removeClient(name) {
            if (confirm(`Remove ${name.toUpperCase()} client? This will not delete any files.`)) {
                loadSettings();
            }
        }
        
        function moveQueue(client, direction, filename) {
            fetch(`/api/queue/move/${client}/${direction}/${filename}`)
                .then(r => r.json())
                .then(data => {
                    if (!data.success && data.message) {
                        console.log(data.message);
                    }
                    loadStatus();
                })
                .catch(err => console.error('moveQueue error:', err));
        }
        
        function testArrConnection(clientName) {
            const url = document.getElementById(`arr-url-${clientName}`).value;
            const apiKey = document.getElementById(`arr-key-${clientName}`).value;
            
            if (!url || !apiKey) {
                alert('Please enter both URL and API key');
                return;
            }
            
            fetch('/api/test-arr', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({url: url, api_key: apiKey})
            })
                .then(r => r.json())
                .then(data => {
                    alert(data.message);
                })
                .catch(err => {
                    console.error('Test connection error:', err);
                    alert('Test failed: ' + err);
                });
        }

        function syncDebridDownloads() {
            if (!confirm('Sync Real-Debrid download history? This will fetch all downloads from your Real-Debrid account.')) return;
            
            fetch('/api/debrid-downloads/sync', {method: 'POST'})
                .then(r => r.json())
                .then(data => {
                    alert(data.message);
                    if (data.success) loadDebridDownloads();
                })
                .catch(err => {
                    console.error('Sync error:', err);
                    alert('Sync failed: ' + err);
                });
        }
        
        let currentDebridPage = 1;
        
        function loadDebridDownloads(page = 1) {
            currentDebridPage = page;
            const search = document.getElementById('debrid-search').value;
            const sort = document.getElementById('debrid-sort').value;
            const status = document.getElementById('debrid-status').value;
            
            fetch(`/api/debrid-downloads?search=${encodeURIComponent(search)}&sort=${sort}&status=${encodeURIComponent(status)}&page=${page}`)
                .then(r => r.json())
                .then(data => {
                    const debridList = document.getElementById('debrid-list');
                    debridList.innerHTML = '';
                    
                    if (data.downloads && data.downloads.length > 0) {
                        data.downloads.forEach(download => {
                            const item = document.createElement('div');
                            item.className = 'download-item';
                            item.id = 'debrid-' + download.id;
                            
                            const sizeGB = (download.filesize / (1024*1024*1024)).toFixed(2);
                            const date = download.generated ? new Date(download.generated).toLocaleString() : 'Unknown';
                            const statusColor = download.status === 'Already in Manual Downloads' ? '#28a745' : 
                                              download.status === 'Already in Media Library' ? '#007acc' : 
                                              download.status === 'Not Downloaded' ? '#ffc107' : '#999';
                            
                            item.innerHTML = `
                                <div style="margin-bottom: 8px;">
                                    <strong>${download.filename}</strong>
                                </div>
                                <div style="font-size: 13px; color: #ccc; margin: 5px 0;">
                                    Size: ${sizeGB} GB | Source: ${download.host || 'Unknown'} | Date: ${date}
                                </div>
                                <div style="margin: 5px 0;">
                                    <span style="color: ${statusColor}; font-weight: bold;">Status: ${download.status}</span>
                                </div>
                            `;
                            
                            // Show progress bar if downloading
                            const progress = data.progress[download.id];
                            if (progress) {
                                const progressDiv = document.createElement('div');
                                progressDiv.style.margin = '10px 0';
                                progressDiv.innerHTML = `
                                    <div style="font-size: 13px; color: #ccc; margin-bottom: 3px;">${progress.status}</div>
                                    <div class="progress-bar download-progress">
                                        <div class="progress-fill" style="width: ${progress.progress}%"></div>
                                        <div class="progress-text">${progress.progress}%</div>
                                    </div>
                                `;
                                item.appendChild(progressDiv);
                            } else if (download.status === 'Already in Manual Downloads' || download.status === 'Already in Media Library') {
                                const locateBtn = document.createElement('button');
                                locateBtn.className = 'retry-btn';
                                locateBtn.textContent = 'Show in Explorer';
                                locateBtn.onclick = () => locateDebridFile(download.id);
                                item.appendChild(locateBtn);
                            } else {
                                const downloadBtn = document.createElement('button');
                                downloadBtn.className = 'retry-btn';
                                downloadBtn.textContent = 'Download';
                                downloadBtn.onclick = () => downloadDebridFile(download.id);
                                item.appendChild(downloadBtn);
                            }
                            
                            debridList.appendChild(item);
                        });
                    } else {
                        debridList.innerHTML = '<div class="download-item">No downloads found. Click "Sync Debrid Downloads" to fetch from Real-Debrid.</div>';
                    }
                    
                    // Pagination controls
                    const pagination = document.getElementById('debrid-pagination');
                    if (data.total_pages > 1) {
                        pagination.innerHTML = '';
                        
                        if (page > 1) {
                            const prevBtn = document.createElement('button');
                            prevBtn.className = 'retry-btn';
                            prevBtn.textContent = 'Previous';
                            prevBtn.onclick = () => loadDebridDownloads(page - 1);
                            pagination.appendChild(prevBtn);
                        }
                        
                        const pageInfo = document.createElement('span');
                        pageInfo.style.margin = '0 15px';
                        pageInfo.textContent = `Page ${page} of ${data.total_pages} (${data.total} total)`;
                        pagination.appendChild(pageInfo);
                        
                        if (page < data.total_pages) {
                            const nextBtn = document.createElement('button');
                            nextBtn.className = 'retry-btn';
                            nextBtn.textContent = 'Next';
                            nextBtn.onclick = () => loadDebridDownloads(page + 1);
                            pagination.appendChild(nextBtn);
                        }
                    } else {
                        pagination.innerHTML = '';
                    }
                })
                .catch(err => console.error('loadDebridDownloads error:', err));
        }
        
        function downloadDebridFile(fileId) {
            if (!confirm('Download this file to your manual downloads folder?')) return;
            
            fetch(`/api/debrid-downloads/download/${fileId}`, {method: 'POST'})
                .then(r => r.json())
                .then(data => {
                    if (!data.success) {
                        alert(data.message);
                    }
                    // Start polling for progress
                    const pollInterval = setInterval(() => {
                        loadDebridDownloads();
                    }, 1000);
                    
                    // Stop polling after 5 minutes
                    setTimeout(() => clearInterval(pollInterval), 300000);
                })
                .catch(err => {
                    console.error('Download error:', err);
                    alert('Download failed: ' + err);
                });
        }
        
        function locateDebridFile(fileId) {
            fetch(`/api/debrid-downloads/locate/${fileId}`)
                .then(r => r.json())
                .then(data => {
                    if (!data.success) {
                        alert(data.message);
                    }
                })
                .catch(err => {
                    console.error('Locate error:', err);
                    alert('Failed to locate file: ' + err);
                });
        }
        
        // Event listeners for debrid downloads filters
        document.addEventListener('DOMContentLoaded', function() {
            const debridSearch = document.getElementById('debrid-search');
            const debridSort = document.getElementById('debrid-sort');
            const debridStatus = document.getElementById('debrid-status');
            
            if (debridSearch) debridSearch.addEventListener('input', () => loadDebridDownloads(1));
            if (debridSort) debridSort.addEventListener('change', () => loadDebridDownloads(1));
            if (debridStatus) debridStatus.addEventListener('change', () => loadDebridDownloads(1));
        });
        
        // Restore active tab on page load
        const savedTab = localStorage.getItem('activeTab') || 'overview';
        document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
        document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
        document.getElementById(savedTab).classList.add('active');
        const navItems = document.querySelectorAll('.nav-item');
        const sections = ['overview', 'downloads', 'manual-upload', 'history', 'failed', 'debrid-downloads', 'completed', 'logs', 'settings'];
        const index = sections.indexOf(savedTab);
        if (index >= 0) navItems[index].classList.add('active');
        
        if (savedTab === 'logs') loadLogs();
        if (savedTab === 'history') loadHistory(1);
        if (savedTab === 'failed') loadFailed();
        if (savedTab === 'debrid-downloads') loadDebridDownloads();
        if (savedTab === 'completed') loadCompleted();
        if (savedTab === 'settings') loadSettings();
        if (savedTab === 'manual-upload') loadManualUpload();
        
        // Global variable to preserve input values
        window.magnetInputValues = {};
        
        // Function to preserve input values
        function preserveMagnetInputs() {
            const inputs = document.querySelectorAll('[id^="magnet-input-"]');
            inputs.forEach(input => {
                const client = input.id.replace('magnet-input-', '');
                if (input.value.trim()) {
                    window.magnetInputValues[client] = input.value;
                }
            });
        }
        
        // Function to restore input values
        function restoreMagnetInputs() {
            Object.keys(window.magnetInputValues).forEach(client => {
                const input = document.getElementById(`magnet-input-${client}`);
                if (input && window.magnetInputValues[client]) {
                    input.value = window.magnetInputValues[client];
                }
            });
        }
        
        // Auto-refresh status every 2 seconds for better progress bar updates
        setInterval(() => {
            preserveMagnetInputs();
            loadStatus();
            setTimeout(restoreMagnetInputs, 100); // Small delay to ensure DOM is updated
        }, 2000);
        // Health check every 10 minutes
        healthCheckInterval = setInterval(loadHealth, 600000);
        // Initial loads with retry for server startup
        setTimeout(loadStatus, 500);
        setTimeout(loadHealth, 1000);
    </script>
</body>
</html>
'''