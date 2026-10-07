package com.alicepro.mobile

import android.annotation.SuppressLint
import android.os.Bundle
import android.view.WindowManager
import android.webkit.CookieManager
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.WindowCompat
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import org.json.JSONObject
import org.json.JSONTokener

class BrowserTakeoverActivity : AppCompatActivity() {
    private lateinit var webView: WebView
    private var controlServer: BrowserControlServer? = null

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, true)
        window.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)

        CookieManager.getInstance().setAcceptCookie(true)

        webView = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.builtInZoomControls = false
            settings.displayZoomControls = false
            settings.setSupportZoom(true)
            settings.useWideViewPort = true
            settings.loadWithOverviewMode = true
            settings.mediaPlaybackRequiresUserGesture = true
            webChromeClient = WebChromeClient()
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean {
                    val scheme = request?.url?.scheme
                    return scheme != "http" && scheme != "https"
                }
            }
        }
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true)
        setContentView(webView)

        controlServer = BrowserControlServer(this, webView).also { it.start() }

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (webView.canGoBack()) webView.goBack() else finish()
            }
        })

        val initialUrl = intent.getStringExtra(EXTRA_URL)
            ?.takeIf(::isAllowedUrl)
            ?: HOME_URL
        webView.loadUrl(initialUrl)
    }

    override fun onDestroy() {
        controlServer?.close()
        CookieManager.getInstance().flush()
        webView.destroy()
        super.onDestroy()
    }

    companion object {
        const val EXTRA_URL = "url"
        const val CONTROL_HOST = "127.0.0.1"
        const val CONTROL_PORT = 8765
        const val HOME_URL = "https://www.google.com/"

        private fun isAllowedUrl(url: String): Boolean =
            url.startsWith("https://") || url.startsWith("http://")
    }

    private class BrowserControlServer(
        private val activity: BrowserTakeoverActivity,
        private val webView: WebView,
    ) {
        @Volatile
        private var running = false
        private var serverSocket: ServerSocket? = null
        private var serverThread: Thread? = null

        fun start() {
            if (running) return
            running = true
            serverThread = Thread {
                try {
                    val socket = ServerSocket(
                        CONTROL_PORT,
                        8,
                        InetAddress.getByName(CONTROL_HOST),
                    )
                    serverSocket = socket
                    AppLogger.info(
                        "BrowserControl",
                        "Local browser API started",
                        mapOf("address" to "$CONTROL_HOST:$CONTROL_PORT"),
                    )
                    while (running) {
                        try {
                            val client = socket.accept()
                            Thread { handle(client) }.start()
                        } catch (error: Throwable) {
                            if (running) {
                                AppLogger.warning(
                                    "BrowserControl",
                                    "Accept failed: ${error.message}",
                                )
                            }
                        }
                    }
                } catch (error: Throwable) {
                    AppLogger.error("BrowserControl", "Failed to start local browser API", error)
                }
            }.apply {
                name = "alice-browser-control"
                isDaemon = true
                start()
            }
        }

        fun close() {
            running = false
            try {
                serverSocket?.close()
            } catch (_: Throwable) {
            }
            serverSocket = null
            serverThread = null
        }

        private fun handle(socket: Socket) {
            socket.use { client ->
                client.soTimeout = 5000
                val reader = BufferedReader(InputStreamReader(client.getInputStream(), Charsets.UTF_8))
                val requestLine = reader.readLine() ?: return
                var contentLength = 0
                while (true) {
                    val line = reader.readLine() ?: break
                    if (line.isEmpty()) break
                    if (line.startsWith("Content-Length:", ignoreCase = true)) {
                        contentLength = line.substringAfter(":").trim().toIntOrNull() ?: 0
                    }
                }

                val body = if (contentLength > 0) {
                    val chars = CharArray(contentLength)
                    var offset = 0
                    while (offset < contentLength) {
                        val count = reader.read(chars, offset, contentLength - offset)
                        if (count <= 0) break
                        offset += count
                    }
                    String(chars, 0, offset)
                } else {
                    ""
                }

                val parts = requestLine.split(" ")
                val method = parts.getOrNull(0).orEmpty()
                val path = parts.getOrNull(1).orEmpty()

                val response = try {
                    when {
                        method == "GET" && path == "/health" -> state()
                        method == "GET" && path == "/state" -> state()
                        method == "POST" && path == "/command" -> execute(JSONObject(body))
                        else -> JSONObject()
                            .put("ok", false)
                            .put("error", "not_found")
                    }
                } catch (error: Throwable) {
                    JSONObject()
                        .put("ok", false)
                        .put("error", error.message ?: error.javaClass.simpleName)
                }

                val payload = response.toString().toByteArray(Charsets.UTF_8)
                val out = client.getOutputStream()
                out.write(
                    (
                        "HTTP/1.1 200 OK\r\n" +
                            "Content-Type: application/json; charset=utf-8\r\n" +
                            "Content-Length: ${payload.size}\r\n" +
                            "Connection: close\r\n\r\n"
                        ).toByteArray(Charsets.UTF_8),
                )
                out.write(payload)
                out.flush()
            }
        }

        private fun state(): JSONObject {
            val latch = CountDownLatch(1)
            var url = ""
            var title = ""
            activity.runOnUiThread {
                url = webView.url.orEmpty()
                title = webView.title.orEmpty()
                latch.countDown()
            }
            if (!latch.await(2, TimeUnit.SECONDS)) {
                return JSONObject().put("ok", false).put("error", "ui_timeout")
            }
            return JSONObject()
                .put("ok", true)
                .put("url", url)
                .put("title", title)
                .put("port", CONTROL_PORT)
        }

        private fun execute(command: JSONObject): JSONObject {
            return when (val action = command.optString("action")) {
                "navigate" -> {
                    val url = command.optString("url")
                    require(isAllowedUrl(url)) { "Only http/https URLs are allowed" }
                    runUi { webView.loadUrl(url) }
                    JSONObject().put("ok", true)
                }

                "back" -> {
                    runUi { if (webView.canGoBack()) webView.goBack() }
                    JSONObject().put("ok", true)
                }

                "forward" -> {
                    runUi { if (webView.canGoForward()) webView.goForward() }
                    JSONObject().put("ok", true)
                }

                "reload" -> {
                    runUi { webView.reload() }
                    JSONObject().put("ok", true)
                }

                "text" -> js("document.body ? document.body.innerText : ''")
                "html" -> js("document.documentElement ? document.documentElement.outerHTML : ''")

                "click" -> {
                    val selector = command.getString("selector")
                    js(
                        """
                        (() => {
                          const el = document.querySelector(${JSONObject.quote(selector)});
                          if (!el) return false;
                          el.click();
                          return true;
                        })()
                        """.trimIndent(),
                    )
                }

                "type" -> {
                    val selector = command.getString("selector")
                    val text = command.optString("text")
                    js(
                        """
                        (() => {
                          const el = document.querySelector(${JSONObject.quote(selector)});
                          if (!el) return false;
                          el.focus();
                          el.value = ${JSONObject.quote(text)};
                          el.dispatchEvent(new Event('input', {bubbles: true}));
                          el.dispatchEvent(new Event('change', {bubbles: true}));
                          return true;
                        })()
                        """.trimIndent(),
                    )
                }

                "eval" -> js(command.getString("script"))
                else -> JSONObject()
                    .put("ok", false)
                    .put("error", "unknown_action")
                    .put("action", action)
            }
        }

        private fun runUi(block: () -> Unit) {
            val latch = CountDownLatch(1)
            var failure: Throwable? = null
            activity.runOnUiThread {
                try {
                    block()
                } catch (error: Throwable) {
                    failure = error
                } finally {
                    latch.countDown()
                }
            }
            if (!latch.await(2, TimeUnit.SECONDS)) error("ui_timeout")
            failure?.let { throw it }
        }

        private fun js(script: String): JSONObject {
            val latch = CountDownLatch(1)
            var rawResult = "null"
            var failure: Throwable? = null
            activity.runOnUiThread {
                try {
                    webView.evaluateJavascript(script) { result ->
                        rawResult = result ?: "null"
                        latch.countDown()
                    }
                } catch (error: Throwable) {
                    failure = error
                    latch.countDown()
                }
            }
            if (!latch.await(5, TimeUnit.SECONDS)) {
                return JSONObject().put("ok", false).put("error", "javascript_timeout")
            }
            failure?.let { throw it }

            val decoded = try {
                JSONTokener(rawResult).nextValue()
            } catch (_: Throwable) {
                rawResult
            }
            return JSONObject()
                .put("ok", true)
                .put("result", decoded)
        }
    }
}
