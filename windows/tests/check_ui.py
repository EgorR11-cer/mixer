"""Быстрая проверка интерфейса без браузера: скрипт страницы собирается, manifest.json корректный."""
import json, os, re, subprocess, sys, tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
UI = os.path.join(ROOT, "server", "mixer")

html = open(os.path.join(UI, "index.html"), encoding="utf-8").read()
scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
assert scripts, "в index.html не найден <script>"
with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
    f.write(scripts[-1])
r = subprocess.run(["node", "--check", f.name], capture_output=True, text=True)
os.unlink(f.name)
assert r.returncode == 0, "ошибка в скрипте страницы:\n" + r.stderr

ids = re.findall(r'id="([^"]+)"', html)
dups = sorted({i for i in ids if ids.count(i) > 1})
assert not dups, f"повторяющиеся id в разметке: {dups}"

m = json.load(open(os.path.join(UI, "manifest.json"), encoding="utf-8"))
for icon in m["icons"]:
    assert os.path.exists(os.path.join(UI, os.path.basename(icon["src"]))), f"нет файла иконки {icon['src']}"
print("интерфейс: ОК")
