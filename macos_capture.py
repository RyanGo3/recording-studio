"""macOS screen geometry, permissions and capture arguments.

Coordinates use logical display points, matching Tk. Retina captures are scaled
to that coordinate system before cropping. Only the main display is supported.
Native frameworks are loaded lazily so Windows imports remain safe.
"""
from __future__ import annotations

import ctypes
from functools import lru_cache


class Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class Size(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class Rect(ctypes.Structure):
    _fields_ = [("origin", Point), ("size", Size)]


@lru_cache(maxsize=1)
def quartz():
    api = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    api.CGMainDisplayID.restype = ctypes.c_uint32
    api.CGDisplayBounds.argtypes = [ctypes.c_uint32]
    api.CGDisplayBounds.restype = Rect
    api.CGGetActiveDisplayList.argtypes = [
        ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
    api.CGGetActiveDisplayList.restype = ctypes.c_int32
    api.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
    api.CGRequestScreenCaptureAccess.restype = ctypes.c_bool
    api.CGEventCreate.argtypes = [ctypes.c_void_p]
    api.CGEventCreate.restype = ctypes.c_void_p
    api.CGEventGetLocation.argtypes = [ctypes.c_void_p]
    api.CGEventGetLocation.restype = Point
    api.CGEventSourceButtonState.argtypes = [ctypes.c_int32, ctypes.c_uint32]
    api.CGEventSourceButtonState.restype = ctypes.c_bool
    return api


@lru_cache(maxsize=1)
def core_foundation():
    api = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    api.CFRelease.argtypes = [ctypes.c_void_p]
    api.CFRelease.restype = None
    return api


def main_display() -> tuple[int, int, int, int]:
    api = quartz()
    bounds = api.CGDisplayBounds(api.CGMainDisplayID())
    return (round(bounds.origin.x), round(bounds.origin.y),
            round(bounds.size.width), round(bounds.size.height))


def require_screen_permission() -> None:
    api = quartz()
    if not api.CGPreflightScreenCaptureAccess():
        api.CGRequestScreenCaptureAccess()
        raise RuntimeError(
            "请在系统设置 → 隐私与安全性 → 屏幕录制中允许土豆工厂，"
            "然后退出并重新打开程序。源码运行时请授权启动它的终端。")


def screen_index() -> int:
    # FFmpeg enumerates capture screens with this same CoreGraphics API.
    api = quartz()
    displays = (ctypes.c_uint32 * 32)()
    count = ctypes.c_uint32()
    if api.CGGetActiveDisplayList(32, displays, ctypes.byref(count)):
        raise RuntimeError("无法读取 macOS 显示器列表。")
    main = api.CGMainDisplayID()
    for index in range(min(count.value, len(displays))):
        if displays[index] == main:
            return index
    raise RuntimeError("没有找到主显示器。")


def capture_arguments(region: tuple[int, int, int, int], fps: int,
                      draw_cursor: bool) -> list[str]:
    dx, dy, dw, dh = main_display()
    x, y, width, height = region
    if (width < 16 or height < 16 or x < dx or y < dy
            or x + width > dx + dw or y + height > dy + dh):
        raise ValueError("macOS 版目前只支持录制主显示器及其内部区域。")
    return [
        "-f", "avfoundation", "-framerate", str(fps), "-pixel_format", "bgr0",
        "-capture_cursor", "1" if draw_cursor else "0",
        "-i", f"Capture screen {screen_index()}:none",
        "-vf", f"scale={dw}:{dh},crop={width}:{height}:{x-dx}:{y-dy},setsar=1",
    ]


def cursor_state() -> tuple[float, float, list[bool]]:
    api = quartz()
    event = api.CGEventCreate(None)
    if not event:
        raise RuntimeError("无法读取 macOS 光标位置。")
    try:
        point = api.CGEventGetLocation(event)
    finally:
        core_foundation().CFRelease(event)
    return point.x, point.y, [bool(api.CGEventSourceButtonState(1, key)) for key in (0, 1)]
