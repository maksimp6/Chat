package com.alicepro.mobile;

import android.app.Activity;
import android.os.Bundle;
import android.webkit.CookieManager;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import org.json.JSONObject;
import org.json.JSONTokener;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

public final class BrowserActivity extends Activity {
    public static final String CONTROL_HOST = "127.0.0.1";
    public static final int CONTROL_PORT = 8765;
    public static final String HOME_URL = "https://www.google.com/";

    private WebView webView;
    private BrowserControlServer controlServer;

    @Override
    public void onCreate(Bundle state) {
        super.onCreate(state);

        CookieManager.getInstance().setAcceptCookie(true);

        webView = new WebView(this);
        webView.getSettings().setJavaScriptEnabled(true);
        webView.getSettings().setDomStorageEnabled(true);
        webView.getSettings().setSupportZoom(true);
        webView.getSettings().setUseWideViewPort(true);
        webView.getSettings().setLoadWithOverviewMode(true);
        webView.setWebChromeClient(new WebChromeClient());
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                String scheme = request.getUrl().getScheme();
                return !"http".equals(scheme) && !"https".equals(scheme);
            }
        });
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true);
        setContentView(webView);

        controlServer = new BrowserControlServer(this, webView);
        controlServer.start();

        webView.loadUrl(HOME_URL);
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onDestroy() {
        if (controlServer != null) controlServer.close();
        CookieManager.getInstance().flush();
        if (webView != null) webView.destroy();
        super.onDestroy();
    }

    private static boolean isAllowedUrl(String url) {
        return url != null && (url.startsWith("https://") || url.startsWith("http://"));
    }

    private static final class BrowserControlServer {
        private final BrowserActivity activity;
        private final WebView webView;
        private volatile boolean running;
        private ServerSocket serverSocket;

        BrowserControlServer(BrowserActivity activity, WebView webView) {
            this.activity = activity;
            this.webView = webView;
        }

        void start() {
            if (running) return;
            running = true;
            Thread thread = new Thread(() -> {
                try {
                    serverSocket = new ServerSocket(
                        CONTROL_PORT,
                        8,
                        InetAddress.getByName(CONTROL_HOST)
                    );
                    while (running) {
                        try {
                            Socket client = serverSocket.accept();
                            new Thread(() -> handle(client), "alice-browser-request").start();
                        } catch (Throwable error) {
                            if (!running) return;
                        }
                    }
                } catch (Throwable error) {
                    running = false;
                }
            }, "alice-browser-control");
            thread.setDaemon(true);
            thread.start();
        }

        void close() {
            running = false;
            try {
                if (serverSocket != null) serverSocket.close();
            } catch (Throwable ignored) {
            }
        }

        private void handle(Socket socket) {
            try (Socket client = socket) {
                client.setSoTimeout(5000);
                BufferedReader reader = new BufferedReader(
                    new InputStreamReader(client.getInputStream(), StandardCharsets.UTF_8)
                );
                String requestLine = reader.readLine();
                if (requestLine == null) return;

                int contentLength = 0;
                while (true) {
                    String line = reader.readLine();
                    if (line == null || line.isEmpty()) break;
                    if (line.toLowerCase().startsWith("content-length:")) {
                        contentLength = Integer.parseInt(line.substring(line.indexOf(':') + 1).trim());
                    }
                }

                char[] chars = new char[Math.max(0, contentLength)];
                int offset = 0;
                while (offset < contentLength) {
                    int count = reader.read(chars, offset, contentLength - offset);
                    if (count <= 0) break;
                    offset += count;
                }
                String body = new String(chars, 0, offset);

                String[] parts = requestLine.split(" ");
                String method = parts.length > 0 ? parts[0] : "";
                String path = parts.length > 1 ? parts[1] : "";

                JSONObject response;
                try {
                    if ("GET".equals(method) && ("/health".equals(path) || "/state".equals(path))) {
                        response = state();
                    } else if ("POST".equals(method) && "/command".equals(path)) {
                        response = execute(new JSONObject(body));
                    } else {
                        response = new JSONObject().put("ok", false).put("error", "not_found");
                    }
                } catch (Throwable error) {
                    response = new JSONObject()
                        .put("ok", false)
                        .put("error", error.getMessage() == null ? error.getClass().getSimpleName() : error.getMessage());
                }

                byte[] payload = response.toString().getBytes(StandardCharsets.UTF_8);
                String headers =
                    "HTTP/1.1 200 OK\r\n" +
                    "Content-Type: application/json; charset=utf-8\r\n" +
                    "Content-Length: " + payload.length + "\r\n" +
                    "Connection: close\r\n\r\n";
                client.getOutputStream().write(headers.getBytes(StandardCharsets.UTF_8));
                client.getOutputStream().write(payload);
                client.getOutputStream().flush();
            } catch (Throwable ignored) {
            }
        }

        private JSONObject state() throws Exception {
            CountDownLatch latch = new CountDownLatch(1);
            String[] values = new String[2];
            activity.runOnUiThread(() -> {
                values[0] = webView.getUrl() == null ? "" : webView.getUrl();
                values[1] = webView.getTitle() == null ? "" : webView.getTitle();
                latch.countDown();
            });
            if (!latch.await(2, TimeUnit.SECONDS)) {
                return new JSONObject().put("ok", false).put("error", "ui_timeout");
            }
            return new JSONObject()
                .put("ok", true)
                .put("url", values[0])
                .put("title", values[1])
                .put("port", CONTROL_PORT);
        }

        private JSONObject execute(JSONObject command) throws Exception {
            String action = command.optString("action");
            switch (action) {
                case "navigate": {
                    String url = command.optString("url");
                    if (!isAllowedUrl(url)) throw new IllegalArgumentException("Only http/https URLs are allowed");
                    runUi(() -> webView.loadUrl(url));
                    return new JSONObject().put("ok", true);
                }
                case "back":
                    runUi(() -> { if (webView.canGoBack()) webView.goBack(); });
                    return new JSONObject().put("ok", true);
                case "forward":
                    runUi(() -> { if (webView.canGoForward()) webView.goForward(); });
                    return new JSONObject().put("ok", true);
                case "reload":
                    runUi(webView::reload);
                    return new JSONObject().put("ok", true);
                case "text":
                    return js("document.body ? document.body.innerText : ''");
                case "html":
                    return js("document.documentElement ? document.documentElement.outerHTML : ''");
                case "click": {
                    String selector = JSONObject.quote(command.getString("selector"));
                    return js("(() => { const el = document.querySelector(" + selector + "); if (!el) return false; el.click(); return true; })()");
                }
                case "type": {
                    String selector = JSONObject.quote(command.getString("selector"));
                    String text = JSONObject.quote(command.optString("text"));
                    return js("(() => { const el = document.querySelector(" + selector + "); if (!el) return false; el.focus(); el.value = " + text + "; el.dispatchEvent(new Event('input', {bubbles:true})); el.dispatchEvent(new Event('change', {bubbles:true})); return true; })()");
                }
                case "eval":
                    return js(command.getString("script"));
                default:
                    return new JSONObject().put("ok", false).put("error", "unknown_action").put("action", action);
            }
        }

        private void runUi(Runnable runnable) throws Exception {
            CountDownLatch latch = new CountDownLatch(1);
            Throwable[] failure = new Throwable[1];
            activity.runOnUiThread(() -> {
                try {
                    runnable.run();
                } catch (Throwable error) {
                    failure[0] = error;
                } finally {
                    latch.countDown();
                }
            });
            if (!latch.await(2, TimeUnit.SECONDS)) throw new IllegalStateException("ui_timeout");
            if (failure[0] != null) throw new RuntimeException(failure[0]);
        }

        private JSONObject js(String script) throws Exception {
            CountDownLatch latch = new CountDownLatch(1);
            String[] result = new String[]{"null"};
            activity.runOnUiThread(() -> webView.evaluateJavascript(script, value -> {
                result[0] = value == null ? "null" : value;
                latch.countDown();
            }));
            if (!latch.await(5, TimeUnit.SECONDS)) {
                return new JSONObject().put("ok", false).put("error", "javascript_timeout");
            }

            Object decoded;
            try {
                decoded = new JSONTokener(result[0]).nextValue();
            } catch (Throwable ignored) {
                decoded = result[0];
            }
            return new JSONObject().put("ok", true).put("result", decoded);
        }
    }
}
