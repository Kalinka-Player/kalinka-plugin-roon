"""Normalize the selected output's Roon volume to Kalinka's slider."""

import math

from kalinka_plugin_sdk.datamodel import DeviceVolume


def bounds(volume):
    if not volume or volume.get("type") not in ("number", "db"):
        return None
    values = [volume.get("min"), volume.get("max"), volume.get("value")]
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
        return None
    low, high, _ = values
    for key in ("hard_limit_max", "soft_limit"):
        if isinstance(volume.get(key), (int, float)):
            high = min(high, volume[key])
    if isinstance(volume.get("hard_limit_min"), (int, float)):
        low = max(low, volume["hard_limit_min"])
    return (low, high) if low < high else None


def state(volume):
    limits = bounds(volume)
    if limits is None:
        return DeviceVolume(supported=False)
    low, high = limits
    percent = round(100 * (volume["value"] - low) / (high - low))
    return DeviceVolume(
        supported=True,
        max_volume=100,
        current_volume=0 if volume.get("is_muted") else max(0, min(100, percent)),
    )


def value(volume, percent):
    limits = bounds(volume)
    if limits is None:
        raise NotImplementedError("Roon output has fixed or unavailable volume")
    if not 0 <= percent <= 100:
        raise ValueError("volume must be between 0 and 100")
    low, high = limits
    target = low + (high - low) * percent / 100
    step = volume.get("step")
    if isinstance(step, (int, float)) and math.isfinite(step) and step > 0:
        target = volume["min"] + round((target - volume["min"]) / step) * step
    return max(low, min(high, target))
