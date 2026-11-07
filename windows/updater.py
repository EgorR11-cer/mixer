"""Проверка и установка новых версий Mixer.exe из GitHub Releases."""
import json
import os
import subprocess
import sys
import urllib.request

REPO = "EgorR11-cer/mixer"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET = "Mixer.exe"


def current():
    """Номер установленной сборки (0 — локальная сборка без номера)."""
    try:
        from app_version import VERSION
        return int(VERSION)
    except Exception:
        return 0


def latest():
    """Последний релиз: {'n', 'name', 'url', 'size', 'page'} или None, если не удалось/нет Mixer.exe."""
    req = urllib.request.Request(API, headers={"User-Agent": "MixerAgent", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.loads(r.read().decode("utf-8"))
    try:
        n = int(str(d.get("tag_name", "")).lstrip("vV"))
    except ValueError:
        return None
    asset = next((a for a in d.get("assets", []) if a.get("name") == ASSET), None)
    if not asset:
        return None
    return {"n": n, "name": d.get("name") or f"1.{n}", "url": asset["browser_download_url"],
            "size": int(asset.get("size") or 0), "page": d.get("html_url", "")}


def check():
    """Новый релиз, если он новее установленной сборки, иначе None."""
    info = latest()
    return info if info and info["n"] > current() else None


def can_self_update():
    return bool(getattr(sys, "frozen", False)) and os.name == "nt"


# Ждём, пока старый Mixer.exe закроется (пока он запущен, файл занят и move не проходит), подменяем и запускаем.
_BAT = """@echo off
for /l %%i in (1,1,60) do (
  move /y "%MX_NEW%" "%MX_OLD%" >nul 2>&1 && goto ok
  timeout /t 1 /nobreak >nul
)
exit /b 1
:ok
start "" "%MX_OLD%"
(goto) 2>nul & del "%~f0"
"""


def download(info, new_path):
    req = urllib.request.Request(info["url"], headers={"User-Agent": "MixerAgent"})
    with urllib.request.urlopen(req, timeout=60) as r, open(new_path, "wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
    size = os.path.getsize(new_path)
    with open(new_path, "rb") as f:
        head = f.read(2)
    if head != b"MZ" or (info["size"] and size != info["size"]):
        os.remove(new_path)
        raise RuntimeError("скачанный файл повреждён")


def install(info):
    """Скачать новый exe рядом со старым и запустить подменяющий скрипт. После True — программу нужно закрыть."""
    old = sys.executable
    new = old + ".new"
    download(info, new)
    bat = os.path.join(os.environ.get("TEMP", os.path.dirname(old)), "mixer_update.bat")
    with open(bat, "w", encoding="ascii") as f:        # только ASCII: пути с кириллицей идут через переменные окружения
        f.write(_BAT)
    env = dict(os.environ, MX_OLD=old, MX_NEW=new, PYINSTALLER_RESET_ENVIRONMENT="1")
    env.pop("_MEIPASS2", None)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(["cmd", "/c", bat], env=env, creationflags=flags, close_fds=True)
    return True
