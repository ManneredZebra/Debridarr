#!/usr/bin/env python3
import pystray
from PIL import Image, ImageDraw
import threading
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

if __name__ == "__main__":
    main()