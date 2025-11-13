# Restructure Command Examples

The `restructure` command allows you to reorganize existing audio files based on their embedded metadata.

## Basic Usage

```bash
# Restructure all audio files in a directory
spotqo-dl restructure /path/to/music

# Preview changes without making them (dry run)
spotqo-dl restructure /path/to/music --dry-run

# Enable verbose logging
spotqo-dl restructure /path/to/music --verbose
```

## Custom Formatting

### Track Format

The `--format` option controls how individual track files are named:

```bash
# Simple format: "01 - Song Title.flac"
spotqo-dl restructure /path/to/music --format "{track-number:02d} - {title}"

# Include artist: "01 - Artist Name - Song Title.flac"
spotqo-dl restructure /path/to/music --format "{track-number:02d} - {artist} - {title}"

# No track number: "Song Title.flac"
spotqo-dl restructure /path/to/music --format "{title}"
```

### Folder Format

The `--folder-format` option controls how album folders are named:

```bash
# Default: "Artist Name - Album Name (2024)"
spotqo-dl restructure /path/to/music --folder-format "{artist} - {album} ({year})"

# Album artist: "Album Artist - Album Name (2024)"
spotqo-dl restructure /path/to/music --folder-format "{album-artist} - {album} ({year})"

# Simple: "Album Name"
spotqo-dl restructure /path/to/music --folder-format "{album}"

# With year prefix: "[2024] Artist - Album"
spotqo-dl restructure /path/to/music --folder-format "[{year}] {artist} - {album}"
```

## Available Format Variables

### Track Information
- `{title}` - Track title
- `{artist}` - Track artist
- `{track-number}` - Track number (e.g., "1")
- `{track-number:02d}` - Track number with zero-padding (e.g., "01")
- `{disc-number}` - Disc number (e.g., "1")
- `{disc-number:02d}` - Disc number with zero-padding (e.g., "01")

### Album Information
- `{album}` - Album name
- `{album-artist}` - Album artist
- `{album-base}` - Album name without version info
- `{album-version}` - Album version (e.g., "deluxe", "remastered")
- `{year}` - Release year

### Other
- `{duration}` - Track duration
- `{isrc}` - ISRC code
- `{spotify-id}` - Spotify track ID
- `{spotify-url}` - Spotify track URL

## How It Works

1. **Scanning**: Recursively finds all `.flac` and `.mp3` files in the directory
2. **Metadata Extraction**: Reads embedded metadata from each file:
   - For FLAC: Uses FLAC tags (TITLE, ARTIST, ALBUM, DATE, TRACKNUMBER, DISCNUMBER, etc.)
   - For MP3: Uses ID3 tags (TIT2, TPE1, TALB, TDRC, TRCK, TPOS, etc.)
3. **Summary Display**: Shows all found files grouped by album with their metadata
4. **User Confirmation**: Prompts you to verify the metadata is correct
5. **Manual Editing**: If metadata is incorrect, you can manually input correct values
6. **Restructuring**: Moves files to new locations based on format templates
7. **Multi-disc Handling**: Automatically creates `disc-1`, `disc-2`, etc. subdirectories for multi-disc albums
8. **Cleanup**: Removes empty directories after restructuring

## Multi-Disc Albums

For albums with multiple discs, the restructure command automatically creates disc subdirectories:

```
Before:
/music/
  track1.flac  (disc 1, track 1)
  track2.flac  (disc 1, track 2)
  track3.flac  (disc 2, track 1)
  track4.flac  (disc 2, track 2)

After:
/music/
  artist-name-album-name-2024/
    disc-1/
      01-song-title.flac
      02-another-song.flac
    disc-2/
      01-third-song.flac
      02-fourth-song.flac
```

## Interactive Metadata Editing

If the extracted metadata is incorrect, the tool will prompt you to edit it:

```
Found 12 audio files in 1 album(s)

┏━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━┳━━━━━━━━━━━━━━━━━━━━┓
┃ File                ┃ Track ┃ Disc ┃ Title              ┃
┡━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━╇━━━━━━━━━━━━━━━━━━━━┩
│ song1.flac          │ 1     │ 1    │ Song Title         │
│ song2.flac          │ 2     │ 1    │ Another Song       │
└─────────────────────┴───────┴──────┴────────────────────┘

Is the metadata correct? [Y/n]: n

Let's update the metadata manually.

Album 1/1
Artist [Current Artist]: Correct Artist Name
Album [Current Album]: Correct Album Name
Year [2023]: 2024

Proceed with restructuring? [Y/n]: y
```

## Example Scenarios

### Scenario 1: Clean up downloaded files

```bash
# You have a messy downloads folder with various audio files
cd ~/Downloads/music
spotqo-dl restructure . --dry-run

# Review the proposed changes, then run:
spotqo-dl restructure .
```

### Scenario 2: Standardize library format

```bash
# Restructure your entire music library with a consistent format
spotqo-dl restructure ~/Music \
  --format "{track-number:02d} - {title}" \
  --folder-format "{album-artist} - {album} ({year})"
```

### Scenario 3: Fix incorrectly organized files

```bash
# Files are in the wrong folders or have wrong names
spotqo-dl restructure /path/to/album --verbose

# The tool will extract metadata and reorganize properly
```

## Tips

1. **Always use --dry-run first** to preview changes before making them
2. **Backup your files** before restructuring, especially for large libraries
3. **Use verbose mode** (`-v`) to see detailed information about what's happening
4. **Check metadata** in your audio files before running (use tools like `metaflac` or `id3v2`)
5. **Test on a small directory** first to ensure the format templates work as expected

## Troubleshooting

### No metadata found
If the tool can't extract metadata, check that your files have proper tags:
```bash
# For FLAC files
metaflac --list file.flac

# For MP3 files
id3v2 -l file.mp3
```

### Files not moving
- Ensure you have write permissions in the directory
- Check that the files aren't in use by another program
- Use `--verbose` to see detailed error messages

### Incorrect metadata
- Use the interactive editing feature to correct metadata
- Or fix the tags in your files first using tools like:
  - `metaflac` for FLAC files
  - `id3v2` or `eyeD3` for MP3 files
  - GUI tools like MusicBrainz Picard, Kid3, or Mp3tag

