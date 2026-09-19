package com.alicepro.mobile

import android.app.AlertDialog
import android.os.Bundle
import android.text.InputType
import android.view.ViewGroup
import android.view.WindowManager
import android.webkit.JavascriptInterface
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID

class MainActivity : AppCompatActivity() {
    private lateinit var webView: WebView
    private val serverUrl = "http://127.0.0.1:5000"
    private val prefs by lazy { getSharedPreferences("alice_pro", MODE_PRIVATE) }
    private val updateManager by lazy { UpdateManager(this) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        AppLogger.initialize(this)
        AppLogger.info("MainActivity", "Activity created")

        // Use the platform's normal system-window fitting. The web UI already
        // has its own bottom safe-area padding, so edge-to-edge here causes
        // the WebView content to be laid out beneath system bars.
        androidx.core.view.WindowCompat.setDecorFitsSystemWindows(window, true)
        window.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)

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

        setContentView(webView)

        val localAgentMode = prefs.getBoolean(KEY_LOCAL_AGENT_MODE, false)
        val localAgentGateway = prefs.getString(KEY_LOCAL_AGENT_GATEWAY, "").orEmpty()
        val localAgentBootstrap = prefs.getString(KEY_LOCAL_AGENT_BOOTSTRAP, "").orEmpty()

        when {
            localAgentMode && localAgentGateway.isNotBlank() && localAgentBootstrap.isNotBlank() -> {
                AppLogger.info("Startup", "Local agent mode enabled")
                startLocalAgent(localAgentGateway, localAgentBootstrap)
            }

            localAgentMode -> {
                AppLogger.warning("Startup", "Local agent mode is incomplete; opening setup")
                promptForLocalAgent()
            }

            prefs.getString(KEY_YANDEX_API_KEY, "").orEmpty().isBlank() -> {
                AppLogger.info("Startup", "No stored API key; showing setup dialog")
                promptForApiKey()
            }

            else -> {
                AppLogger.info("Startup", "Stored API key found; starting Python server")
                startPythonServer(prefs.getString(KEY_YANDEX_API_KEY, "").orEmpty())
            }
        }
    }

    override fun onResume() {
        super.onResume()
        AppLogger.debug("Lifecycle", "Activity resumed")
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
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
                    prefs.edit()
                        .putBoolean(KEY_LOCAL_AGENT_MODE, false)
                        .putString(KEY_YANDEX_API_KEY, key)
                        .apply()
                    AppLogger.info("Startup", "API key saved; starting Python server")
                    startPythonServer(key)
                }
            }
            .setNeutralButton("Local agent") { _, _ ->
                promptForLocalAgent()
            }
            .setNegativeButton("Exit") { _, _ ->
                AppLogger.warning("Startup", "User exited API key setup")
                finish()
            }
            .show()
    }

    private fun promptForLocalAgent() {
        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 8, 48, 0)
        }

        val gatewayInput = EditText(this).apply {
            hint = "https://your-cloud-host"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            setSingleLine(true)
        }
        val bootstrapInput = EditText(this).apply {
            hint = "Bootstrap token"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            setSingleLine(true)
        }
        val defaultAgentId = prefs.getString(KEY_LOCAL_AGENT_ID, "").orEmpty()
            .ifBlank { "android-${UUID.randomUUID().toString().replace("-", "").take(12)}" }
        val agentIdInput = EditText(this).apply {
            hint = "Agent ID"
            setSingleLine(true)
            setText(defaultAgentId)
        }

        container.addView(gatewayInput, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        container.addView(bootstrapInput, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        container.addView(agentIdInput, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))

        AlertDialog.Builder(this)
            .setTitle("Run as Local Tool Agent")
            .setMessage("The phone will make outbound HTTPS requests to Cloud.ru. No router port forwarding or public Android port is required.")
            .setView(container)
            .setCancelable(false)
            .setPositiveButton("Connect") { _, _ ->
                val gateway = gatewayInput.text.toString().trim().removeSuffix("/")
                val bootstrap = bootstrapInput.text.toString().trim()
                val agentId = agentIdInput.text.toString().trim()
                if (gateway.isBlank() || bootstrap.isBlank() || agentId.isBlank()) {
                    Toast.makeText(this, "Gateway URL, bootstrap token and agent ID are required", Toast.LENGTH_LONG).show()
                    promptForLocalAgent()
                    return@setPositiveButton
                }

                prefs.edit()
                    .putBoolean(KEY_LOCAL_AGENT_MODE, true)
                    .putString(KEY_LOCAL_AGENT_GATEWAY, gateway)
                    .putString(KEY_LOCAL_AGENT_BOOTSTRAP, bootstrap)
                    .putString(KEY_LOCAL_AGENT_ID, agentId)
                    .remove(KEY_YANDEX_API_KEY)
                    .apply()

                startLocalAgent(gateway, bootstrap)
            }
            .setNegativeButton("Back") { _, _ -> promptForApiKey() }
            .show()
    }

    private fun startLocalAgent(gatewayUrl: String, bootstrapToken: String) {
        AppLogger.info("LocalAgent", "Starting outbound local agent", mapOf("gateway" to gatewayUrl))

        if (!Python.isStarted()) Python.start(AndroidPlatform(this))

        Thread {
            try {
                val agentId = prefs.getString(KEY_LOCAL_AGENT_ID, "").orEmpty()
                val result = Python.getInstance().getModule("android_server")
                    .callAttr("start_local_agent", gatewayUrl, bootstrapToken, agentId.ifBlank { null }, listOf("local.tools"))
                    .toJava(String::class.java)

                AppLogger.info("LocalAgent", "Local agent started", mapOf("result" to result))
                runOnUiThread { Toast.makeText(this, "Local Tool Agent connected", Toast.LENGTH_LONG).show() }
            } catch (error: Throwable) {
                AppLogger.error("LocalAgent", "Local agent failed", error)
                runOnUiThread { Toast.makeText(this, "Local agent failed: ${error.message}", Toast.LENGTH_LONG).show() }
            }
        }.start()
    }

    private fun startPythonServer(apiKey: String) {
        AppLogger.info("Python", "Starting embedded server")
        if (!Python.isStarted()) Python.start(AndroidPlatform(this))

        Thread {
            try {
                Python.getInstance().getModule("android_server").callAttr("start_server", apiKey)
                AppLogger.info("Python", "Embedded server start requested")
            } catch (error: Throwable) {
                AppLogger.error("Python", "Embedded server failed to start", error)
                runOnUiThread { Toast.makeText(this, "Python server failed: ${error.message}", Toast.LENGTH_LONG).show() }
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
        private const val KEY_LOCAL_AGENT_MODE = "local_agent_mode"
        private const val KEY_LOCAL_AGENT_GATEWAY = "local_agent_gateway"
        private const val KEY_LOCAL_AGENT_BOOTSTRAP = "local_agent_bootstrap"
        private const val KEY_LOCAL_AGENT_ID = "local_agent_id"
    }
}
