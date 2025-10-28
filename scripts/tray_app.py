#!/usr/bin/env python3
import pystray
from PIL import Image, ImageDraw
import threading
import sys
import ctypes
from ctypes import wintypes
from app import main as app_main

def create_image():
    # Create a simple icon
    width = 64
    height = 64
    image = Image.new('RGB', (width, height), color='black')
    dc = ImageDraw.Draw(image)
    dc.rectangle([16, 16, 48, 48], fill='white')
    return image

def quit_action(icon, item):
    icon.stop()

def main():
    # Check for existing instance using mutex
    mutex_name = "Global\\Debridarr_SingleInstance"
    kernel32 = ctypes.windll.kernel32
    mutex = kernel32.CreateMutexW(None, True, mutex_name)
    
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        print("Debridarr is already running")
        sys.exit(0)
    
    try:
        # Start the main app in a separate thread
        app_thread = threading.Thread(target=app_main, daemon=True)
        app_thread.start()
        
        # Create system tray icon
        icon = pystray.Icon(
            "Debridarr",
            create_image(),
            menu=pystray.Menu(
                pystray.MenuItem("Quit", quit_action)
            )
        )
        
        icon.run()
    finally:
        if mutex:
            kernel32.CloseHandle(mutex)

if __name__ == "__main__":
    main()