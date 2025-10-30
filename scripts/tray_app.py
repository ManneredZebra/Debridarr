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
            os.path.join(os.path.dirname(sys.executable), '..', 'icon.png'),  # From Program Files
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

def quit_action(icon, item):
    shutdown_event.set()
    icon.stop()
    os._exit(0)

def main():
    # Check for existing instance using mutex
    mutex_name = "Global\\Debridarr_SingleInstance"
    kernel32 = ctypes.windll.kernel32
    mutex = kernel32.CreateMutexW(None, True, mutex_name)
    
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        # App already running, just open the web UI
        os.system("start http://127.0.0.1:3636")
        sys.exit(0)
    
    try:
        # Start the main app in a separate thread
        app_thread = threading.Thread(target=lambda: app_main(shutdown_event), daemon=True)
        app_thread.start()
        
        # Create system tray icon
        icon = pystray.Icon(
            "Debridarr",
            create_image(),
            menu=pystray.Menu(
                pystray.MenuItem("Open Web UI", lambda: os.system("start http://127.0.0.1:3636")),
                pystray.MenuItem("Quit", quit_action)
            )
        )
        
        icon.run()
    finally:
        if mutex:
            kernel32.CloseHandle(mutex)

if __name__ == "__main__":
    main()