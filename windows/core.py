"""Ядро агента: обмен с сервером mixer.php. Не зависит от Windows — аудио приходит через Backend."""
import json
import threading
import time
import urllib.error
import urllib.request

MASTER = "__master__"


class Backend:
    """Что должен уметь звуковой слой (см. audio_win.py; для тестов — FakeBackend)."""

    def init_thread(self): pass
    def list_apps(self): raise NotImplementedError      # [{name,title,volume 0-100,muted}], первым — MASTER
    def icon(self, name): return None                   # base64 PNG или None
    def set_volume(self, name, volume): raise NotImplementedError
    def set_muted(self, name, muted): raise NotImplementedError
    def media(self, kind): raise NotImplementedError    # 'playpause' | 'next' | 'prev'


class ServerError(Exception):
    def __init__(self, code, msg=""):
        super().__init__(f"HTTP {code} {msg}".strip())
        self.code = code


def call(server, key, action, body=None, timeout=15):
    url = server.rstrip("/") + "/mixer.php?a=" + action
    data = json.dumps(body if body is not None else {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"X-Key": key, "Content-Type": "application/json",
                                          "User-Agent": "MixerAgent/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        raise ServerError(e.code) from None


def pair_new(server, key):
    """Код для телефона: {'code': '123456', 'ttl': 300}."""
    return call(server, key, "pair_new")


class Agent:
    ICONS_PER_CYCLE = 5

    def __init__(self, backend, get_config, on_status=None, log=print):
        self.backend = backend
        self.get_config = get_config          # -> {'server':..., 'key':...}
        self.on_status = on_status or (lambda ok, text: None)
        self.log = log
        self.icons_sent = set()
        self.stop = threading.Event()

    def apply(self, cmd):
        b = self.backend
        if "media" in cmd:
            b.media(cmd["media"])
        elif "name" in cmd and "volume" in cmd:
            b.set_volume(cmd["name"], max(0, min(100, int(cmd["volume"]))))
        elif "name" in cmd and "muted" in cmd:
            b.set_muted(cmd["name"], bool(cmd["muted"]))

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
        resp = call(cfg["server"], cfg["key"], "agent", {"apps": apps, "icons": icons}, timeout=20)
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
