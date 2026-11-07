"""Сквозная проверка без Windows: настоящий mixer.php (php -S) + агент на фейковом звуке + «телефон»."""
import datetime, json, os, shutil, statistics, subprocess, sys, tempfile, threading, time, urllib.request, urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core import Agent, Backend, MASTER, call

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
KEY = "k" * 32
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"


class Fake(Backend):
    def __init__(self):
        self.apps = {MASTER: [70, False], "Discord.exe": [100, False], "game.exe": [50, False]}
        self.media_log = []
        self.devices = {"dev-speakers": "Колонки", "dev-headset": "Наушники"}
        self.default = "dev-speakers"
    def list_devices(self):
        return [{"id": i, "name": n, "default": i == self.default} for i, n in self.devices.items()]
    def set_device(self, i):
        if i not in self.devices: raise ValueError(i)
        self.default = i
    def list_apps(self):
        return [{"name": n, "title": n.replace(".exe", ""), "volume": v, "muted": m} for n, (v, m) in self.apps.items()]
    def icon(self, name): return "iVBORw0KGgo" + "A" * 40 if name == "Discord.exe" else None
    def set_volume(self, n, v): self.apps[n][0] = v
    def set_muted(self, n, m): self.apps[n][1] = m
    def media(self, k): self.media_log.append(k)


def phone(action, body=None, key=KEY, method=None):
    req = urllib.request.Request(f"{BASE}/mixer.php?a={action}", method=method or ("POST" if body is not None else "GET"),
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-Key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def wait(cond, t=8):
    t0 = time.time()
    while time.time() - t0 < t:
        if cond(): return True
        time.sleep(0.1)
    return False


def main():
    d = tempfile.mkdtemp()
    shutil.copy(os.path.join(ROOT, "server", "mixer.php"), d)
    os.makedirs(os.path.join(d, "data"))
    open(os.path.join(d, "data", "mixer_key.txt"), "w").write(KEY)
    srv = subprocess.Popen(["php", "-S", f"127.0.0.1:{PORT}", "-t", d], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           env={**os.environ, "PHP_CLI_SERVER_WORKERS": "6"})
    try:
        time.sleep(1)
        fb = Fake()
        ag = Agent(fb, lambda: {"server": BASE, "key": KEY}, log=print, version=123)
        threading.Thread(target=ag.run, daemon=True).start()

        assert phone("state", key="bad")[0] == 403, "чужой ключ должен получать 403"
        assert wait(lambda: phone("state")[1].get("online")), "агент не вышел на связь"
        st = phone("state")[1]
        assert [a["name"] for a in st["apps"]][0] == MASTER and len(st["apps"]) == 3
        assert wait(lambda: any(a.get("icon") for a in phone("state")[1]["apps"])), "иконка не дошла"
        assert phone("icon&n=Discord.exe")[1]["data"].startswith("iVBOR")

        # --- сопряжение ---
        assert phone("pair_new", {}, key="bad")[0] == 403
        code = phone("pair_new", {})[1]["code"]
        assert len(code) == 6
        for _ in range(5):
            assert phone("pair_claim", {"code": "000000" if code != "000000" else "111111"}, key="")[0] == 403
        assert phone("pair_claim", {"code": code}, key="")[0] == 429, "после 5 неверных попыток — блокировка"
        # блокировка по IP, поэтому проверим успешный путь в отдельном прогоне ниже

        # --- управление с телефона через токен: берём токен напрямую (обход лимита по IP) ---
        pf = os.path.join(d, "data", "mixer_pair.json")
        os.remove(pf)
        code = phone("pair_new", {})[1]["code"]
        s, r = phone("pair_claim", {"code": code}, key="")
        assert s == 200 and len(r["key"]) == 48, (s, r)
        tok = r["key"]
        assert phone("pair_claim", {"code": code}, key="")[0] == 403, "код одноразовый"
        assert phone("state", key=tok)[0] == 200
        assert phone("agent", {"apps": []}, key=tok)[0] == 403, "телефон не должен выдавать себя за ПК"
        assert phone("pair_new", {}, key=tok)[0] == 403

        assert phone("set", {"name": "game.exe", "volume": 20}, key=tok)[0] == 200
        assert wait(lambda: fb.apps["game.exe"][0] == 20), "громкость не применилась"
        assert phone("set", {"name": MASTER, "volume": 33}, key=tok)[0] == 200
        assert wait(lambda: fb.apps[MASTER][0] == 33)
        phone("set", {"name": "Discord.exe", "muted": True}, key=tok)
        assert wait(lambda: fb.apps["Discord.exe"][1] is True)
        phone("set", {"media": "next"}, key=tok)
        assert wait(lambda: fb.media_log == ["next"])
        assert phone("set", {"media": "rm -rf"}, key=tok)[0] == 400

        # --- пресеты ---
        assert wait(lambda: any(a["name"] == "game.exe" and a["volume"] == 20 for a in phone("state")[1]["apps"]))
        assert phone("preset_save", {"name": "Игра"}, key=tok)[0] == 200
        assert phone("state", key=tok)[1]["preset_counts"]["Игра"] == 3
        phone("set", {"name": "game.exe", "volume": 90}, key=tok)
        assert wait(lambda: fb.apps["game.exe"][0] == 90)
        phone("preset_apply", {"name": "Игра"}, key=tok)
        assert wait(lambda: fb.apps["game.exe"][0] == 20), "пресет не применился"
        phone("preset_delete", {"name": "Игра"}, key=tok)
        assert phone("state", key=tok)[1]["presets"] == []

        # --- версии ---
        st = phone("state", key=tok)[1]
        assert st["pc_ver"] == 123, "номер версии программы на ПК должен доходить до телефона"
        assert st["server_ver"], "сервер должен сообщать свою версию"

        # --- устройство вывода звука ---
        assert [d["id"] for d in st["devices"]] == ["dev-speakers", "dev-headset"]
        assert phone("set", {"device": "нет-такого"}, key=tok)[0] == 400, "чужой id устройства не принимается"
        assert phone("set", {"device": "dev-headset"}, key=tok)[0] == 200
        assert wait(lambda: fb.default == "dev-headset"), "устройство вывода не переключилось"
        assert wait(lambda: [d["id"] for d in phone("state", key=tok)[1]["devices"] if d["default"]] == ["dev-headset"])

        # --- пресеты по времени ---
        now = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)
        hhmm = now.strftime("%H:%M")
        other_day = (now.isoweekday() % 7) + 1                       # любой день, кроме сегодняшнего
        phone("preset_save", {"name": "Тишина"}, key=tok)
        assert phone("sched_save", {"time": "25:00", "name": "Тишина"}, key=tok)[0] == 400, "кривое время"
        assert phone("sched_save", {"time": hhmm, "name": "Нет"}, key=tok)[0] == 404, "нет такого пресета"
        phone("set", {"name": "game.exe", "volume": 77}, key=tok)
        assert wait(lambda: fb.apps["game.exe"][0] == 77)
        phone("preset_save", {"name": "Громко"}, key=tok)           # game.exe = 77
        phone("set", {"name": "game.exe", "volume": 5}, key=tok)
        assert wait(lambda: fb.apps["game.exe"][0] == 5)
        # правило на другой день недели не должно сработать
        assert phone("sched_save", {"time": hhmm, "name": "Громко", "days": [other_day], "tz": "UTC"}, key=tok)[0] == 200
        time.sleep(5)
        assert fb.apps["game.exe"][0] == 5, "правило не на сегодня не должно срабатывать"
        # правило на сегодня срабатывает само, пока ПК на связи
        assert phone("sched_save", {"time": hhmm, "name": "Громко", "days": [now.isoweekday()], "tz": "UTC"}, key=tok)[0] == 200
        assert wait(lambda: fb.apps["game.exe"][0] == 77, 10), "пресет по расписанию не применился"
        sc = phone("state", key=tok)[1]["schedules"]
        assert len(sc) == 2 and phone("state", key=tok)[1]["tz"] == "UTC"
        # в тот же день правило второй раз не срабатывает
        phone("set", {"name": "game.exe", "volume": 5}, key=tok)
        assert wait(lambda: fb.apps["game.exe"][0] == 5)
        time.sleep(5)
        assert fb.apps["game.exe"][0] == 5, "правило должно срабатывать раз в день"
        phone("sched_delete", {"id": sc[0]["id"]}, key=tok)
        assert len(phone("state", key=tok)[1]["schedules"]) == 1
        phone("preset_delete", {"name": "Громко"}, key=tok)
        assert phone("state", key=tok)[1]["schedules"] == [], "правила удалённого пресета уходят вместе с ним"
        phone("preset_delete", {"name": "Тишина"}, key=tok)

        # --- задержка: от запроса телефона до применения на ПК ---
        lat = []
        for i in range(25):
            v = 10 + i
            t0 = time.time()
            phone("set", {"name": "Discord.exe", "volume": v}, key=tok)
            assert wait(lambda: fb.apps["Discord.exe"][0] == v, 5)
            lat.append((time.time() - t0) * 1000)
            time.sleep(0.07)
        print(f"задержка команды: медиана {statistics.median(lat):.0f} мс, максимум {max(lat):.0f} мс")

        # --- отвязка ---
        assert phone("unpair", {}, key=tok)[0] == 200
        assert phone("state", key=tok)[0] == 403, "токен должен быть отозван"

        # --- CORS для приложения ---
        req = urllib.request.Request(f"{BASE}/mixer.php?a=state", method="OPTIONS",
                                     headers={"Origin": "https://appassets.androidplatform.net"})
        with urllib.request.urlopen(req) as r:
            assert r.status == 204 and r.headers["Access-Control-Allow-Origin"] == "https://appassets.androidplatform.net"
            assert "X-Key" in r.headers["Access-Control-Allow-Headers"]
        ag.stop.set()
        print("ВСЕ ПРОВЕРКИ ПРОШЛИ")
    finally:
        srv.terminate(); shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()
