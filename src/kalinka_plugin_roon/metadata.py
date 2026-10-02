"""Map only data actually exposed by Roon or the configured ALSA output."""

import hashlib
import math
import re
from pathlib import Path

from kalinka_plugin_sdk.datamodel import (
    Album,
    Artist,
    AudioInfo,
    CoverImage,
    EntityId,
    EntityType,
    OutputInfo,
    PlaybackState,
    PlayerStateEnum,
    Track,
)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def number(value):
    return (
        max(0, value) if isinstance(value, (int, float)) and math.isfinite(value) else 0
    )


def entity(kind, value):
    return EntityId(source="roon", type=kind, id=digest(value))


def alsa_output(path):
    if not path:
        return None
    try:
        text = Path(path).read_text()
    except OSError:
        return None
    values = dict(re.findall(r"^(\w+):\s*(.*)$", text, re.MULTILINE))
    try:
        rate = int(values["rate"].split()[0])
        channels = int(values["channels"])
    except (KeyError, ValueError):
        return None
    # Container width is not source bit depth; leave bits_per_sample unknown.
    return OutputInfo(sample_rate=rate, channels=channels)


def playback_state(zone, artwork_url=None, output=None):
    now = zone.get("now_playing") or {}
    lines = now.get("three_line") or now.get("two_line") or now.get("one_line") or {}
    title, artist, album = (lines.get(f"line{i}") or "" for i in (1, 2, 3))
    duration = int(number(now.get("length")))
    performer = (
        Artist(id=entity(EntityType.ARTIST, artist), name=artist) if artist else None
    )
    cover = (
        CoverImage(small=artwork_url, thumbnail=artwork_url, large=artwork_url)
        if artwork_url
        else None
    )
    track = (
        Track(
            id=entity(EntityType.TRACK, f"{title}\n{artist}\n{album}\n{duration}"),
            title=title or "Roon",
            duration=duration,
            performer=performer,
            album=Album(
                id=entity(EntityType.ALBUM, f"{artist}\n{album}"),
                title=album,
                image=cover,
                artist=performer,
            ),
        )
        if now
        else None
    )
    state = {
        "playing": PlayerStateEnum.PLAYING,
        "loading": PlayerStateEnum.BUFFERING,
        "paused": PlayerStateEnum.PAUSED,
        "stopped": PlayerStateEnum.STOPPED,
    }.get(zone.get("state"), PlayerStateEnum.STOPPED)
    return PlaybackState(
        state=state,
        current_track=track,
        position=round(number(now.get("seek_position")) * 1000),
        message=f"Roon endpoint · {zone.get('display_name', 'Roon')} · RAAT",
        audio_info=AudioInfo(
            sample_rate=0,
            bits_per_sample=0,
            channels=0,
            duration_ms=duration * 1000,
            output=output,
        ),
    )
