package com.alicepro.mobile

import android.app.AlertDialog
import android.os.Bundle
import android.text.InputType
import android.widget.EditText
import android.widget.Toast
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.appcompat.app.AppCompatActivity
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.net.HttpURLConnection
import java.net.URL

class MainActivity : AppCompatActivity() {
    private lateinit var webView: WebView
    private val serverUrl = "http://127.0.0.1:5000"
    private val prefs by lazy { getSharedPreferences("alice_pro", MODE_PRIVATE) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        webView = WebView(this)
        webView.settings.javaScriptEnabled = true
        webView.settings.domStorageEnabled = true
        webView.webViewClient = WebViewClient()
        setContentView(webView)

        val storedKey = prefs.getString(KEY_YANDEX_API_KEY, "").orEmpty()
        if (storedKey.isBlank()) {
            promptForApiKey()
        } else {
            startPythonServer(storedKey)
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
                    Toast.makeText(this, "API key is required", Toast.LENGTH_SHORT).show()
                    promptForApiKey()
                } else {
                    prefs.edit().putString(KEY_YANDEX_API_KEY, key).apply()
                    startPythonServer(key)
                }
            }
            .setNegativeButton("Exit") { _, _ -> finish() }
            .show()
    }

    private fun startPythonServer(apiKey: String) {
        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(this))
        }

        Thread {
            try {
                Python.getInstance()
                    .getModule("android_server")
                    .callAttr("start_server", apiKey)
            } catch (error: Throwable) {
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
                    webView.loadUrl(serverUrl)
                } else {
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
        } catch (_: Exception) {
            false
        }
    }

    override fun onDestroy() {
        webView.destroy()
        super.onDestroy()
    }

    companion object {
        private const val KEY_YANDEX_API_KEY = "yandex_api_key"
    }
}
