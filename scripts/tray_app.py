#!/usr/bin/env python3
import pystray
from PIL import Image
import threading
import sys
import os
import signal
import ctypes
from ctypes import wintypes
from app import main as app_main

# Global flag for shutdown
shutdown_event = threading.Event()

def create_image():
    # Try to load icon.png, fallback to simple icon
    try:
        # Check multiple possible locations
        possible_paths = [
            os.path.join(os.path.dirname(sys.executable), 'icon.png'),  # Same dir as exe
            os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'icon.png'),  # From scripts
            'icon.png'  # Current directory
        ]
        
        for icon_path in possible_paths:
            if os.path.exists(icon_path):
                return Image.open(icon_path)
                
        raise FileNotFoundError("Icon not found")
    except:
        # Fallback to simple icon
        width = 64
        height = 64
        image = Image.new('RGB', (width, height), color='black')
        from PIL import ImageDraw
        dc = ImageDraw.Draw(image)
        dc.rectangle([16, 16, 48, 48], fill='white')
        return image

def open_web_ui():
    os.system('start http://127.0.0.1:3636')

def quit_action(icon, item):
    icon.stop()

def main():
    # Start the main app in a separate thread
    app_thread = threading.Thread(target=lambda: app_main(shutdown_event), daemon=True)
    app_thread.start()
    
    # Wait for Flask to be ready and auto-open web UI
    def wait_and_open():
        import socket
        import time
        for i in range(30):  # Wait up to 30 seconds
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                result = sock.connect_ex(('127.0.0.1', 3636))
                sock.close()
                if result == 0:
                    time.sleep(0.5)  # Brief delay for server to be fully ready
                    os.system('start http://127.0.0.1:3636')
                    break
            except:
                pass
            time.sleep(1)
    
    threading.Thread(target=wait_and_open, daemon=True).start()
    
    # Create system tray icon
    icon = pystray.Icon(
        "Debridarr",
        create_image(),
        menu=pystray.Menu(
            pystray.MenuItem("Open Web UI", lambda: open_web_ui()),
            pystray.MenuItem("Quit", quit_action)
        )
    )
    
    icon.run()
    shutdown_event.set()
    os._exit(0)

if __name__ == "__main__":
    main()