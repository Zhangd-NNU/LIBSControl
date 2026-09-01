"""LIBS 深度相机上位机入口。"""

import platform
import sys

from libs_camera.controller import DepthCameraController
from libs_camera.gui import run


def diagnose() -> int:
    """输出调试包运行环境和相机枚举结果。"""
    print("LIBS Depth Camera Debug Diagnostics")
    print(f"Python: {sys.version}")
    print(f"Platform: {platform.platform()}")
    for name, available in DepthCameraController.runtime_status().items():
        print(f"Dependency {name}: {'OK' if available else 'MISSING'}")
    try:
        devices = DepthCameraController.list_devices(include_simulated=False)
    except Exception as exc:
        print(f"Device enumeration: ERROR - {exc}")
        return 2
    print(f"RealSense devices: {len(devices)}")
    for device in devices:
        print(
            f"- {device.name} | SN {device.serial} | FW {device.firmware or '--'} | "
            f"USB {device.usb_type or '--'}"
        )
    return 0


if __name__ == "__main__":
    if "--diagnose" in sys.argv:
        raise SystemExit(diagnose())
    run()
