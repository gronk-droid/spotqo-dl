"""
Configuration management for spotqo-dl.
"""

import os
import configparser
from pathlib import Path
from typing import Optional


class Config:
    """Configuration manager for spotqo-dl."""
    
    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path or self._get_default_config_path()
        self.spotify_client_id: Optional[str] = None
        self.spotify_client_secret: Optional[str] = None
        self.qobuz_email: Optional[str] = None
        self.qobuz_password: Optional[str] = None
        
    def _get_default_config_path(self) -> str:
        """Get the default configuration file path."""
        if os.name == "nt":
            config_dir = os.environ.get("APPDATA", "")
        else:
            config_dir = os.path.join(os.environ.get("HOME", ""), ".config")
        
        config_path = os.path.join(config_dir, "spotqo-dl", "config.ini")
        return config_path
    
    def load(self) -> None:
        """Load configuration from file and environment variables."""
        # Load from environment variables first
        self.spotify_client_id = os.getenv("SPOTIFY_CLIENT_ID")
        self.spotify_client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
        self.qobuz_email = os.getenv("QOBUZ_EMAIL")
        self.qobuz_password = os.getenv("QOBUZ_PASSWORD")
        
        # Load from config file if it exists
        if os.path.exists(self.config_path):
            config = configparser.ConfigParser()
            config.read(self.config_path)
            
            if "spotify" in config:
                self.spotify_client_id = self.spotify_client_id or config["spotify"].get("client_id")
                self.spotify_client_secret = self.spotify_client_secret or config["spotify"].get("client_secret")
            
            if "qobuz" in config:
                self.qobuz_email = self.qobuz_email or config["qobuz"].get("email")
                self.qobuz_password = self.qobuz_password or config["qobuz"].get("password")
    
    def is_valid(self) -> bool:
        """Check if all required configuration is present."""
        return all([
            self.spotify_client_id,
            self.spotify_client_secret,
            self.qobuz_email,
            self.qobuz_password
        ])
    
    def save(self) -> None:
        """Save configuration to file."""
        config_dir = os.path.dirname(self.config_path)
        os.makedirs(config_dir, exist_ok=True)
        
        config = configparser.ConfigParser()
        
        if self.spotify_client_id and self.spotify_client_secret:
            config["spotify"] = {
                "client_id": self.spotify_client_id,
                "client_secret": self.spotify_client_secret
            }
        
        if self.qobuz_email and self.qobuz_password:
            config["qobuz"] = {
                "email": self.qobuz_email,
                "password": self.qobuz_password
            }
        
        with open(self.config_path, 'w') as f:
            config.write(f)
