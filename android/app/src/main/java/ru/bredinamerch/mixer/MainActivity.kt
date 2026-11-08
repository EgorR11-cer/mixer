package ru.bredinamerch.mixer

import android.annotation.SuppressLint
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.View
import android.view.WindowManager
import android.webkit.JavascriptInterface
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.webkit.WebViewAssetLoader
import androidx.webkit.WebViewClientCompat
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * Приложение — это окно с интерфейсом микшера, который лежит внутри APK (assets),
 * а не открывается с сайта. С сервером (mixer.php) он общается по HTTPS напрямую.
 * Нативная часть: сканер QR-кода, номер версии и проверка обновлений (через мост window.Android).
 */
class MainActivity : AppCompatActivity() {

    private lateinit var web: WebView

    /** Результат сканера QR отдаём интерфейсу: window.onQrResult("текст из кода"). */
    private val scanLauncher = registerForActivityResult(ScanContract()) { result ->
        val text = result.contents ?: return@registerForActivityResult
        web.evaluateJavascript("window.onQrResult&&window.onQrResult(${JSONObject.quote(text)})", null)
    }

    /** То, что интерфейс может вызывать из JavaScript. Страница грузится только из assets приложения. */
    inner class Bridge {
        @JavascriptInterface
        fun scanQr() {
            runOnUiThread {
                scanLauncher.launch(
                    ScanOptions()
                        .setDesiredBarcodeFormats(ScanOptions.QR_CODE)
                        .setPrompt("Наведите камеру на QR-код на экране компьютера")
                        .setBeepEnabled(false)
                        .setOrientationLocked(false)
                )
            }
        }

        /** Версия установленного приложения, например «1.123». */
        @JavascriptInterface
        fun getVersion(): String = try {
            packageManager.getPackageInfo(packageName, 0).versionName ?: "?"
        } catch (_: Exception) {
            "?"
        }

        /** Не давать экрану гаснуть, пока приложение открыто (настройка «Экран не гаснет»). */
        @JavascriptInterface
        fun setKeepAwake(on: Boolean) {
            runOnUiThread {
                if (on) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            }
        }

        @JavascriptInterface
        fun checkUpdate() {
            this@MainActivity.checkUpdate(manual = true)
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val assets = WebViewAssetLoader.Builder()
            .addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(this))
            .build()

        web = WebView(this).apply {
            setBackgroundColor(Color.parseColor("#0B0D12"))
            overScrollMode = View.OVER_SCROLL_NEVER
            settings.apply {
                javaScriptEnabled = true
                domStorageEnabled = true            // тут хранится ключ устройства и тема (localStorage)
                allowFileAccess = false
                allowContentAccess = false
                cacheMode = WebSettings.LOAD_DEFAULT
                mediaPlaybackRequiresUserGesture = true
            }
            addJavascriptInterface(Bridge(), "Android")
            webViewClient = object : WebViewClientCompat() {
                override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest) =
                    assets.shouldInterceptRequest(request.url)

                // Всё, что не наш интерфейс, открываем в браузере, а не внутри приложения
                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                    if (request.url.host == WebViewAssetLoader.DEFAULT_DOMAIN) return false
                    startActivity(Intent(Intent.ACTION_VIEW, request.url))
                    return true
                }
            }
        }
        setContentView(web)

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() { moveTaskToBack(true) }
        })

        if (savedInstanceState != null) web.restoreState(savedInstanceState)
        else web.loadUrl("https://${WebViewAssetLoader.DEFAULT_DOMAIN}/assets/index.html")

        if (savedInstanceState == null) checkUpdate(manual = false)
    }

    private fun toastUi(text: String) = runOnUiThread { Toast.makeText(this, text, Toast.LENGTH_SHORT).show() }

    /**
     * Смотрим последний релиз на GitHub; если он новее установленной версии — предлагаем скачать APK.
     * manual = true — проверка по кнопке в настройках: ответ показываем всегда, «Позже» не учитываем.
     */
    private fun checkUpdate(manual: Boolean) {
        val prefs = getSharedPreferences("mixer", MODE_PRIVATE)
        Thread {
            try {
                val conn = (URL("https://api.github.com/repos/EgorR11-cer/mixer/releases/latest").openConnection() as HttpURLConnection).apply {
                    connectTimeout = 8000
                    readTimeout = 8000
                    setRequestProperty("User-Agent", "MixerApp")
                    setRequestProperty("Accept", "application/vnd.github+json")
                }
                if (conn.responseCode != 200) {
                    if (manual) toastUi("Не удалось проверить обновления")
                    return@Thread
                }
                val json = JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
                val latest = json.optString("tag_name").removePrefix("v").toIntOrNull() ?: return@Thread
                val assets = json.optJSONArray("assets") ?: return@Thread
                val apk = (0 until assets.length()).map { assets.getJSONObject(it) }
                    .firstOrNull { it.optString("name") == "Mixer.apk" }
                    ?.optString("browser_download_url")
                if (apk.isNullOrEmpty()) return@Thread
                val pi = packageManager.getPackageInfo(packageName, 0)
                @Suppress("DEPRECATION")
                val current = if (Build.VERSION.SDK_INT >= 28) pi.longVersionCode else pi.versionCode.toLong()
                if (latest <= current) {
                    if (manual) toastUi("Установлена последняя версия (${pi.versionName})")
                    return@Thread
                }
                if (!manual && System.currentTimeMillis() < prefs.getLong("skip_$latest", 0L)) return@Thread
                val title = json.optString("name").ifEmpty { "1.$latest" }
                runOnUiThread {
                    if (isFinishing || isDestroyed) return@runOnUiThread
                    androidx.appcompat.app.AlertDialog.Builder(this)
                        .setTitle("Доступно обновление")
                        .setMessage("Вышла новая версия: $title (у вас ${pi.versionName}). Скачать и установить?")
                        .setPositiveButton("Обновить") { _, _ -> startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(apk))) }
                        .setNegativeButton("Позже") { _, _ ->
                            prefs.edit().putLong("skip_$latest", System.currentTimeMillis() + 24L * 3600 * 1000).apply()
                        }
                        .show()
                }
            } catch (_: Exception) {
                // нет сети или GitHub недоступен — тихо пропускаем; по кнопке скажем об этом
                if (manual) toastUi("Не удалось проверить обновления")
            }
        }.start()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        web.saveState(outState)
    }

    override fun onResume() { super.onResume(); web.onResume() }
    override fun onPause() { web.onPause(); super.onPause() }
    override fun onDestroy() { web.destroy(); super.onDestroy() }
}
