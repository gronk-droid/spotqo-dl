#!/usr/bin/env python3
"""
Setup script for spotqo-dl.
This script helps you configure your credentials.
"""

import os
import sys
from pathlib import Path

def create_config_file():
    """Create a configuration file with user input."""
    print("=== spotqo-dl Setup ===")
    print("This script will help you set up your credentials.")
    print()
    
    # Get Spotify credentials
    print("1. Spotify API Credentials")
    print("   Get these from: https://developer.spotify.com/dashboard")
    print("   Create a new app and note down your Client ID and Client Secret")
    print()
    
    spotify_client_id = input("Enter your Spotify Client ID: ").strip()
    spotify_client_secret = input("Enter your Spotify Client Secret: ").strip()
    
    print()
    print("2. Qobuz Account")
    print("   You need a Qobuz account with an active subscription")
    print()
    
    qobuz_email = input("Enter your Qobuz email: ").strip()
    qobuz_password = input("Enter your Qobuz password: ").strip()
    
    # Create config directory
    if os.name == "nt":
        config_dir = os.environ.get("APPDATA", "")
    else:
        config_dir = os.path.join(os.environ.get("HOME", ""), ".config")
    
    config_path = os.path.join(config_dir, "spotqo-dl", "config.ini")
    config_dir_path = os.path.dirname(config_path)
    
    # Create directory if it doesn't exist
    os.makedirs(config_dir_path, exist_ok=True)
    
    # Write config file
    config_content = f"""[spotify]
client_id = {spotify_client_id}
client_secret = {spotify_client_secret}

[qobuz]
email = {qobuz_email}
password = {qobuz_password}
"""
    
    with open(config_path, 'w') as f:
        f.write(config_content)
    
    print()
    print(f"✓ Configuration saved to: {config_path}")
    print()
    print("You can now use spotqo-dl:")
    print("  uv run spotqo-dl 'https://open.spotify.com/track/...'")
    print()
    print("For more options:")
    print("  uv run spotqo-dl --help")

def main():
    """Main setup function."""
    try:
        create_config_file()
    except KeyboardInterrupt:
        print("\nSetup cancelled.")
        sys.exit(1)
    except Exception as e:
        print(f"Error during setup: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
