"""Микшер для ПК: значок в трее + агент, который связывает компьютер с телефоном через mixer.php.

Режимы запуска:
  (без аргументов)  — трей и агент
  --settings        — окно настроек (адрес сервера и ключ)
  --window          — окно микшера (то же, что на телефоне, с вкладкой подключения)
"""
import ctypes
import json
import logging
import logging.handlers
import os
import subprocess
import sys
import threading
import time
import webbrowser

APP = "Mixer"
DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP)
CONFIG = os.path.join(DIR, "config.json")
DEFAULT_SERVER = "https://bredinamerch.ru"
os.makedirs(DIR, exist_ok=True)

log = logging.getLogger("mixer")
log.setLevel(logging.INFO)
_h = logging.handlers.RotatingFileHandler(os.path.join(DIR, "agent.log"), maxBytes=200_000, backupCount=1, encoding="utf-8")
_h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
log.addHandler(_h)


# ---------- настройки ----------
_cfg_cache = {"mtime": None, "data": {}}

def load_config():
    """Читает config.json; перечитывается сам, когда файл поменяли в окне настроек."""
    try:
        m = os.path.getmtime(CONFIG)
        if m != _cfg_cache["mtime"]:
            with open(CONFIG, encoding="utf-8") as f:
                _cfg_cache["data"] = json.load(f)
            _cfg_cache["mtime"] = m
    except (OSError, ValueError):
        _cfg_cache["data"] = {}
    return _cfg_cache["data"]

def save_config(server, key):
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump({"server": server.strip().rstrip("/"), "key": key.strip()}, f, ensure_ascii=False)


def spawn(flag):
    """Окна запускаем отдельным процессом: у каждого свой главный поток и не мешают трею."""
    cmd = [sys.executable, flag] if getattr(sys, "frozen", False) else [sys.executable, os.path.abspath(__file__), flag]
    return subprocess.Popen(cmd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if flag == "--window" else 0)


# ---------- окно настроек ----------
def run_settings():
    import tkinter as tk
    from tkinter import messagebox
    cfg = load_config()
    root = tk.Tk()
    root.title("Микшер — настройки")
    root.resizable(False, False)
    pad = {"padx": 12, "pady": 6}
    tk.Label(root, text="Адрес сервера").grid(row=0, column=0, sticky="w", **pad)
    server = tk.Entry(root, width=42); server.grid(row=0, column=1, **pad)
    server.insert(0, cfg.get("server", DEFAULT_SERVER))
    tk.Label(root, text="Ключ доступа").grid(row=1, column=0, sticky="w", **pad)
    key = tk.Entry(root, width=42, show="•"); key.grid(row=1, column=1, **pad)
    key.insert(0, cfg.get("key", ""))
    tk.Label(root, text="Ключ лежит на сервере в data/mixer_key.txt", fg="#777").grid(row=2, column=1, sticky="w", padx=12)

    def ok():
        if not server.get().strip().startswith("http") or len(key.get().strip()) < 16:
            messagebox.showerror("Микшер", "Укажите адрес (https://…) и ключ не короче 16 символов.")
            return
        save_config(server.get(), key.get())
        root.destroy()
    tk.Button(root, text="Сохранить", command=ok, width=14).grid(row=3, column=1, sticky="e", **pad)
    root.bind("<Return>", lambda e: ok())
    root.mainloop()


# ---------- окно микшера ----------
def run_window():
    cfg = load_config()
    if not cfg.get("server") or not cfg.get("key"):
        return run_settings()
    from urllib.parse import quote
    url = f"{cfg['server'].rstrip('/')}/mixer/?desktop#{quote(cfg['key'], safe='')}"
    try:
        import webview
        webview.create_window("Микшер", url, width=420, height=780, background_color="#0e0f13")
        webview.start()
    except Exception as e:                       # нет pywebview/WebView2 — откроем в браузере
        log.info(f"webview: {e}")
        webbrowser.open(url)


# ---------- автозапуск ----------
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

def _launch_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pyw = sys.executable.replace("python.exe", "pythonw.exe")
    return f'"{pyw}" "{os.path.abspath(__file__)}"'

def autostart_enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP)
            return True
    except OSError:
        return False

def set_autostart(on):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP, 0, winreg.REG_SZ, _launch_command())
        else:
            try:
                winreg.DeleteValue(k, APP)
            except OSError:
                pass


# ---------- трей ----------
def msgbox(text, title="Микшер"):
    ctypes.windll.user32.MessageBoxW(0, text, title, 0x40 | 0x10000)   # информация, поверх всех окон

def ask(text, title="Микшер"):
    """Вопрос Да/Нет поверх всех окон; True — «Да»."""
    return ctypes.windll.user32.MessageBoxW(0, text, title, 0x24 | 0x40000) == 6

def tray_image(online):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 62, 62), 14, fill=(24, 26, 32, 255))
    for x, h in ((16, 40), (32, 24), (48, 34)):               # три ползунка
        d.rounded_rectangle((x - 3, 12, x + 3, 52), 3, fill=(60, 64, 76, 255))
        d.ellipse((x - 7, 52 - h - 6, x + 7, 52 - h + 8), fill=(77, 163, 255, 255))
    d.ellipse((42, 42, 60, 60), fill=(61, 220, 132, 255) if online else (229, 72, 77, 255), outline=(24, 26, 32, 255), width=3)
    return img

def run_tray():
    import pystray
    from core import Agent, pair_new
    from audio_win import WinAudio

    if not load_config().get("key"):               # первый запуск
        spawn("--settings").wait()

    state = {"ok": False, "text": "Подключаюсь…"}
    icon = pystray.Icon(APP, tray_image(False), "Микшер")

    def on_status(ok, text):
        changed = ok != state["ok"] or text != state["text"]
        state.update(ok=ok, text=text)
        if changed:
            icon.icon = tray_image(ok)
            icon.title = f"Микшер — {text}"
            icon.update_menu()

    def show_code(*_):
        cfg = load_config()
        try:
            d = pair_new(cfg["server"], cfg["key"])
            c = str(d["code"])
            msgbox(f"Код для телефона:\n\n        {c[:3]} {c[3:]}\n\nВведите его в приложении «Микшер». Действует 5 минут.")
        except Exception as e:
            log.info(f"pair_new: {e}")
            msgbox("Не удалось получить код. Проверьте адрес сервера и ключ в настройках.")

    def toggle_autostart(*_):
        set_autostart(not autostart_enabled())

    import updater
    updater.cleanup()
    declined = set()            # версии, от которых отказались в этом запуске

    def do_update(info):
        if not updater.can_self_update():
            webbrowser.open(info["page"] or f"https://github.com/{updater.REPO}/releases/latest")
            return
        icon.title = "Микшер — скачиваю обновление…"
        try:
            updater.install(info)
        except Exception as e:
            log.info(f"update: {e}")
            icon.title = "Микшер"
            msgbox("Не удалось установить обновление. Скачайте Mixer.exe вручную со страницы релизов.")
            webbrowser.open(info["page"] or f"https://github.com/{updater.REPO}/releases/latest")
            return
        # новая копия уже запущена и ждёт, пока эта закроется
        try:
            icon.stop()
        except Exception:
            pass
        time.sleep(0.5)
        os._exit(0)

    def check_updates(manual=False):
        if updater.current() == 0:
            if manual:
                msgbox("Это локальная сборка без номера версии, обновления для неё отключены.\nСкачайте Mixer.exe со страницы Releases на GitHub.")
            return
        try:
            info = updater.check()
        except Exception as e:
            log.info(f"update check: {e}")
            if manual:
                msgbox("Не удалось проверить обновления. Проверьте интернет.")
            return
        if info is None:
            if manual:
                msgbox("У вас последняя версия.")
            return
        if not manual and info["n"] in declined:
            return
        if ask(f"Вышла новая версия: {info['name']}.\n\nОбновить сейчас? Программа перезапустится сама."):
            do_update(info)
        else:
            declined.add(info["n"])

    def update_loop():
        agent.stop.wait(20)                       # даём программе спокойно запуститься
        while not agent.stop.is_set():
            check_updates()
            agent.stop.wait(6 * 3600)

    agent = Agent(WinAudio(), load_config, on_status, log.info)
    threading.Thread(target=agent.run, daemon=True, name="agent").start()
    threading.Thread(target=update_loop, daemon=True, name="updates").start()

    def quit_(*_):
        agent.stop.set()
        icon.stop()

    icon.menu = pystray.Menu(
        pystray.MenuItem(lambda item: state["text"], None, enabled=False),
        pystray.MenuItem("Открыть микшер", lambda *_: spawn("--window"), default=True),
        pystray.MenuItem("Подключить телефон…", show_code),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Запускать вместе с Windows", toggle_autostart, checked=lambda item: autostart_enabled()),
        pystray.MenuItem("Настройки…", lambda *_: spawn("--settings")),
        pystray.MenuItem("Проверить обновления…", lambda *_: threading.Thread(target=check_updates, args=(True,), daemon=True).start()),
        pystray.MenuItem(f"Версия: {updater.current() or 'локальная'}", None, enabled=False),
        pystray.MenuItem("Выход", quit_),
    )
    icon.run()


def single_instance(wait=0):
    """True, если других копий нет. После обновления новая копия ждёт (до wait секунд), пока закроется старая."""
    k = ctypes.windll.kernel32
    t0 = time.time()
    while True:
        h = k.CreateMutexW(None, False, "Local\\MixerAgentSingleton")
        if k.GetLastError() != 183:                    # 183 = ERROR_ALREADY_EXISTS
            return True, h
        k.CloseHandle(h)                               # без этого замок не освободится, даже когда старая копия закроется
        if time.time() - t0 >= wait:
            return False, None
        time.sleep(0.3)


if __name__ == "__main__":
    if "--settings" in sys.argv:
        run_settings()
    elif "--window" in sys.argv:
        run_window()
    else:
        ok, _mutex = single_instance(wait=20 if "--after-update" in sys.argv else 0)
        if ok:
            run_tray()
