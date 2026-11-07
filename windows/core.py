"""Ядро агента: обмен с сервером mixer.php. Не зависит от Windows — аудио приходит через Backend."""
import http.client
import json
import socket
import threading
import time
import urllib.parse

MASTER = "__master__"


class Backend:
    """Что должен уметь звуковой слой (см. audio_win.py; для тестов — FakeBackend)."""

    def init_thread(self): pass
    def list_apps(self): raise NotImplementedError      # [{name,title,volume 0-100,muted}], первым — MASTER
    def icon(self, name): return None                   # base64 PNG или None
    def set_volume(self, name, volume): raise NotImplementedError
    def set_muted(self, name, muted): raise NotImplementedError
    def media(self, kind): raise NotImplementedError    # 'playpause' | 'next' | 'prev'
    def list_devices(self): return []                   # [{id, name, default}] — устройства вывода звука
    def set_device(self, dev_id): raise NotImplementedError   # сделать устройство основным


class ServerError(Exception):
    def __init__(self, code, msg=""):
        super().__init__(f"HTTP {code} {msg}".strip())
        self.code = code


# Одно постоянное соединение на поток: не тратим время на новое TLS-рукопожатие при каждом обмене с сервером.
_tls = threading.local()


def _drop_conn():
    c = getattr(_tls, "conn", None)
    _tls.conn = None
    if c is not None:
        try:
            c.close()
        except Exception:
            pass


def _get_conn(u, timeout):
    """(соединение, переиспользовано ли)."""
    key = (u.scheme, u.netloc)
    c = getattr(_tls, "conn", None)
    if c is not None and getattr(_tls, "key", None) == key:
        c.timeout = timeout
        if c.sock is not None:
            c.sock.settimeout(timeout)
        return c, True
    _drop_conn()
    cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    _tls.conn, _tls.key = cls(u.netloc, timeout=timeout), key
    return _tls.conn, False


def call(server, key, action, body=None, timeout=15):
    u = urllib.parse.urlsplit(server.rstrip("/") + "/mixer.php")
    path = u.path + "?a=" + action
    data = json.dumps(body if body is not None else {}).encode("utf-8")
    headers = {"X-Key": key, "Content-Type": "application/json", "User-Agent": "MixerAgent/2.0", "Connection": "keep-alive"}
    for attempt in (0, 1):
        conn, reused = _get_conn(u, timeout)
        try:
            conn.request("POST", path, data, headers)
            resp = conn.getresponse()
            raw = resp.read()
        except socket.timeout:
            _drop_conn()
            raise
        except (http.client.HTTPException, OSError):
            _drop_conn()
            if reused and attempt == 0:      # сервер закрыл простаивающее соединение — открываем новое и повторяем
                continue
            raise
        if resp.status >= 400:
            if resp.getheader("Connection", "").lower() == "close":
                _drop_conn()
            raise ServerError(resp.status)
        return json.loads(raw.decode("utf-8") or "{}")


def pair_new(server, key):
    """Код для телефона: {'code': '123456', 'ttl': 300}."""
    return call(server, key, "pair_new")


class Agent:
    ICONS_PER_CYCLE = 5
    DEVICES_EVERY = 5          # список устройств вывода обновляем не чаще раза в 5 секунд

    def __init__(self, backend, get_config, on_status=None, log=print, version=0):
        self.backend = backend
        self.version = int(version)           # номер сборки (0 — локальная), показывается на телефоне
        self._devices, self._devices_at = [], 0.0
        self.get_config = get_config          # -> {'server':..., 'key':...}
        self.on_status = on_status or (lambda ok, text: None)
        self.log = log
        self.icons_sent = set()
        self.stop = threading.Event()

    def apply(self, cmd):
        b = self.backend
        if "media" in cmd:
            b.media(cmd["media"])
        elif "device" in cmd:
            b.set_device(str(cmd["device"]))
            self._devices_at = 0.0            # пусть следующий обмен сразу покажет новое устройство
        elif "name" in cmd and "volume" in cmd:
            b.set_volume(cmd["name"], max(0, min(100, int(cmd["volume"]))))
        elif "name" in cmd and "muted" in cmd:
            b.set_muted(cmd["name"], bool(cmd["muted"]))

    def _get_devices(self):
        if time.time() - self._devices_at >= self.DEVICES_EVERY:
            try:
                self._devices = self.backend.list_devices()
            except Exception as e:
                self.log(f"устройства вывода: {e}")
                self._devices = []
            self._devices_at = time.time()
        return self._devices

    def cycle(self, cfg):
        """Один обмен: отдать список приложений, получить и выполнить команды."""
        apps = self.backend.list_apps()
        icons = {}
        for a in apps:
            if len(icons) >= self.ICONS_PER_CYCLE:
                break
            if a["name"] != MASTER and a["name"] not in self.icons_sent:
                data = self.backend.icon(a["name"])
                if data:
                    icons[a["name"]] = data
                else:
                    self.icons_sent.add(a["name"])      # иконки нет — не спрашиваем снова
        resp = call(cfg["server"], cfg["key"], "agent",
                    {"apps": apps, "icons": icons, "devices": self._get_devices(), "ver": self.version}, timeout=20)
        self.icons_sent.update(icons)
        for cmd in resp.get("cmds", []):
            try:
                self.apply(cmd)
            except Exception as e:                    # одна плохая команда не должна ронять цикл
                self.log(f"команда {cmd!r}: {e}")
        return len(resp.get("cmds", []))

    def run(self):
        self.backend.init_thread()
        fails = 0
        while not self.stop.is_set():
            cfg = self.get_config()
            if not cfg.get("server") or not cfg.get("key"):
                self.on_status(False, "Не настроено")
                self.stop.wait(2)
                continue
            try:
                self.cycle(cfg)
                fails = 0
                self.on_status(True, "На связи")
            except ServerError as e:
                fails += 1
                self.on_status(False, "Неверный ключ" if e.code == 403 else f"Сервер: {e}")
                self.log(str(e))
                self.stop.wait(5)
            except Exception as e:
                fails += 1
                self.on_status(False, "Нет связи с сервером")
                if fails in (1, 10, 100):
                    self.log(f"нет связи: {e}")
                self.stop.wait(min(30, 2 + fails))
