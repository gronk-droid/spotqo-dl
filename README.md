# spotqo-dl

A command line tool that downloads Spotify tracks using Qobuz as the source.

## Features

- Download Spotify tracks, albums, and playlists
- Uses Qobuz for high-quality audio downloads
- Command line interface with rich output
- Automatic metadata extraction and tagging
- Support for multiple audio qualities (MP3, Lossless, Hi-Res)

## Requirements
- A Spotify developer project (free and easy to set up. see [here](https://developer.spotify.com/documentation/web-api/tutorials/getting-started).)
- A Qobuz account
- python>=3.10
- [uv](https://github.com/astral-sh/uv)

## Installation

1. Clone the repo
2. Run `uv sync`
3. Run install script with `./install.sh`
   - Note: This will install it to the venv that uv makes, so it will only be available when that is activated.

## Setup

Before using spotqo-dl, you need to set up your credentials:

### 1. Spotify API Credentials

1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Create a new app
3. Note down your Client ID and Client Secret

### 2. Qobuz Account

You need a Qobuz account with a subscription to download tracks.

### 3. Configure Credentials

You can set up credentials in three ways:

#### Option 1: Environment Variables
```bash
export SPOTIFY_CLIENT_ID="your_spotify_client_id"
export SPOTIFY_CLIENT_SECRET="your_spotify_client_secret"
export QOBUZ_EMAIL="your_qobuz_email"
export QOBUZ_PASSWORD="your_qobuz_password"
```

#### Option 2: Command Line Arguments
```bash
spotqo-dl --spotify-client-id "your_id" --spotify-client-secret "your_secret" \
           --qobuz-email "your_email" --qobuz-password "your_password" \
           "https://open.spotify.com/track/..."
```

#### Option 3: Config File
Create a config file at `~/.config/spotqo-dl/config.ini`:
```ini
[spotify]
client_id = your_spotify_client_id
client_secret = your_spotify_client_secret

[qobuz]
email = your_qobuz_email
password = your_qobuz_password
```

## Usage

### Basic Usage

```bash
# Download a single track
spotqo-dl "https://open.spotify.com/track/4iV5W9uYEdYUVa79Axb7Rh"

# Download an album
spotqo-dl "https://open.spotify.com/album/1DFixLWuPkv3KT3TnVXm4o"

# Download a playlist
spotqo-dl "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
```

### Advanced Options

```bash
# Specify output directory
spotqo-dl -o ./my_music "https://open.spotify.com/track/..."

# Choose quality (5=MP3, 6=Lossless, 7=Hi-res <96kHz, 27=Hi-res >96kHz)
spotqo-dl -q 6 "https://open.spotify.com/track/..."

# Enable verbose logging
spotqo-dl -v "https://open.spotify.com/track/..."
```

## Requirements

- Python 3.10+
- Spotify API credentials
- Qobuz account with subscription
- Internet connection

## How It Works

1. **Parse Spotify URL**: Extracts track, album, or playlist information from Spotify URLs
2. **Search Qobuz**: Uses the extracted metadata to search for matching tracks on Qobuz
3. **Download**: Downloads the high-quality audio files from Qobuz
4. **Organize**: Saves files with proper metadata and folder structure

## Troubleshooting

### Common Issues

1. **"Missing required configuration"**: Make sure you've set up your Spotify and Qobuz credentials
2. **"Could not find track on Qobuz"**: The track might not be available on Qobuz, or the search didn't find a good match
3. **Authentication errors**: Check your credentials and make sure your Qobuz account is active

### Getting Help

Run with `-v` flag for verbose logging to see detailed information about the download process.

## License

MIT License