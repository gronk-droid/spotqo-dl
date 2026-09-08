"""
Loudness measurement and ReplayGain 2.0 tagging for spotqo-dl.

Different sources (Tidal, Qobuz, Soulseek rips, CDs, Bandcamp, ...) frequently
ship different masters of the same recording, so their integrated loudness can
differ by several dB.  This module measures each file's loudness with ffmpeg's
EBU R128 (``ebur128``) scanner and writes ReplayGain 2.0 gain/peak tags with
mutagen.  The audio samples are never modified: a ReplayGain-aware player reads
the tags and applies the gain at playback time, so the process is completely
reversible (delete the tags and the file is byte-for-byte the original).

For players that ignore ReplayGain tags, ``export_with_gain`` bakes the gain
into a fresh lossless copy (with dither) rather than touching the original.

Only two external tools are required, both already needed by the project:
``ffmpeg`` (runtime requirement for tiddl) and ``mutagen`` (a declared
dependency).
"""

from __future__ import annotations

import logging
import math
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# ReplayGain 2.0 reference level.  -18 LUFS is the de-facto standard used by
# loudgain/rsgain and honored by foobar2000, mpd, mpv, Rockbox, etc.
DEFAULT_TARGET_LUFS = -18.0

# Ceiling for the anti-clipping clamp, in dBTP.  Leaving 1 dB of headroom below
# full scale avoids inter-sample overshoots on reconstruction.
CLIP_CEILING_DBTP = -1.0

# Files whose measured integrated loudness is below this are treated as silence
# and skipped (ffmpeg reports ``-inf`` / a very low number for pure silence).
SILENCE_FLOOR_LUFS = -70.0

SUPPORTED_EXTENSIONS = [".flac", ".mp3", ".m4a", ".wav"]
LOSSLESS_EXTENSIONS = [".flac", ".wav"]

# Marker written by export_with_gain so a baked copy is never gained twice.
GAIN_APPLIED_KEY = "SPOTQO_GAIN_APPLIED"


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass
class LoudnessResult:
    """Result of an EBU R128 measurement for a single file or a set of files."""

    path: Optional[Path]
    integrated_lufs: float
    true_peak_dbfs: float
    duration: float = 0.0

    @property
    def is_silent(self) -> bool:
        return (
            math.isinf(self.integrated_lufs)
            or math.isnan(self.integrated_lufs)
            or self.integrated_lufs <= SILENCE_FLOOR_LUFS
        )


@dataclass
class GainValues:
    """ReplayGain gain (dB) and linear peak for a track and its album."""

    track_gain_db: float
    track_peak: float
    album_gain_db: float
    album_peak: float
    reference_lufs: float


# ---------------------------------------------------------------------------
# ffmpeg helpers
# ---------------------------------------------------------------------------


def _require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "ffmpeg not found on PATH. Install ffmpeg to use loudness features."
        )


def _parse_ebur128_summary(stderr: str) -> LoudnessResult:
    """
    Parse the ``Summary:`` block emitted by ffmpeg's ebur128 filter.

    Pure function (no I/O) so it can be unit-tested against captured output.

    The block looks like::

        [Parsed_ebur128_0 @ ...] Summary:

          Integrated loudness:
            I:         -8.9 LUFS
            Threshold: -19.0 LUFS

          ...

          True peak:
            Peak:      0.4 dBFS

    A silent input yields ``I: -inf LUFS`` (or a value near -70); callers should
    check :pyattr:`LoudnessResult.is_silent`.
    """

    def _grab(pattern: str) -> Optional[float]:
        match = re.search(pattern, stderr)
        if not match:
            return None
        token = match.group(1).strip().lower()
        if token in ("-inf", "inf", "-nan", "nan"):
            return float("-inf") if token.startswith("-") else float("inf")
        try:
            return float(token)
        except ValueError:
            return None

    integrated = _grab(r"\bI:\s*(-?[\d.]+|-?inf|-?nan)\s*LUFS")
    peak = _grab(r"Peak:\s*(-?[\d.]+|-?inf|-?nan)\s*dBFS")

    if integrated is None:
        raise ValueError(
            "Could not find integrated loudness in ffmpeg ebur128 output"
        )

    # A missing true-peak line (peak mode disabled) is not fatal: assume 0 dBFS
    # which yields a linear peak of 1.0 and disables any clip clamp benefit.
    if peak is None:
        peak = 0.0

    return LoudnessResult(path=None, integrated_lufs=integrated, true_peak_dbfs=peak)


def measure(path: Path) -> LoudnessResult:
    """
    Measure the integrated loudness and true peak of a single audio file.

    Runs one ffmpeg pass with the ebur128 scanner.  Raises RuntimeError if
    ffmpeg fails and ValueError if the output cannot be parsed.
    """
    _require_ffmpeg()
    path = Path(path)

    cmd = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-nostats",
        "-i",
        str(path),
        "-map",
        "a:0",
        "-filter:a",
        "ebur128=peak=true:framelog=quiet",
        "-f",
        "null",
        "-",
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed to measure {path.name}: {proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else 'unknown error'}"
        )

    result = _parse_ebur128_summary(proc.stderr)
    result.path = path
    result.duration = _probe_duration(path)
    return result


def measure_album(paths: List[Path]) -> LoudnessResult:
    """
    Measure the gated album loudness across *paths* in a single ffmpeg pass.

    The files are concatenated in the filtergraph (after normalizing sample
    format/rate/layout so mixed-rate albums concatenate cleanly) and fed through
    one ebur128 scanner.  This is the correct EBU R128 album measurement, not an
    average of per-track values.  The album peak is the max of the individual
    true peaks.
    """
    _require_ffmpeg()
    paths = [Path(p) for p in paths]
    if not paths:
        raise ValueError("measure_album requires at least one path")
    if len(paths) == 1:
        return measure(paths[0])

    cmd: List[str] = ["ffmpeg", "-nostdin", "-hide_banner", "-nostats"]
    for p in paths:
        cmd += ["-i", str(p)]

    # Normalize every input then concatenate into a single audio stream.
    fmt = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    filter_parts = []
    labels = []
    for i in range(len(paths)):
        filter_parts.append(f"[{i}:a]{fmt}[a{i}]")
        labels.append(f"[a{i}]")
    concat = (
        "".join(labels)
        + f"concat=n={len(paths)}:v=0:a=1,ebur128=peak=true:framelog=quiet[out]"
    )
    filtergraph = ";".join(filter_parts + [concat])

    cmd += [
        "-filter_complex",
        filtergraph,
        "-map",
        "[out]",
        "-f",
        "null",
        "-",
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        # Fall back to per-track energy averaging if the concat pass fails
        # (e.g. an exotic codec ffmpeg cannot resample in this graph).
        logger.warning(
            "Album loudness pass failed; falling back to per-track energy mean"
        )
        return _album_from_tracks([measure(p) for p in paths])

    result = _parse_ebur128_summary(proc.stderr)
    result.path = None
    return result


def _album_from_tracks(results: List[LoudnessResult]) -> LoudnessResult:
    """Energy-weighted mean loudness fallback when a concat pass is impossible."""
    usable = [r for r in results if not r.is_silent]
    if not usable:
        return LoudnessResult(path=None, integrated_lufs=float("-inf"), true_peak_dbfs=0.0)
    total_energy = sum(10 ** (r.integrated_lufs / 10) for r in usable)
    mean_lufs = 10 * math.log10(total_energy / len(usable))
    peak = max(r.true_peak_dbfs for r in usable)
    return LoudnessResult(path=None, integrated_lufs=mean_lufs, true_peak_dbfs=peak)


def _probe_duration(path: Path) -> float:
    """Return duration in seconds via ffprobe, or 0.0 if unavailable."""
    if shutil.which("ffprobe") is None:
        return 0.0
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
        )
        return float(proc.stdout.strip())
    except (ValueError, subprocess.SubprocessError):
        return 0.0


# ---------------------------------------------------------------------------
# Gain math
# ---------------------------------------------------------------------------


def _dbfs_to_linear(dbfs: float) -> float:
    return 10 ** (dbfs / 20)


def compute_gain(
    track: LoudnessResult,
    album: Optional[LoudnessResult],
    target_lufs: float = DEFAULT_TARGET_LUFS,
    prevent_clipping: bool = True,
) -> GainValues:
    """
    Turn measured loudness into ReplayGain gain (dB) and linear peak values.

    ``track_gain = target - measured``.  When *prevent_clipping* is set, the
    gain is reduced (never increased) so the post-gain true peak stays at or
    below :pydata:`CLIP_CEILING_DBTP`.
    """
    track_gain = target_lufs - track.integrated_lufs
    track_peak = _dbfs_to_linear(track.true_peak_dbfs)

    if album is not None and not album.is_silent:
        album_gain = target_lufs - album.integrated_lufs
        album_peak = _dbfs_to_linear(album.true_peak_dbfs)
    else:
        album_gain = track_gain
        album_peak = track_peak

    if prevent_clipping:
        track_gain = _clamp_gain(track_gain, track.true_peak_dbfs)
        album_gain = _clamp_gain(album_gain, album.true_peak_dbfs if album else track.true_peak_dbfs)

    return GainValues(
        track_gain_db=round(track_gain, 2),
        track_peak=track_peak,
        album_gain_db=round(album_gain, 2),
        album_peak=album_peak,
        reference_lufs=target_lufs,
    )


def _clamp_gain(gain_db: float, true_peak_dbfs: float) -> float:
    """Reduce *gain_db* so the resulting true peak does not exceed the ceiling."""
    resulting_peak = true_peak_dbfs + gain_db
    if resulting_peak > CLIP_CEILING_DBTP:
        return gain_db - (resulting_peak - CLIP_CEILING_DBTP)
    return gain_db


# ---------------------------------------------------------------------------
# Tag writing (mutagen)
# ---------------------------------------------------------------------------


def _fmt_gain(gain_db: float) -> str:
    return f"{gain_db:.2f} dB"


def _fmt_peak(peak: float) -> str:
    return f"{peak:.6f}"


def _fmt_ref(reference_lufs: float) -> str:
    # ReplayGain reference is conventionally expressed in dB SPL-ish LUFS terms.
    return f"{reference_lufs:.2f} LUFS"


def has_tags(path: Path) -> bool:
    """Return True if the file already carries ReplayGain track-gain tags."""
    path = Path(path)
    ext = path.suffix.lower()
    try:
        if ext == ".flac":
            from mutagen.flac import FLAC

            audio = FLAC(str(path))
            return bool(audio.get("replaygain_track_gain"))
        if ext == ".mp3":
            from mutagen.id3 import ID3, ID3NoHeaderError

            try:
                tags = ID3(str(path))
            except ID3NoHeaderError:
                return False
            for frame in tags.getall("TXXX"):
                if frame.desc.lower() == "replaygain_track_gain":
                    return True
            return bool(tags.getall("RVA2"))
        if ext == ".m4a":
            from mutagen.mp4 import MP4

            audio = MP4(str(path))
            return "----:com.apple.iTunes:replaygain_track_gain" in audio
        if ext == ".wav":
            from mutagen.wave import WAVE

            audio = WAVE(str(path))
            if audio.tags is None:
                return False
            for frame in audio.tags.getall("TXXX"):
                if frame.desc.lower() == "replaygain_track_gain":
                    return True
            return False
    except Exception as exc:  # noqa: BLE001 - detection must never raise
        logger.debug(f"has_tags check failed for {path.name}: {exc}")
    return False


def write_tags(path: Path, gains: GainValues) -> None:
    """Write ReplayGain 2.0 tags to *path*, dispatching on file format."""
    path = Path(path)
    ext = path.suffix.lower()

    if ext == ".flac":
        _write_vorbis_tags(path, gains)
    elif ext == ".mp3":
        _write_id3_tags(path, gains)
    elif ext == ".m4a":
        _write_mp4_tags(path, gains)
    elif ext == ".wav":
        _write_wav_tags(path, gains)
    else:
        raise ValueError(f"Unsupported format for ReplayGain tags: {ext}")


def _write_vorbis_tags(path: Path, gains: GainValues) -> None:
    from mutagen.flac import FLAC

    audio = FLAC(str(path))
    audio["REPLAYGAIN_TRACK_GAIN"] = _fmt_gain(gains.track_gain_db)
    audio["REPLAYGAIN_TRACK_PEAK"] = _fmt_peak(gains.track_peak)
    audio["REPLAYGAIN_ALBUM_GAIN"] = _fmt_gain(gains.album_gain_db)
    audio["REPLAYGAIN_ALBUM_PEAK"] = _fmt_peak(gains.album_peak)
    audio["REPLAYGAIN_REFERENCE_LOUDNESS"] = _fmt_ref(gains.reference_lufs)
    audio.save()


def _write_id3_tags(path: Path, gains: GainValues) -> None:
    from mutagen.id3 import ID3, ID3NoHeaderError, TXXX, RVA2

    try:
        tags = ID3(str(path))
    except ID3NoHeaderError:
        tags = ID3()

    def set_txxx(desc: str, value: str) -> None:
        tags.delall(f"TXXX:{desc}")
        tags.add(TXXX(encoding=3, desc=desc, text=[value]))

    # foobar2000 / picard style TXXX frames.
    set_txxx("replaygain_track_gain", _fmt_gain(gains.track_gain_db))
    set_txxx("replaygain_track_peak", _fmt_peak(gains.track_peak))
    set_txxx("replaygain_album_gain", _fmt_gain(gains.album_gain_db))
    set_txxx("replaygain_album_peak", _fmt_peak(gains.album_peak))
    set_txxx("replaygain_reference_loudness", _fmt_ref(gains.reference_lufs))

    # RVA2 frames for players that follow the ID3v2.4 relative-volume convention.
    tags.delall("RVA2")
    tags.add(
        RVA2(desc="track", channel=1, gain=gains.track_gain_db, peak=gains.track_peak)
    )
    tags.add(
        RVA2(desc="album", channel=1, gain=gains.album_gain_db, peak=gains.album_peak)
    )

    tags.save(str(path))


def _write_mp4_tags(path: Path, gains: GainValues) -> None:
    from mutagen.mp4 import MP4, MP4FreeForm

    audio = MP4(str(path))

    def freeform(name: str, value: str) -> None:
        key = f"----:com.apple.iTunes:{name}"
        audio[key] = [MP4FreeForm(value.encode("utf-8"))]

    freeform("replaygain_track_gain", _fmt_gain(gains.track_gain_db))
    freeform("replaygain_track_peak", _fmt_peak(gains.track_peak))
    freeform("replaygain_album_gain", _fmt_gain(gains.album_gain_db))
    freeform("replaygain_album_peak", _fmt_peak(gains.album_peak))
    freeform("replaygain_reference_loudness", _fmt_ref(gains.reference_lufs))
    audio.save()


def _write_wav_tags(path: Path, gains: GainValues) -> None:
    from mutagen.wave import WAVE
    from mutagen.id3 import TXXX

    audio = WAVE(str(path))
    if audio.tags is None:
        audio.add_tags()

    def set_txxx(desc: str, value: str) -> None:
        audio.tags.delall(f"TXXX:{desc}")
        audio.tags.add(TXXX(encoding=3, desc=desc, text=[value]))

    set_txxx("replaygain_track_gain", _fmt_gain(gains.track_gain_db))
    set_txxx("replaygain_track_peak", _fmt_peak(gains.track_peak))
    set_txxx("replaygain_album_gain", _fmt_gain(gains.album_gain_db))
    set_txxx("replaygain_album_peak", _fmt_peak(gains.album_peak))
    set_txxx("replaygain_reference_loudness", _fmt_ref(gains.reference_lufs))
    audio.save()


# ---------------------------------------------------------------------------
# Batch processor
# ---------------------------------------------------------------------------


class LoudnessProcessor:
    """
    Measures a set of audio files and writes ReplayGain tags.

    Files are grouped by their parent directory (which, under spotqo-dl's
    ``folder_format``, is already the album folder) so a single gated album
    measurement can be taken per group.  Per-track measurements run in a
    thread pool because each is an independent ffmpeg subprocess.
    """

    def __init__(
        self,
        target: float = DEFAULT_TARGET_LUFS,
        album_mode: bool = True,
        prevent_clipping: bool = True,
        force: bool = False,
        jobs: int = 4,
        dry_run: bool = False,
        console=None,
    ) -> None:
        self.target = target
        self.album_mode = album_mode
        self.prevent_clipping = prevent_clipping
        self.force = force
        self.jobs = max(1, jobs)
        self.dry_run = dry_run
        self.console = console

    # -- discovery ------------------------------------------------------

    def scan_directory(self, directory: str) -> List[Path]:
        """Recursively find supported audio files under *directory*."""
        directory_path = Path(directory)
        files: List[Path] = []
        for ext in SUPPORTED_EXTENSIONS:
            files.extend(directory_path.rglob(f"*{ext}"))
        return sorted(files)

    # -- driving --------------------------------------------------------

    def process_directory(self, directory: str) -> int:
        files = self.scan_directory(directory)
        if not files:
            self._echo(f"[yellow]No audio files found in {directory}[/yellow]")
            return 0
        return self.process_files(files)

    def process_files(self, paths: List[Path]) -> int:
        """
        Measure and tag *paths*.  Returns the number of files tagged.
        """
        paths = [Path(p) for p in paths]
        if not self.force:
            skipped = [p for p in paths if has_tags(p)]
            if skipped:
                self._echo(
                    f"[dim]Skipping {len(skipped)} already-tagged file(s) "
                    f"(use --force to re-tag)[/dim]"
                )
            paths = [p for p in paths if p not in set(skipped)]

        if not paths:
            return 0

        groups: Dict[Path, List[Path]] = {}
        for p in paths:
            groups.setdefault(p.parent, []).append(p)

        tagged = 0
        for parent, group in groups.items():
            tagged += self._process_group(parent, group)
        return tagged

    def _process_group(self, parent: Path, group: List[Path]) -> int:
        self._echo(f"[cyan]Analyzing {len(group)} file(s) in {parent}[/cyan]")

        measurements: Dict[Path, LoudnessResult] = {}
        with ThreadPoolExecutor(max_workers=self.jobs) as pool:
            future_map = {pool.submit(self._safe_measure, p): p for p in group}
            for future in future_map:
                p = future_map[future]
                result = future.result()
                if result is not None:
                    measurements[p] = result

        usable = {p: r for p, r in measurements.items() if not r.is_silent}
        for p, r in measurements.items():
            if r.is_silent:
                self._echo(f"[yellow]Skipping silent file: {p.name}[/yellow]")

        if not usable:
            return 0

        album_result: Optional[LoudnessResult] = None
        if self.album_mode and len(usable) > 1:
            try:
                album_result = measure_album(list(usable.keys()))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Album measurement failed for {parent}: {exc}")

        tagged = 0
        for p, result in usable.items():
            gains = compute_gain(
                result,
                album_result,
                target_lufs=self.target,
                prevent_clipping=self.prevent_clipping,
            )
            self._echo(
                f"  {p.name}: {result.integrated_lufs:.1f} LUFS -> "
                f"track gain {gains.track_gain_db:+.2f} dB"
                + (f", album gain {gains.album_gain_db:+.2f} dB" if album_result else "")
            )
            if not self.dry_run:
                try:
                    write_tags(p, gains)
                    tagged += 1
                except Exception as exc:  # noqa: BLE001
                    logger.error(f"Failed to tag {p.name}: {exc}")
                    self._echo(f"[red]Failed to tag {p.name}: {exc}[/red]")
        return tagged

    def _safe_measure(self, path: Path) -> Optional[LoudnessResult]:
        try:
            return measure(path)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Measurement failed for {path.name}: {exc}")
            self._echo(f"[red]Measurement failed for {path.name}: {exc}[/red]")
            return None

    def _echo(self, message: str) -> None:
        if self.console is not None:
            self.console.print(message)
        else:
            logger.info(re.sub(r"\[/?[a-z ]+\]", "", message))


# ---------------------------------------------------------------------------
# Destructive (but copy-based) gain baking
# ---------------------------------------------------------------------------


def _probe_bit_depth(path: Path) -> Optional[int]:
    """Return the source PCM bit depth via ffprobe, or None if unknown."""
    if shutil.which("ffprobe") is None:
        return None
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=bits_per_raw_sample,sample_fmt",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
        )
        lines = [l.strip() for l in proc.stdout.splitlines() if l.strip()]
        for line in lines:
            if line.isdigit():
                return int(line)
    except (ValueError, subprocess.SubprocessError):
        return None
    return None


def export_with_gain(
    src: Path,
    dest: Path,
    target_lufs: float = DEFAULT_TARGET_LUFS,
    prevent_clipping: bool = True,
    measurement: Optional[LoudnessResult] = None,
    gains: Optional[GainValues] = None,
) -> Path:
    """
    Write a gain-applied *lossless* copy of *src* to *dest*.

    The gain is baked into the samples with ffmpeg's ``volume`` filter and TPDF
    dither, so it plays correctly on hardware that ignores ReplayGain tags.  The
    original is never modified.  Tags and cover art are copied from the source
    and the ReplayGain tags are stripped from the copy (a ``SPOTQO_GAIN_APPLIED``
    marker is written so a later pass will not double-apply gain).

    Only lossless sources are accepted; MP3/M4A raise ValueError pointing at the
    tag-based ``normalize`` command.
    """
    _require_ffmpeg()
    src = Path(src)
    dest = Path(dest)
    ext = src.suffix.lower()

    if ext not in LOSSLESS_EXTENSIONS:
        raise ValueError(
            f"export_with_gain only supports lossless inputs ({', '.join(LOSSLESS_EXTENSIONS)}); "
            f"for {ext} files use the tag-based 'normalize' command instead."
        )

    if measurement is None:
        measurement = measure(src)
    if measurement.is_silent:
        raise ValueError(f"Refusing to export silent file: {src.name}")
    if gains is None:
        gains = compute_gain(
            measurement, None, target_lufs=target_lufs, prevent_clipping=prevent_clipping
        )

    bit_depth = _probe_bit_depth(src)
    # Map to an ffmpeg sample format; default to 16-bit if unknown.
    if bit_depth and bit_depth >= 24:
        out_fmt = "s32"
        flac_bits = 24
    else:
        out_fmt = "s16"
        flac_bits = 16

    dest.parent.mkdir(parents=True, exist_ok=True)

    filtergraph = (
        f"volume={gains.track_gain_db}dB,"
        f"aresample=out_sample_fmt={out_fmt}:dither_method=triangular_hp"
    )

    cmd = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-nostats",
        "-y",
        "-i",
        str(src),
        "-map",
        "a:0",
        # Start from clean metadata; tags and art are copied with mutagen below
        # so stale ReplayGain tags cannot survive and cause double-application.
        "-map_metadata",
        "-1",
        "-af",
        filtergraph,
        "-c:a",
        "flac",
        "-compression_level",
        "8",
    ]
    if flac_bits == 24:
        cmd += ["-sample_fmt", "s32"]
    else:
        cmd += ["-sample_fmt", "s16"]

    # Always write a .flac copy (lossless, tag-friendly, universally supported).
    dest = dest.with_suffix(".flac")
    cmd.append(str(dest))

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed to export {src.name}: "
            f"{proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else 'unknown error'}"
        )

    _copy_tags_and_art(src, dest)
    return dest


def _copy_tags_and_art(src: Path, dest: Path) -> None:
    """
    Copy textual tags and embedded cover art from *src* to *dest* (both FLAC or
    a FLAC destination), strip any ReplayGain tags, and mark the copy as gained.
    """
    from mutagen.flac import FLAC, Picture

    try:
        dest_audio = FLAC(str(dest))
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Could not open exported file to copy tags: {exc}")
        return

    src_ext = src.suffix.lower()
    copied_tags: Dict[str, list] = {}
    pictures: list = []

    try:
        if src_ext == ".flac":
            src_audio = FLAC(str(src))
            copied_tags = {k: v for k, v in src_audio.items()}
            pictures = list(src_audio.pictures)
        elif src_ext == ".wav":
            from mutagen.wave import WAVE

            src_audio = WAVE(str(src))
            if src_audio.tags is not None:
                for frame in src_audio.tags.getall("TXXX"):
                    copied_tags[frame.desc.upper()] = list(frame.text)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Could not read source tags from {src.name}: {exc}")

    # Defensively drop any ReplayGain tags that ffmpeg may have carried over.
    for existing in list(dest_audio.keys()):
        if existing.upper().startswith("REPLAYGAIN"):
            del dest_audio[existing]

    for key, value in copied_tags.items():
        if key.upper().startswith("REPLAYGAIN"):
            continue
        dest_audio[key] = value

    for pic in pictures:
        new_pic = Picture()
        new_pic.type = pic.type
        new_pic.mime = pic.mime
        new_pic.desc = pic.desc
        new_pic.data = pic.data
        dest_audio.add_picture(new_pic)

    dest_audio[GAIN_APPLIED_KEY] = "1"
    dest_audio.save()
