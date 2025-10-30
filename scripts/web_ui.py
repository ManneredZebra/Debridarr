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
    def __init__(self, config_path, handlers):
        self.config_path = config_path
        self.handlers = handlers
        self.app = Flask(__name__)
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
                    os.path.join(os.path.dirname(sys.executable), '..', 'icon.png'),  # From Program Files
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
                    downloads.append({
                        'filename': filename,
                        'status': progress_info['status'],
                        'progress': progress_info['progress'],
                        'cache_progress': progress_info.get('cache_progress', 0),
                        'files_progress': progress_info.get('files_progress', 0),
                        'files': file_downloads
                    })
                status[client_name] = {
                    'active_downloads': len(handler.processing_files),
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
                        # Remove from tracking
                        handler.processing_files.discard(file_path)
                        handler.download_progress.pop(file_path, None)
                        handler.file_downloads.pop(file_path, None)
                        
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
                
                history = {}
                for client_name, client_config in config.get('download_clients', {}).items():
                    completed_magnets_folder = os.path.expandvars(client_config['completed_magnets_folder'])
                    if os.path.exists(completed_magnets_folder):
                        files = [f for f in os.listdir(completed_magnets_folder) if f.endswith('.magnet')]
                        history[client_name] = files
                    else:
                        history[client_name] = []
                return jsonify(history)
            except:
                return jsonify({})
                
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
                magnets_folder = os.path.expandvars(client_config['magnets_folder'])
                
                src_path = os.path.join(completed_magnets_folder, filename)
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
                
                # Clean in_progress and completed_downloads - remove all files not actively downloading
                for folder_key in ['in_progress_folder', 'completed_downloads_folder']:
                    folder_path = os.path.expandvars(client_config[folder_key])
                    if os.path.exists(folder_path):
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
                        'completed_downloads': 0
                    }
                    
                    magnets_folder = os.path.expandvars(client_config['magnets_folder'])
                    if os.path.exists(magnets_folder):
                        counts[client_name]['magnets'] = len([f for f in os.listdir(magnets_folder) if os.path.isfile(os.path.join(magnets_folder, f))])
                    
                    in_progress_folder = os.path.expandvars(client_config['in_progress_folder'])
                    if os.path.exists(in_progress_folder):
                        counts[client_name]['in_progress'] = len([f for f in os.listdir(in_progress_folder) if os.path.isfile(os.path.join(in_progress_folder, f))])
                    
                    completed_downloads_folder = os.path.expandvars(client_config['completed_downloads_folder'])
                    if os.path.exists(completed_downloads_folder):
                        counts[client_name]['completed_downloads'] = len([f for f in os.listdir(completed_downloads_folder) if os.path.isfile(os.path.join(completed_downloads_folder, f))])
                
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
                
                # If API token ends with '...', keep the existing one
                if 'real_debrid_api_token' in new_config:
                    if new_config['real_debrid_api_token'].endswith('...'):
                        new_config['real_debrid_api_token'] = existing_config.get('real_debrid_api_token', '')
                
                # Write updated config
                with open(self.config_path, 'w') as f:
                    yaml.dump(new_config, f, default_flow_style=False, sort_keys=False)
                
                return jsonify({'success': True, 'message': 'Configuration saved successfully.', 'recheck': True})
            except Exception as e:
                return jsonify({'success': False, 'message': str(e), 'recheck': False})
    
    def run(self):
        import logging
        logging.getLogger('werkzeug').setLevel(logging.WARNING)
        self.app.run(host='127.0.0.1', port=3636, debug=False, use_reloader=False)

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Debridarr Web UI</title>
    <link rel="icon" type="image/png" href="/favicon.ico">
    <style>
        body { margin: 0; font-family: Arial, sans-serif; background: #1a1a1a; color: #fff; }
        .container { display: flex; height: 100vh; }
        .sidebar { width: 250px; background: #2d2d2d; padding: 20px; }
        .content { flex: 1; padding: 20px; overflow-y: auto; }
        .nav-item { padding: 10px; margin: 5px 0; background: #3d3d3d; border-radius: 5px; cursor: pointer; }
        .nav-item:hover { background: #4d4d4d; }
        .nav-item.active { background: #007acc; }
        .section { display: none; }
        .section.active { display: block; }
        .download-item { background: #2d2d2d; padding: 15px; margin: 10px 0; border-radius: 5px; }
        .abort-btn { background: #dc3545; color: white; border: none; padding: 5px 10px; border-radius: 3px; cursor: pointer; }
        .retry-btn { background: #28a745; color: white; border: none; padding: 5px 10px; border-radius: 3px; cursor: pointer; margin-right: 5px; }
        .delete-btn { background: #dc3545; color: white; border: none; padding: 5px 10px; border-radius: 3px; cursor: pointer; }
        .progress-container { display: flex; gap: 10px; margin: 10px 0; }
        .progress-bar { flex: 1; height: 20px; background: #444; border-radius: 10px; position: relative; }
        .progress-fill { height: 100%; border-radius: 10px; transition: width 0.3s; }
        .progress-text { position: absolute; top: 0; left: 0; right: 0; text-align: center; line-height: 20px; color: white; font-size: 12px; }
        .cache-progress .progress-fill { background: #28a745; }
        .download-progress .progress-fill { background: #007acc; }
        .progress-label { font-size: 11px; color: #ccc; margin-bottom: 2px; }
        .logs { background: #000; padding: 15px; border-radius: 5px; height: 400px; overflow-y: auto; font-family: monospace; font-size: 12px; }
        .status-good { color: #28a745; }
        .status-active { color: #ffc107; }
        .settings-group { background: #2d2d2d; padding: 20px; margin: 15px 0; border-radius: 5px; }
        .settings-group h3 { margin-top: 0; }
        .form-row { margin: 15px 0; }
        .form-row label { display: block; margin-bottom: 5px; color: #ccc; }
        .form-row input { width: 100%; padding: 8px; background: #1a1a1a; border: 1px solid #444; border-radius: 3px; color: #fff; box-sizing: border-box; }
        .save-btn { background: #007acc; color: white; border: none; padding: 10px 20px; border-radius: 3px; cursor: pointer; margin-top: 10px; }
        .add-client-btn { background: #28a745; color: white; border: none; padding: 8px 15px; border-radius: 3px; cursor: pointer; margin-top: 10px; }
        .remove-client-btn { background: #dc3545; color: white; border: none; padding: 5px 10px; border-radius: 3px; cursor: pointer; float: right; }
        .warning-box { background: #dc3545; color: white; padding: 15px; margin: 15px 0; border-radius: 5px; border-left: 5px solid #a02a2a; }
    </style>
</head>
<body>
    <div class="container">
        <div class="sidebar">
            <div style="display: flex; align-items: center; margin-bottom: 20px;">
                <img src="/favicon.ico" width="32" height="32" style="margin-right: 10px;">
                <h2 style="margin: 0;">Debridarr</h2>
            </div>
            <div class="nav-item active" onclick="showSection('overview')">Overview</div>
            <div class="nav-item" onclick="showSection('downloads')">Active Downloads</div>
            <div class="nav-item" onclick="showSection('history')">History</div>
            <div class="nav-item" onclick="showSection('completed')">Completed Downloads</div>
            <div class="nav-item" onclick="showSection('logs')">Logs</div>
            <div class="nav-item" onclick="showSection('settings')" id="settings-nav">Settings <span id="settings-warning" style="display: none; color: #ffc107; margin-left: 5px;">⚠</span></div>
        </div>
        <div class="content">
            <div id="overview" class="section active">
                <h1>System Overview</h1>
                <div id="system-warnings"></div>
                <div id="status-cards"></div>
            </div>
            <div id="downloads" class="section">
                <h1>Active Downloads <span id="download-badge" style="background: #007acc; padding: 3px 10px; border-radius: 12px; font-size: 14px; margin-left: 10px;">0</span></h1>
                <div id="download-list"></div>
            </div>
            <div id="history" class="section">
                <h1>Download History</h1>
                <div id="history-list"></div>
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
            if (section === 'history') loadHistory();
            if (section === 'completed') loadCompleted();
            if (section === 'settings') loadSettings();
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
                    const statusCards = document.getElementById('status-cards');
                    const downloadList = document.getElementById('download-list');
                    
                    statusCards.innerHTML = '';
                    downloadList.innerHTML = '';
                    
                    // Count total active downloads
                    let totalDownloads = 0;
                    
                    Object.entries(data).forEach(([client, status]) => {
                        totalDownloads += status.active_downloads;
                        
                        // Status card
                        const card = document.createElement('div');
                        card.className = 'download-item';
                        const statusClass = status.active_downloads > 0 ? 'status-active' : 'status-good';
                        const clientCounts = counts[client] || {magnets: 0, in_progress: 0, completed_downloads: 0};
                        
                        card.innerHTML = `
                            <h3>${client.toUpperCase()}</h3>
                            <p class="${statusClass}">Active Downloads: ${status.active_downloads}</p>
                            <div style="font-size: 11px; color: #999; margin: 5px 0;">Folder File Counts:</div>
                            <div style="font-size: 12px; color: #ccc; margin: 10px 0;">
                                <div>Magnets: ${clientCounts.magnets}</div>
                                <div>In Progress: ${clientCounts.in_progress}</div>
                                <div>Completed: ${clientCounts.completed_downloads}</div>
                            </div>
                        `;
                        
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
                        
                        // Download items
                        status.downloads.forEach(download => {
                            const item = document.createElement('div');
                            item.className = 'download-item';
                            
                            // Add abort button at top
                            const abortBtn = document.createElement('button');
                            abortBtn.className = 'abort-btn';
                            abortBtn.textContent = 'Abort';
                            abortBtn.style.float = 'right';
                            abortBtn.onclick = function() { abortDownload(client, download.filename); };
                            item.appendChild(abortBtn);
                            
                            const contentDiv = document.createElement('div');
                            contentDiv.innerHTML = `
                                <strong>${client.toUpperCase()}</strong>: ${download.filename}
                                <div>${download.status}</div>
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

        function loadHistory() {
            fetch('/api/history')
                .then(r => r.json())
                .then(data => {
                    const historyList = document.getElementById('history-list');
                    historyList.innerHTML = '';
                    
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
                            
                            item.appendChild(label);
                            item.appendChild(retryBtn);
                            historyList.appendChild(item);
                        });
                    });
                    
                    if (historyList.innerHTML === '') {
                        historyList.innerHTML = '<div class="download-item">No download history</div>';
                    }
                })
                .catch(err => console.error('loadHistory error:', err));
        }

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
                `- Completed Downloads folder (old video files)\n\n` +
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
                    
                    // Download clients section
                    const clientsGroup = document.createElement('div');
                    clientsGroup.className = 'settings-group';
                    clientsGroup.innerHTML = '<h3>Download Clients</h3>';
                    
                    const clientsDiv = document.createElement('div');
                    clientsDiv.id = 'clients-list';
                    
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
            
            const baseDir = 'C:/Users/' + (prompt('Enter your Windows username:') || 'YourUser') + '/AppData/Local/Debridarr/content/' + name.toLowerCase();
            
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
            `;
            clientsDiv.appendChild(clientDiv);
        }

        function removeClient(name) {
            if (confirm(`Remove ${name.toUpperCase()} client? This will not delete any files.`)) {
                loadSettings();
            }
        }

        // Restore active tab on page load
        const savedTab = localStorage.getItem('activeTab') || 'overview';
        document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
        document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
        document.getElementById(savedTab).classList.add('active');
        const navItems = document.querySelectorAll('.nav-item');
        const sections = ['overview', 'downloads', 'history', 'completed', 'logs', 'settings'];
        const index = sections.indexOf(savedTab);
        if (index >= 0) navItems[index].classList.add('active');
        
        if (savedTab === 'logs') loadLogs();
        if (savedTab === 'history') loadHistory();
        if (savedTab === 'completed') loadCompleted();
        if (savedTab === 'settings') loadSettings();
        
        // Auto-refresh status every 5 seconds
        setInterval(loadStatus, 5000);
        // Health check every 10 minutes
        healthCheckInterval = setInterval(loadHealth, 600000);
        // Initial loads with retry for server startup
        setTimeout(loadStatus, 500);
        setTimeout(loadHealth, 1000);
    </script>
</body>
</html>
'''