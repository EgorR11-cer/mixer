"""Звуковой слой для Windows: громкость приложений и общая (pycaw), иконки, медиа-клавиши."""
import base64
import ctypes
import io

import psutil
from comtypes import CLSCTX_ALL, CoInitialize
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
from ctypes import POINTER, cast

from core import MASTER, Backend

ICON_BG = (0x22, 0x25, 0x2D)          # фон плитки в интерфейсе, чтобы иконка не получалась с чёрными краями
VK = {"playpause": 0xB3, "next": 0xB0, "prev": 0xB1}
KEYEVENTF_KEYUP = 0x0002


def _title(name):
    return name[:-4] if name.lower().endswith(".exe") else name


class WinAudio(Backend):
    def __init__(self):
        self._paths = {}              # name -> путь к exe (для иконок)

    def init_thread(self):
        CoInitialize()

    # --- общая громкость ---
    def _master(self):
        dev = AudioUtilities.GetSpeakers()
        ep = getattr(dev, "EndpointVolume", None)          # pycaw >= 2024
        if ep is not None:
            return ep
        iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)   # старые версии
        return cast(iface, POINTER(IAudioEndpointVolume))

    # --- приложения ---
    def _sessions(self):
        groups = {}
        for s in AudioUtilities.GetAllSessions():
            p = s.Process
            if p is None:
                continue                                   # «Системные звуки»
            try:
                name = p.name()
                self._paths.setdefault(name, p.exe())
            except (psutil.Error, OSError):
                continue
            groups.setdefault(name, []).append(s)
        return groups

    def list_apps(self):
        m = self._master()
        apps = [{"name": MASTER, "title": "Общая громкость",
                 "volume": round(m.GetMasterVolumeLevelScalar() * 100), "muted": bool(m.GetMute())}]
        for name, ss in sorted(self._sessions().items(), key=lambda kv: kv[0].lower()):
            v = ss[0].SimpleAudioVolume
            apps.append({"name": name, "title": _title(name),
                         "volume": round(v.GetMasterVolume() * 100), "muted": bool(v.GetMute())})
        return apps

    def set_volume(self, name, volume):
        if name == MASTER:
            self._master().SetMasterVolumeLevelScalar(volume / 100, None)
            return
        for s in self._sessions().get(name, []):
            s.SimpleAudioVolume.SetMasterVolume(volume / 100, None)

    def set_muted(self, name, muted):
        if name == MASTER:
            self._master().SetMute(1 if muted else 0, None)
            return
        for s in self._sessions().get(name, []):
            s.SimpleAudioVolume.SetMute(1 if muted else 0, None)

    def media(self, kind):
        vk = VK.get(kind)
        if vk:
            ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
            ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)

    # --- иконки ---
    def icon(self, name):
        path = self._paths.get(name)
        if not path:
            return None
        try:
            return _icon_b64(path)
        except Exception:
            return None


def _icon_b64(path):
    import win32api, win32con, win32gui, win32ui
    from PIL import Image
    large, small = win32gui.ExtractIconEx(path, 0)
    if not large:
        for h in small:
            win32gui.DestroyIcon(h)
        return None
    hicon = large[0]
    for h in large[1:] + small:
        win32gui.DestroyIcon(h)
    size = win32api.GetSystemMetrics(win32con.SM_CXICON)
    hdc = win32ui.CreateDCFromHandle(win32gui.GetDC(0))
    mem = hdc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(hdc, size, size)
    old = mem.SelectObject(bmp)
    brush = win32gui.CreateSolidBrush(ICON_BG[0] | (ICON_BG[1] << 8) | (ICON_BG[2] << 16))
    win32gui.FillRect(mem.GetSafeHdc(), (0, 0, size, size), brush)
    win32gui.DeleteObject(brush)
    win32gui.DrawIconEx(mem.GetSafeHdc(), 0, 0, hicon, size, size, 0, None, win32con.DI_NORMAL)
    raw = bmp.GetBitmapBits(True)
    mem.SelectObject(old)
    win32gui.DestroyIcon(hicon)
    img = Image.frombuffer("RGBA", (size, size), raw, "raw", "BGRA", 0, 1).convert("RGB").resize((64, 64), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")
