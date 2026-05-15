# spotqo-dl

A command line tool that downloads Spotify tracks using Qobuz as the primary source, with automatic Tidal fallback. Direct Qobuz and Tidal URLs are also supported.

## Features

- Download Spotify tracks, albums, and playlists
- Uses Qobuz for high-quality audio downloads
- Automatic Tidal fallback when Qobuz cannot find a track
- Direct Qobuz and Tidal URL downloads
- Command line interface with rich output
- Automatic metadata extraction and tagging
- Support for multiple audio qualities (MP3, Lossless, Hi-Res, Master)
- Restructure existing audio files using metadata

## Requirements
- A Spotify developer project (free and easy to set up. see [here](https://developer.spotify.com/documentation/web-api/tutorials/getting-started).)
- A Qobuz account
- python>=3.13
- [uv](https://github.com/astral-sh/uv)
- [ffmpeg](https://ffmpeg.org/download.html) (required by tiddl for Tidal downloads)

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

### 3. Tidal Account (optional — for fallback and direct Tidal downloads)

Tidal authentication is handled entirely by [tiddl](https://github.com/oskvr37/tiddl). Run once:

```bash
tiddl auth login
```

Follow the on-screen instructions to log in via your browser. spotqo-dl reads the resulting session token from `~/.tiddl/config.toml` automatically — no additional config is required. If Tidal is not authenticated, spotqo-dl will still work for Qobuz and Spotify downloads; the fallback is simply skipped.

### 4. Configure Credentials

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
# Download a single track (Spotify)
spotqo-dl download "https://open.spotify.com/track/4iV5W9uYEdYUVa79Axb7Rh"

# Download an album (Spotify)
spotqo-dl download "https://open.spotify.com/album/1DFixLWuPkv3KT3TnVXm4o"

# Download a playlist (Spotify)
spotqo-dl download "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"

# Download directly from a Tidal URL
spotqo-dl download "https://listen.tidal.com/album/103805723"
spotqo-dl download "https://tidal.com/browse/track/103805726"

# Download directly from a Qobuz URL
spotqo-dl download "https://www.qobuz.com/us-en/album/..."
```

### Advanced Options

```bash
# Specify output directory
spotqo-dl download -o ./my_music "https://open.spotify.com/track/..."

# Choose Qobuz quality (5=MP3, 6=Lossless, 7=Hi-res <96kHz, 27=Hi-res >96kHz)
spotqo-dl download -q 6 "https://open.spotify.com/track/..."

# Choose Tidal quality for fallback or direct Tidal downloads
# (LOW=96kbps, HIGH=320kbps, LOSSLESS=16-bit FLAC, HI_RES_LOSSLESS=up to 24-bit FLAC)
spotqo-dl download --tidal-quality HI_RES_LOSSLESS "https://listen.tidal.com/album/..."

# Disable automatic Tidal fallback for Spotify URLs (Qobuz-only)
spotqo-dl download --no-tidal-fallback "https://open.spotify.com/album/..."

# Enable verbose logging
spotqo-dl download -v "https://open.spotify.com/track/..."
```

### Tidal Fallback Behavior

When downloading Spotify URLs, spotqo-dl first searches Qobuz for each album. If Qobuz cannot find a track, spotqo-dl automatically searches Tidal and downloads from there instead. Both sources use the same file-naming format, so your library stays consistently organized. A playlist `.m3u` file merges tracks from both sources in the correct order.

To disable Tidal fallback, pass `--no-tidal-fallback`. If Tidal is not authenticated, fallback is silently skipped.

### Restructuring Existing Files

The `restructure` command allows you to reorganize existing audio files (FLAC and MP3) based on their metadata:

```bash
# Restructure files in a directory
spotqo-dl restructure /path/to/music

# Preview changes without making them (dry run)
spotqo-dl restructure /path/to/music --dry-run

# Custom format templates
spotqo-dl restructure /path/to/music \
  --format "{track-number:02d} - {title}" \
  --folder-format "{artist} - {album} ({year})"
```

#### How it works:

1. **Scans recursively** for all `.flac` and `.mp3` files in the specified directory
2. **Extracts metadata** from each file using embedded tags (artist, album, year, track number, disc number, etc.)
3. **Displays a summary** of found files grouped by album
4. **Prompts for confirmation** - you can verify the metadata is correct or manually edit it
5. **Restructures files** according to the format templates
6. **Handles multi-disc albums** by creating `disc-1`, `disc-2`, etc. subdirectories when needed
7. **Cleans up** empty directories after restructuring

#### Format Variables:

Available variables for `--format` and `--folder-format`:
- `{title}` - Track title
- `{artist}` - Track artist
- `{album}` - Album name
- `{album-artist}` - Album artist
- `{year}` - Release year
- `{track-number}` or `{track-number:02d}` - Track number (with optional zero-padding)
- `{disc-number}` or `{disc-number:02d}` - Disc number (with optional zero-padding)

#### Example:

```bash
# Before:
/music/
  random-song-1.flac
  random-song-2.flac
  subfolder/
    another-song.mp3

# After running: spotqo-dl restructure /music
/music/
  artist-name-album-name-2024/
    01-track-title.flac
    02-another-track.flac
  another-artist-different-album-2023/
    disc-1/
      01-song-title.mp3
```