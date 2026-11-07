"""Звуковой слой для Windows: громкость приложений и общая (pycaw), иконки, медиа-клавиши."""
import base64
import ctypes
import io
import time

import psutil
from comtypes import CLSCTX_ALL, CoInitialize
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
from ctypes import POINTER, cast

from core import MASTER, Backend

ICON_BG = (0x22, 0x25, 0x2D)          # фон плитки в интерфейсе, чтобы иконка не получалась с чёрными краями
VK = {"playpause": 0xB3, "next": 0xB0, "prev": 0xB1}
KEYEVENTF_KEYUP = 0x0002


# ---- переключение устройства вывода (системный COM-интерфейс IPolicyConfig, его использует и сама Windows) ----
_policy = None


def _policy_config():
    global _policy
    if _policy is None:
        import comtypes
        import comtypes.client
        from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

        def stub(name):
            return COMMETHOD([], HRESULT, name)       # методы, которые мы не вызываем: нужны только чтобы порядок в таблице совпал

        class IPolicyConfig(IUnknown):
            _iid_ = GUID("{F8679F50-850A-41CF-9C72-430F290290C8}")
            _methods_ = (
                stub("GetMixFormat"), stub("GetDeviceFormat"), stub("ResetDeviceFormat"), stub("SetDeviceFormat"),
                stub("GetProcessingPeriod"), stub("SetProcessingPeriod"), stub("GetShareMode"), stub("SetShareMode"),
                stub("GetPropertyValue"), stub("SetPropertyValue"),
                COMMETHOD([], HRESULT, "SetDefaultEndpoint",
                          (["in"], ctypes.c_wchar_p, "deviceId"), (["in"], ctypes.c_int, "role")),
                stub("SetEndpointVisibility"),
            )

        _policy = comtypes.client.CreateObject(GUID("{870AF99C-171D-4F9E-AF0D-E63DF40C2BC9}"), interface=IPolicyConfig)
    return _policy


def _is_render(dev_id):
    return str(dev_id).startswith("{0.0.0.")          # {0.0.0…} — вывод, {0.0.1…} — микрофоны


def _is_active(state):
    try:
        return int(getattr(state, "value", state)) == 1
    except (TypeError, ValueError):
        return str(getattr(state, "name", state)) == "Active"


def _title(name):
    return name[:-4] if name.lower().endswith(".exe") else name


class WinAudio(Backend):
    SESSION_TTL = 1.0                 # столько секунд список сессий считается свежим (иначе каждая команда обходила бы их все)

    def __init__(self):
        self._paths = {}              # name -> путь к exe (для иконок)
        self._cache, self._cache_at = {}, 0.0

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
    def _sessions(self, fresh=False):
        if not fresh and self._cache and time.monotonic() - self._cache_at < self.SESSION_TTL:
            return self._cache
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
        self._cache, self._cache_at = groups, time.monotonic()
        return groups

    def _each(self, name, fn):
        """Применить fn ко всем сессиям приложения; если список устарел (приложение закрылось) — один раз обновить и повторить."""
        for fresh in (False, True):
            try:
                for s in self._sessions(fresh).get(name, []):
                    fn(s)
                return
            except Exception:
                if fresh:
                    raise

    def list_apps(self):
        m = self._master()
        apps = [{"name": MASTER, "title": "Общая громкость",
                 "volume": round(m.GetMasterVolumeLevelScalar() * 100), "muted": bool(m.GetMute())}]
        for name, ss in sorted(self._sessions(fresh=True).items(), key=lambda kv: kv[0].lower()):
            v = ss[0].SimpleAudioVolume
            apps.append({"name": name, "title": _title(name),
                         "volume": round(v.GetMasterVolume() * 100), "muted": bool(v.GetMute())})
        return apps

    def set_volume(self, name, volume):
        if name == MASTER:
            self._master().SetMasterVolumeLevelScalar(volume / 100, None)
            return
        self._each(name, lambda s: s.SimpleAudioVolume.SetMasterVolume(volume / 100, None))

    def set_muted(self, name, muted):
        if name == MASTER:
            self._master().SetMute(1 if muted else 0, None)
            return
        self._each(name, lambda s: s.SimpleAudioVolume.SetMute(1 if muted else 0, None))

    # --- устройства вывода ---
    def list_devices(self):
        dev = AudioUtilities.GetSpeakers()
        cur = getattr(dev, "id", None) or dev.GetId()
        out = []
        for d in AudioUtilities.GetAllDevices():
            if not _is_render(d.id) or not _is_active(d.state):
                continue
            out.append({"id": d.id, "name": getattr(d, "FriendlyName", None) or d.id, "default": d.id == cur})
        return out

    def set_device(self, dev_id):
        if dev_id not in {d["id"] for d in self.list_devices()}:
            raise ValueError("неизвестное устройство вывода")
        pc = _policy_config()
        for role in (0, 1, 2):                             # консоль, мультимедиа, связь
            pc.SetDefaultEndpoint(dev_id, role)
        self._cache, self._cache_at = {}, 0.0              # у нового устройства свои сессии

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
