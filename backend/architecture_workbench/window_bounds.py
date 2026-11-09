"""Respect each Windows monitor's work area for a frameless desktop window.

WM_GETMINMAXINFO applies to titlebar buttons, double-click and OS maximize actions.
The callback stays alive with the window and does not alter global taskbar state.
"""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import os

WM_GETMINMAXINFO = 0x0024
WM_NCDESTROY = 0x0082
SUBCLASS_ID = 0x53415243

class POINT(ctypes.Structure):
    _fields_ = [('x', wintypes.LONG), ('y', wintypes.LONG)]

class RECT(ctypes.Structure):
    _fields_ = [('left', wintypes.LONG), ('top', wintypes.LONG),
                ('right', wintypes.LONG), ('bottom', wintypes.LONG)]

class MINMAXINFO(ctypes.Structure):
    _fields_ = [('reserved', POINT), ('max_size', POINT), ('max_position', POINT),
                ('min_track', POINT), ('max_track', POINT)]

class MONITORINFO(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('monitor', RECT), ('work', RECT), ('flags', wintypes.DWORD)]

def maximize_geometry(monitor, work):
    """Monitor-relative position and physical size; works with negative origins."""
    left, top, right, bottom = work
    if right <= left or bottom <= top:
        raise ValueError('Invalid monitor work area')
    return left - monitor[0], top - monitor[1], right - left, bottom - top

def rect_tuple(value):
    return value.left, value.top, value.right, value.bottom

class WorkAreaGuard:
    def __init__(self, handle):
        if os.name != 'nt': raise OSError('Windows window bounds require Windows')
        self.handle = int(handle)
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.controls = ctypes.WinDLL('comctl32', use_last_error=True)
        self.user.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        self.user.MonitorFromWindow.restype = wintypes.HANDLE
        self.user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
        self.user.GetMonitorInfoW.restype = wintypes.BOOL
        self.user.IsZoomed.argtypes = [wintypes.HWND]
        self.user.IsZoomed.restype = wintypes.BOOL
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                                          ctypes.c_size_t, ctypes.c_ssize_t, ctypes.c_size_t, ctypes.c_size_t)
        self.callback = callback_type(self._message)
        self.controls.SetWindowSubclass.argtypes = [wintypes.HWND, callback_type, ctypes.c_size_t, ctypes.c_size_t]
        self.controls.SetWindowSubclass.restype = wintypes.BOOL
        self.controls.RemoveWindowSubclass.argtypes = [wintypes.HWND, callback_type, ctypes.c_size_t]
        self.controls.RemoveWindowSubclass.restype = wintypes.BOOL
        self.controls.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
        self.controls.DefSubclassProc.restype = ctypes.c_ssize_t
        if not self.controls.SetWindowSubclass(self.handle, self.callback, SUBCLASS_ID, 0):
            raise ctypes.WinError(ctypes.get_last_error())

    def _message(self, handle, message, wparam, lparam, subclass_id, data):
        if message == WM_NCDESTROY:
            self.controls.RemoveWindowSubclass(handle, self.callback, SUBCLASS_ID)
        result = self.controls.DefSubclassProc(handle, message, wparam, lparam)
        if message == WM_GETMINMAXINFO and lparam:
            info = MONITORINFO(); info.size = ctypes.sizeof(info)
            monitor = self.user.MonitorFromWindow(handle, 2)
            if monitor and self.user.GetMonitorInfoW(monitor, ctypes.byref(info)):
                bounds = ctypes.cast(lparam, ctypes.POINTER(MINMAXINFO)).contents
                x, y, width, height = maximize_geometry(rect_tuple(info.monitor), rect_tuple(info.work))
                bounds.max_position.x, bounds.max_position.y = x, y
                bounds.max_size.x, bounds.max_size.y = width, height
        return result

    @property
    def maximized(self):
        return bool(self.user.IsZoomed(self.handle))

def install_work_area_guard(window):
    if os.name != 'nt': return None
    from System import Action
    form = window.native
    def install():
        if not getattr(window, '_work_area_guard', None):
            window._work_area_guard = WorkAreaGuard(form.Handle.ToInt64())
    if form.InvokeRequired: form.Invoke(Action(install))
    else: install()
    return window._work_area_guard
