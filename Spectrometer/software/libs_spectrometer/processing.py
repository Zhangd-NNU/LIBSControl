from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from bisect import bisect_left
from statistics import median
from typing import Iterable


@dataclass(frozen=True)
class Spectrum:
    channel: int
    wavelengths: tuple[float, ...]
    intensities: tuple[float, ...]
    model: str = ""
    serial: str = ""

    def __post_init__(self) -> None:
        if len(self.wavelengths) != len(self.intensities):
            raise ValueError("波长和强度数组长度不一致")


@dataclass(frozen=True)
class Frame:
    captured_at: datetime
    raw: tuple[Spectrum, ...]
    displayed: tuple[Spectrum, ...]
    stitched: Spectrum | None
    dark_subtracted: bool
    sequence: int = 0
    metadata: dict[str, object] = field(default_factory=dict)


def subtract_dark(signal: Spectrum, dark: Spectrum | None) -> Spectrum:
    if dark is None or len(signal.intensities) != len(dark.intensities):
        return signal
    values = tuple(max(0.0, value - background) for value, background in zip(signal.intensities, dark.intensities))
    return Spectrum(signal.channel, signal.wavelengths, values, signal.model, signal.serial)


def boxcar(values: Iterable[float], width: int) -> tuple[float, ...]:
    source = tuple(values)
    if width <= 1 or not source:
        return source
    width = width if width % 2 else width + 1
    radius = width // 2
    prefix = [0.0]
    for value in source:
        prefix.append(prefix[-1] + value)
    result: list[float] = []
    for i in range(len(source)):
        left, right = max(0, i - radius), min(len(source), i + radius + 1)
        result.append((prefix[right] - prefix[left]) / (right - left))
    return tuple(result)


def _interpolate(xs: tuple[float, ...], ys: tuple[float, ...], x: float) -> float | None:
    if not xs or x < xs[0] or x > xs[-1]:
        return None
    pos = bisect_left(xs, x)
    if pos == 0:
        return ys[0]
    if pos == len(xs):
        return ys[-1]
    if xs[pos] == x:
        return ys[pos]
    x0, x1 = xs[pos - 1], xs[pos]
    if x1 == x0:
        return ys[pos]
    ratio = (x - x0) / (x1 - x0)
    return ys[pos - 1] + ratio * (ys[pos] - ys[pos - 1])


def stitch_spectra(
    spectra: Iterable[Spectrum],
    mode: str = "auto",
    calibration_factors: dict[int, float] | None = None,
) -> Spectrum | None:
    """Join wavelength-ordered channels using raw, automatic, or calibrated scaling.

    ``raw`` keeps every selected channel sample unchanged. ``auto`` estimates a
    robust cumulative ratio from adjacent overlap regions. ``calibrated``
    applies the supplied per-channel response factors without estimating them
    from the current measurement.
    """
    if mode not in {"raw", "auto", "calibrated"}:
        raise ValueError(f"未知拼接模式：{mode}")
    factors = calibration_factors or {}
    if mode == "calibrated" and any(value <= 0 for value in factors.values()):
        raise ValueError("通道标定系数必须大于 0")
    usable = [s for s in spectra if s.wavelengths and len(s.wavelengths) == len(s.intensities)]
    if not usable:
        return None
    usable.sort(key=lambda s: min(s.wavelengths))
    current_x = tuple(usable[0].wavelengths)
    first_scale = factors.get(usable[0].channel, 1.0) if mode == "calibrated" else 1.0
    current_y = tuple(value * first_scale for value in usable[0].intensities)
    for spectrum in usable[1:]:
        pairs = sorted(zip(spectrum.wavelengths, spectrum.intensities))
        next_x = tuple(p[0] for p in pairs)
        next_y = tuple(p[1] for p in pairs)
        overlap_start = max(current_x[0], next_x[0])
        overlap_end = min(current_x[-1], next_x[-1])
        scale = factors.get(spectrum.channel, 1.0) if mode == "calibrated" else 1.0
        if overlap_end > overlap_start:
            if mode == "auto":
                ratios: list[float] = []
                for x, y in zip(next_x, next_y):
                    if overlap_start <= x <= overlap_end and abs(y) > 1e-9:
                        reference = _interpolate(current_x, current_y, x)
                        if reference is not None and reference > 0:
                            ratios.append(reference / y)
                if ratios:
                    ratios.sort()
                    trim = max(0, len(ratios) // 10)
                    core = ratios[trim:len(ratios) - trim] if len(ratios) - 2 * trim else ratios
                    scale = min(10.0, max(0.1, median(core)))
            cutoff = (overlap_start + overlap_end) / 2.0
            left = [(x, y) for x, y in zip(current_x, current_y) if x < cutoff]
            right = [(x, y * scale) for x, y in zip(next_x, next_y) if x >= cutoff]
            merged = left + right
        else:
            merged = list(zip(current_x, current_y)) + [(x, y * scale) for x, y in zip(next_x, next_y)]
        merged.sort(key=lambda p: p[0])
        current_x = tuple(p[0] for p in merged)
        current_y = tuple(p[1] for p in merged)
    return Spectrum(0, current_x, current_y, "Maria stitched", "")


def build_processed_frame(
    raw: Iterable[Spectrum],
    dark: dict[int, Spectrum] | None,
    subtract_background: bool,
    software_boxcar: int = 0,
    sequence: int = 0,
    metadata: dict[str, object] | None = None,
    stitch_mode: str = "auto",
    calibration_factors: dict[int, float] | None = None,
) -> Frame:
    raw_tuple = tuple(raw)
    shown: list[Spectrum] = []
    for spectrum in raw_tuple:
        processed = subtract_dark(spectrum, (dark or {}).get(spectrum.channel)) if subtract_background else spectrum
        if software_boxcar > 1:
            processed = Spectrum(
                processed.channel,
                processed.wavelengths,
                boxcar(processed.intensities, software_boxcar),
                processed.model,
                processed.serial,
            )
        shown.append(processed)
    return Frame(
        captured_at=datetime.now(),
        raw=raw_tuple,
        displayed=tuple(shown),
        stitched=stitch_spectra(shown, stitch_mode, calibration_factors),
        dark_subtracted=subtract_background,
        sequence=sequence,
        metadata=metadata or {},
    )
