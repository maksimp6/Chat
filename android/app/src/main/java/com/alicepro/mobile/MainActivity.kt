package com.alicepro.mobile

import android.app.AlertDialog
import android.os.Bundle
import android.text.InputType
import android.widget.EditText
import android.widget.Toast
import android.webkit.JavascriptInterface
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.net.HttpURLConnection
import java.net.URL

class MainActivity : AppCompatActivity() {
    private lateinit var webView: WebView
    private val serverUrl = "http://127.0.0.1:5000"
    private val prefs by lazy { getSharedPreferences("alice_pro", MODE_PRIVATE) }
    private val updateManager by lazy { UpdateManager(this) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        AppLogger.initialize(this)
        AppLogger.info("MainActivity", "Activity created")

        // Android 15+ enforces edge-to-edge for apps targeting SDK 35.
        // Apply system-bar, display-cutout and IME insets to the WebView.
        WindowCompat.setDecorFitsSystemWindows(window, false)

        webView = WebView(this)
        webView.settings.javaScriptEnabled = true
        webView.settings.domStorageEnabled = true
        webView.webViewClient = object : WebViewClient() {
            override fun onPageStarted(view: WebView?, url: String?, favicon: android.graphics.Bitmap?) {
                AppLogger.debug("WebView", "Page loading", mapOf("url" to (url ?: "")))
            }

            override fun onPageFinished(view: WebView?, url: String?) {
                AppLogger.info("WebView", "Page loaded", mapOf("url" to (url ?: "")))
            }

            override fun onReceivedError(
                view: WebView?,
                request: WebResourceRequest?,
                error: WebResourceError?,
            ) {
                if (request?.isForMainFrame == true) {
                    AppLogger.error(
                        "WebView",
                        "Main frame load error: ${error?.description ?: "unknown"}",
                        context = mapOf("url" to (request.url?.toString() ?: "")),
                    )
                }
            }
        }
        webView.addJavascriptInterface(AndroidBridge(), "AliceAndroid")

        ViewCompat.setOnApplyWindowInsetsListener(webView) { view, insets ->
            val bars = insets.getInsets(
                WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout()
            )
            val ime = insets.getInsets(WindowInsetsCompat.Type.ime())

            view.updatePadding(
                left = bars.left,
                top = bars.top,
                right = bars.right,
                bottom = maxOf(bars.bottom, ime.bottom),
            )

            insets
        }

        setContentView(webView)
        ViewCompat.requestApplyInsets(webView)

        val storedKey = prefs.getString(KEY_YANDEX_API_KEY, "").orEmpty()
        if (storedKey.isBlank()) {
            AppLogger.info("Startup", "No stored API key; showing setup dialog")
            promptForApiKey()
        } else {
            AppLogger.info("Startup", "Stored API key found; starting Python server")
            startPythonServer(storedKey)
        }
    }

    override fun onResume() {
        super.onResume()
        AppLogger.debug("Lifecycle", "Activity resumed")
    }

    override fun onPause() {
        AppLogger.debug("Lifecycle", "Activity paused")
        super.onPause()
    }

    private inner class AndroidBridge {
        @JavascriptInterface
        fun openUpdater() {
            AppLogger.info("AndroidBridge", "Updater opened from web UI")
            runOnUiThread { updateManager.openPicker() }
        }

        @JavascriptInterface
        fun openDiagnostics() {
            AppLogger.info("AndroidBridge", "Diagnostics opened from web UI")
            runOnUiThread {
                startActivity(android.content.Intent(this@MainActivity, DiagnosticsActivity::class.java))
            }
        }

        @JavascriptInterface
        fun log(level: String, tag: String, message: String) {
            val mappedLevel = when (level.uppercase()) {
                "DEBUG" -> AppLogger.LogLevel.DEBUG
                "WARNING", "WARN" -> AppLogger.LogLevel.WARNING
                "ERROR" -> AppLogger.LogLevel.ERROR
                else -> AppLogger.LogLevel.INFO
            }
            AppLogger.log(mappedLevel, "Web:$tag", message)
        }
    }

    private fun promptForApiKey() {
        val input = EditText(this).apply {
            hint = "Yandex AI Studio API key"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            setSingleLine(true)
        }

        AlertDialog.Builder(this)
            .setTitle("Connect Alice Pro")
            .setMessage("Enter the Yandex AI Studio API key. It is stored in this app's private preferences and is not bundled into the APK.")
            .setView(input)
            .setCancelable(false)
            .setPositiveButton("Start") { _, _ ->
                val key = input.text.toString().trim()
                if (key.isBlank()) {
                    AppLogger.warning("Startup", "Empty API key submitted")
                    Toast.makeText(this, "API key is required", Toast.LENGTH_SHORT).show()
                    promptForApiKey()
                } else {
                    prefs.edit().putString(KEY_YANDEX_API_KEY, key).apply()
                    AppLogger.info("Startup", "API key saved; starting Python server")
                    startPythonServer(key)
                }
            }
            .setNegativeButton("Exit") {
                AppLogger.warning("Startup", "User exited API key setup")
                finish()
            }
            .show()
    }

    private fun startPythonServer(apiKey: String) {
        AppLogger.info("Python", "Starting embedded server")
        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(this))
        }

        Thread {
            try {
                Python.getInstance()
                    .getModule("android_server")
                    .callAttr("start_server", apiKey)
                AppLogger.info("Python", "Embedded server start requested")
            } catch (error: Throwable) {
                AppLogger.error("Python", "Embedded server failed to start", error)
                runOnUiThread {
                    Toast.makeText(this, "Python server failed: ${error.message}", Toast.LENGTH_LONG).show()
                }
            }
        }.start()

        waitForServer()
    }

    private fun waitForServer() {
        Thread {
            var ready = false
            repeat(60) {
                if (isServerReady()) {
                    ready = true
                    return@repeat
                }
                Thread.sleep(500)
            }

            runOnUiThread {
                if (ready) {
                    AppLogger.info("Startup", "Local server is ready")
                    webView.loadUrl(serverUrl)
                    updateManager.autoCheck()
                } else {
                    AppLogger.error("Startup", "Local server did not become ready")
                    Toast.makeText(this, "Alice Pro server did not start", Toast.LENGTH_LONG).show()
                }
            }
        }.start()
    }

    private fun isServerReady(): Boolean {
        return try {
            val connection = URL(serverUrl).openConnection() as HttpURLConnection
            connection.connectTimeout = 500
            connection.readTimeout = 1000
            connection.requestMethod = "GET"
            connection.connect()
            connection.responseCode in 200..499
        } catch (error: Exception) {
            false
        }
    }

    override fun onDestroy() {
        AppLogger.info("MainActivity", "Activity destroyed")
        webView.destroy()
        super.onDestroy()
    }

    companion object {
        private const val KEY_YANDEX_API_KEY = "yandex_api_key"
    }
}
