"""Maria four-channel spectrometer control package."""

from .controller import (
    DeviceInfo,
    NetworkDevice,
    SeaSDKController,
    SimulationController,
    SpectrometerError,
    discover_network_devices,
    parse_oceanhood_announcement,
)
from .processing import Frame, Spectrum, build_processed_frame, stitch_spectra

__all__ = [
    "DeviceInfo",
    "Frame",
    "NetworkDevice",
    "SeaSDKController",
    "SimulationController",
    "SpectrometerError",
    "Spectrum",
    "build_processed_frame",
    "discover_network_devices",
    "parse_oceanhood_announcement",
    "stitch_spectra",
]
