<?php
// Посредник между ПК (программа Микшер) и телефоном (приложение / страница /mixer/). Лежит в корне сайта.
// Авторизация (заголовок X-Key):
//   • мастер-ключ  — для программы на ПК; лежит в data/mixer_key.txt (папка data закрыта .htaccess);
//   • токен устройства — выдаётся телефону при сопряжении по коду, можно отозвать («Отвязать»).
// Состояние: data/mixer.json, иконки: data/mixer_icons.json, токены: data/mixer_tokens.json,
// коды сопряжения и счётчики попыток: data/mixer_pair.json.

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
header('X-Robots-Tag: noindex');

// CORS: нужен приложению Android (страница открывается с appassets.androidplatform.net)
const MX_ORIGINS = ['https://appassets.androidplatform.net', 'https://bredinamerch.ru', 'https://www.bredinamerch.ru'];
$origin = (string)($_SERVER['HTTP_ORIGIN'] ?? '');
if ($origin !== '' && in_array($origin, MX_ORIGINS, true)) {
    header('Access-Control-Allow-Origin: ' . $origin);
    header('Vary: Origin');
    header('Access-Control-Allow-Headers: X-Key, Content-Type');
    header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
    header('Access-Control-Max-Age: 86400');
}
if (($_SERVER['REQUEST_METHOD'] ?? '') === 'OPTIONS') { http_response_code(204); exit; }

define('MX_DIR', __DIR__ . '/data');
define('MX_FILE', MX_DIR . '/mixer.json');
define('MX_ICONS', MX_DIR . '/mixer_icons.json');
define('MX_TOKENS', MX_DIR . '/mixer_tokens.json');
define('MX_PAIR', MX_DIR . '/mixer_pair.json');
const MX_DEF = ['apps' => [], 'ts' => 0, 'cmds' => [], 'presets' => [], 'icon_names' => []];
const MX_MAX_DEVICES = 10;
const MX_PAIR_TTL = 300;
@set_time_limit(25);
ignore_user_abort(false);

function mx_open(string $file = MX_FILE, array $def = MX_DEF): array {
    $f = fopen($file, 'c+');
    flock($f, LOCK_EX);
    $s = json_decode((string)stream_get_contents($f), true);
    if (!is_array($s)) $s = [];
    $s += $def;
    return [$f, $s];
}
function mx_close($f, ?array $s = null): void {
    if ($s !== null) {
        ftruncate($f, 0);
        rewind($f);
        fwrite($f, json_encode($s, JSON_UNESCAPED_UNICODE));
        fflush($f);
    }
    flock($f, LOCK_UN);
    fclose($f);
}
function mx_in(): array {
    $d = json_decode((string)file_get_contents('php://input', false, null, 0, 1500000), true);
    return is_array($d) ? $d : [];
}
function mx_out($d, int $code = 200): void {
    if ($code !== 200) http_response_code($code);
    echo json_encode($d, JSON_UNESCAPED_UNICODE);
    exit;
}
function mx_master_key(): string {
    $f = MX_DIR . '/mixer_key.txt';
    $k = is_file($f) ? trim((string)file_get_contents($f)) : '';
    return strlen($k) >= 16 ? $k : '';
}
// 'master' | 'device' | null
function mx_auth(?string &$tokenHash = null): ?string {
    $key = (string)($_SERVER['HTTP_X_KEY'] ?? '');
    if ($key === '') return null;
    $master = mx_master_key();
    if ($master !== '' && hash_equals($master, $key)) return 'master';
    $h = hash('sha256', $key);
    [$f, $t] = mx_open(MX_TOKENS, ['devices' => []]);
    $found = isset($t['devices'][$h]);
    if ($found && ($t['devices'][$h]['seen'] ?? 0) < time() - 3600) {   // раз в час отмечаем «последний вход»
        $t['devices'][$h]['seen'] = time();
        mx_close($f, $t);
    } else {
        mx_close($f);
    }
    if ($found) { $tokenHash = $h; return 'device'; }
    return null;
}
function mx_deny(): void { mx_out(['error' => 'forbidden'], 403); }
function mx_ip(): string { return (string)($_SERVER['REMOTE_ADDR'] ?? '0'); }

// Поставить команду; новая команда того же вида для того же приложения заменяет старую
function mx_push(array &$s, string $name, string $kind, $value): void {
    $s['cmds'] = array_values(array_filter($s['cmds'], fn($c) => !(($c['name'] ?? null) === $name && array_key_exists($kind, $c))));
    $s['cmds'][] = ['name' => $name, $kind => $value];
    foreach ($s['apps'] as &$app) {
        if ($app['name'] === $name) $app[$kind] = $value;
    }
    unset($app);
    if (count($s['cmds']) > 300) $s['cmds'] = array_slice($s['cmds'], -300);
}
function mx_pname(array $in): string {
    return trim(mb_substr((string)($in['name'] ?? ''), 0, 30));
}

$a = (string)($_GET['a'] ?? '');

// ---- Телефон: ввод кода с экрана ПК → токен устройства (без X-Key) ----
if ($a === 'pair_claim') {
    $code = preg_replace('/\D/', '', (string)(mx_in()['code'] ?? ''));
    $ip = mx_ip(); $now = time();
    [$f, $p] = mx_open(MX_PAIR, ['codes' => [], 'fails' => []]);
    foreach ($p['codes'] as $c => $exp) if ($exp < $now) unset($p['codes'][$c]);
    $fails = array_values(array_filter($p['fails'][$ip] ?? [], fn($t) => $t > $now - 600));
    if (count($fails) >= 5) { $p['fails'][$ip] = $fails; mx_close($f, $p); mx_out(['error' => 'rate'], 429); }
    if (strlen($code) !== 6 || !isset($p['codes'][$code])) {
        $fails[] = $now; $p['fails'][$ip] = $fails;
        foreach ($p['fails'] as $k => $v) if (!$v || max($v) < $now - 600) unset($p['fails'][$k]);
        mx_close($f, $p);
        mx_out(['error' => 'bad code'], 403);
    }
    unset($p['codes'][$code]); unset($p['fails'][$ip]);   // код одноразовый
    mx_close($f, $p);

    $token = bin2hex(random_bytes(24));
    [$ft, $t] = mx_open(MX_TOKENS, ['devices' => []]);
    $t['devices'][hash('sha256', $token)] = ['created' => $now, 'seen' => $now, 'ua' => mb_substr((string)($_SERVER['HTTP_USER_AGENT'] ?? ''), 0, 120)];
    if (count($t['devices']) > MX_MAX_DEVICES) {            // самые старые отваливаются первыми
        uasort($t['devices'], fn($x, $y) => ($x['seen'] ?? 0) <=> ($y['seen'] ?? 0));
        $t['devices'] = array_slice($t['devices'], -MX_MAX_DEVICES, null, true);
    }
    mx_close($ft, $t);
    mx_out(['key' => $token]);
}

// ---- ПК: присылает приложения (и новые иконки), ждёт команды до ~4 сек ----
$tokHash = null;
$role = mx_auth($tokHash);
if ($role === null) mx_deny();

if ($a === 'agent') {
    if ($role !== 'master') mx_deny();
    $in = mx_in();
    $apps = [];
    foreach (array_slice((array)($in['apps'] ?? []), 0, 60) as $x) {
        if (!is_array($x) || !isset($x['name'])) continue;
        $apps[] = [
            'name'   => mb_substr((string)$x['name'], 0, 100),
            'title'  => mb_substr((string)($x['title'] ?? $x['name']), 0, 100),
            'volume' => max(0, min(100, (int)($x['volume'] ?? 0))),
            'muted'  => !empty($x['muted']),
        ];
    }
    $newIcons = [];
    foreach (array_slice((array)($in['icons'] ?? []), 0, 20, true) as $n => $b64) {
        if (is_string($n) && mb_strlen($n) <= 100 && is_string($b64) && preg_match('~^[A-Za-z0-9+/=]{20,40000}$~', $b64)) {
            $newIcons[$n] = $b64;
        }
    }
    if ($newIcons) {
        [$fi, $ic] = mx_open(MX_ICONS, []);
        $ic = array_slice($newIcons + $ic, 0, 80, true);
        mx_close($fi, $ic);
    }

    [$f, $s] = mx_open();
    $s['apps'] = $apps;
    $s['ts'] = time();
    if ($newIcons) $s['icon_names'] = array_values(array_unique(array_merge($s['icon_names'], array_map('strval', array_keys($newIcons)))));
    $cmds = $s['cmds'];
    $s['cmds'] = [];
    mx_close($f, $s);

    $t0 = microtime(true);
    while (!$cmds && microtime(true) - $t0 < 4 && !connection_aborted()) {
        usleep(150000);
        [$f, $s] = mx_open();
        if ($s['cmds']) {
            $cmds = $s['cmds'];
            $s['cmds'] = [];
            mx_close($f, $s);
        } else {
            mx_close($f);
        }
    }
    mx_out(['cmds' => array_values($cmds)]);
}

// ---- ПК: показать код сопряжения для телефона ----
if ($a === 'pair_new') {
    if ($role !== 'master') mx_deny();
    $now = time();
    [$f, $p] = mx_open(MX_PAIR, ['codes' => [], 'fails' => []]);
    foreach ($p['codes'] as $c => $exp) if ($exp < $now) unset($p['codes'][$c]);
    $p['codes'] = array_slice($p['codes'], -5, null, true);   // не больше 5 живых кодов
    do { $code = sprintf('%06d', random_int(0, 999999)); } while (isset($p['codes'][$code]));
    $p['codes'][$code] = $now + MX_PAIR_TTL;
    mx_close($f, $p);
    mx_out(['code' => $code, 'ttl' => MX_PAIR_TTL]);
}

// ---- Телефон: «Отвязать это устройство» ----
if ($a === 'unpair') {
    if ($tokHash !== null) {
        [$f, $t] = mx_open(MX_TOKENS, ['devices' => []]);
        unset($t['devices'][$tokHash]);
        mx_close($f, $t);
    }
    mx_out(['ok' => true]);
}

// ---- Телефон: состояние ----
if ($a === 'state') {
    [$f, $s] = mx_open();
    mx_close($f);
    $have = array_flip($s['icon_names']);
    $apps = array_map(function ($x) use ($have) { $x['icon'] = isset($have[$x['name']]); return $x; }, $s['apps']);
    $counts = [];
    foreach ($s['presets'] as $n => $lv) $counts[(string)$n] = is_array($lv) ? count($lv) : 0;
    mx_out([
        'apps'          => $apps,
        'online'        => time() - (int)$s['ts'] <= 15,
        'presets'       => array_map('strval', array_keys($s['presets'])),
        'preset_counts' => (object)$counts,
    ]);
}

// ---- Телефон: иконка одного приложения ----
if ($a === 'icon') {
    $n = (string)($_GET['n'] ?? '');
    [$fi, $ic] = mx_open(MX_ICONS, []);
    mx_close($fi);
    if (!isset($ic[$n])) mx_out(['error' => 'none'], 404);
    mx_out(['data' => $ic[$n]]);
}

// ---- Телефон: громкость / mute / медиа-кнопки ----
if ($a === 'set') {
    $in = mx_in();
    [$f, $s] = mx_open();
    if (isset($in['media'])) {
        $m = (string)$in['media'];
        if (!in_array($m, ['playpause', 'next', 'prev'], true)) { mx_close($f); mx_out(['error' => 'bad media'], 400); }
        $s['cmds'][] = ['media' => $m];
        $s['cmds'] = array_slice($s['cmds'], -300);
        mx_close($f, $s);
        mx_out(['ok' => true]);
    }
    $name = (string)($in['name'] ?? '');
    $did = false;
    if ($name !== '' && mb_strlen($name) <= 100) {
        if (isset($in['volume'])) { mx_push($s, $name, 'volume', max(0, min(100, (int)$in['volume']))); $did = true; }
        if (isset($in['muted']))  { mx_push($s, $name, 'muted', (bool)$in['muted']); $did = true; }
    }
    if (!$did) { mx_close($f); mx_out(['error' => 'bad request'], 400); }
    mx_close($f, $s);
    mx_out(['ok' => true]);
}

// ---- Пресеты ----
if ($a === 'preset_save') {
    $name = mx_pname(mx_in());
    if ($name === '') mx_out(['error' => 'bad name'], 400);
    [$f, $s] = mx_open();
    if (!isset($s['presets'][$name]) && count($s['presets']) >= 12) { mx_close($f); mx_out(['error' => 'too many'], 400); }
    $levels = [];
    foreach ($s['apps'] as $app) $levels[$app['name']] = ['volume' => $app['volume'], 'muted' => $app['muted']];
    $s['presets'][$name] = $levels;
    mx_close($f, $s);
    mx_out(['ok' => true]);
}
if ($a === 'preset_apply') {
    $name = mx_pname(mx_in());
    [$f, $s] = mx_open();
    if (!isset($s['presets'][$name])) { mx_close($f); mx_out(['error' => 'no preset'], 404); }
    foreach ($s['presets'][$name] as $n => $lv) {
        mx_push($s, (string)$n, 'volume', (int)($lv['volume'] ?? 0));
        mx_push($s, (string)$n, 'muted', !empty($lv['muted']));
    }
    mx_close($f, $s);
    mx_out(['ok' => true]);
}
if ($a === 'preset_delete') {
    $name = mx_pname(mx_in());
    [$f, $s] = mx_open();
    unset($s['presets'][$name]);
    mx_close($f, $s);
    mx_out(['ok' => true]);
}

mx_out(['error' => 'unknown'], 404);
