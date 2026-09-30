package com.termux.app.alice;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.IBinder;

import androidx.core.app.NotificationCompat;

import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.Base64;
import java.util.Locale;

public class AliceRootAgentService extends Service {
    private static final String CHANNEL_ID = "alice_root_agent";
    private static final int NOTIFICATION_ID = 4242;
    private static final int PORT = 8765;
    private static final int MAX_BODY = 64 * 1024;
    private volatile boolean running;
    private ServerSocket serverSocket;
    private String token;

    @Override
    public void onCreate() {
        super.onCreate();
        createChannel();
        startForeground(
            NOTIFICATION_ID,
            new NotificationCompat.Builder(this, CHANNEL_ID)
                .setContentTitle("Alice Root Agent")
                .setContentText("Local root bridge active on 127.0.0.1:" + PORT)
                .setSmallIcon(android.R.drawable.stat_sys_upload_done)
                .setOngoing(true)
                .build()
        );
        token = loadOrCreateToken();
        running = true;
        new Thread(this::serveLoop, "alice-root-agent").start();
    }

    @Override
    public void onDestroy() {
        running = false;
        try {
            if (serverSocket != null) serverSocket.close();
        } catch (Exception ignored) {}
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID,
                "Alice Root Agent",
                NotificationManager.IMPORTANCE_LOW
            );
            getSystemService(NotificationManager.class).createNotificationChannel(channel);
        }
    }

    private String loadOrCreateToken() {
        SharedPreferences prefs = getSharedPreferences("alice_root_agent", MODE_PRIVATE);
        String existing = prefs.getString("token", "");
        if (existing != null && !existing.isEmpty()) return existing;

        byte[] bytes = new byte[32];
        new SecureRandom().nextBytes(bytes);
        String created = Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
        prefs.edit().putString("token", created).apply();

        try {
            File dir = new File(getFilesDir(), "alice-root-agent");
            if (!dir.exists()) dir.mkdirs();
            File tokenFile = new File(dir, "token");
            try (FileOutputStream output = new FileOutputStream(tokenFile)) {
                output.write(created.getBytes(StandardCharsets.UTF_8));
            }
            tokenFile.setReadable(false, false);
            tokenFile.setReadable(true, true);
            tokenFile.setWritable(false, false);
            tokenFile.setWritable(true, true);
        } catch (Exception ignored) {}

        return created;
    }

    private void serveLoop() {
        try {
            serverSocket = new ServerSocket(PORT, 4, InetAddress.getByName("127.0.0.1"));
            while (running) {
                try (Socket socket = serverSocket.accept()) {
                    socket.setSoTimeout(10000);
                    handle(socket);
                } catch (Exception ignored) {}
            }
        } catch (Exception ignored) {}
    }

    private void handle(Socket socket) throws Exception {
        BufferedInputStream input = new BufferedInputStream(socket.getInputStream());
        OutputStream output = new BufferedOutputStream(socket.getOutputStream());

        String requestLine = readLine(input);
        if (requestLine == null || requestLine.isEmpty()) return;
        String[] first = requestLine.split(" ");
        if (first.length < 2) return;
        String method = first[0].toUpperCase(Locale.ROOT);
        String path = first[1];

        int contentLength = 0;
        String authorization = "";
        String line;
        while ((line = readLine(input)) != null && !line.isEmpty()) {
            int colon = line.indexOf(':');
            if (colon <= 0) continue;
            String name = line.substring(0, colon).trim().toLowerCase(Locale.ROOT);
            String value = line.substring(colon + 1).trim();
            if ("content-length".equals(name)) {
                contentLength = Math.min(Integer.parseInt(value), MAX_BODY);
            } else if ("authorization".equals(name)) {
                authorization = value;
            }
        }

        byte[] body = new byte[contentLength];
        int offset = 0;
        while (offset < contentLength) {
            int read = input.read(body, offset, contentLength - offset);
            if (read < 0) break;
            offset += read;
        }

        if ("/health".equals(path)) {
            sendJson(output, 200, new JSONObject()
                .put("status", "ok")
                .put("root", canRoot())
                .put("port", PORT)
            );
            return;
        }

        if (!("Bearer " + token).equals(authorization)) {
            sendJson(output, 401, new JSONObject().put("error", "unauthorized"));
            return;
        }

        if ("POST".equals(method) && "/exec".equals(path)) {
            String command = new String(body, 0, offset, StandardCharsets.UTF_8).trim();
            if (command.isEmpty()) {
                sendJson(output, 400, new JSONObject().put("error", "empty_command"));
                return;
            }
            ExecResult result = execRoot(command, false);
            sendJson(output, 200, result.toJson());
            return;
        }

        if ("GET".equals(method) && "/packages".equals(path)) {
            sendJson(output, 200, execRoot("cmd package list packages --user 0 -3", false).toJson());
            return;
        }

        if ("GET".equals(method) && "/processes".equals(path)) {
            sendJson(output, 200, execRoot("ps -A", false).toJson());
            return;
        }

        if ("GET".equals(method) && "/screenshot".equals(path)) {
            ExecResult result = execRoot("screencap -p", true);
            if (result.exitCode != 0) {
                sendJson(output, 500, result.toJson());
            } else {
                sendBytes(output, 200, "image/png", result.raw);
            }
            return;
        }

        if ("POST".equals(method) && "/tap".equals(path)) {
            JSONObject json = new JSONObject(new String(body, 0, offset, StandardCharsets.UTF_8));
            int x = json.getInt("x");
            int y = json.getInt("y");
            sendJson(output, 200, execRoot("input tap " + x + " " + y, false).toJson());
            return;
        }

        if ("POST".equals(method) && "/swipe".equals(path)) {
            JSONObject json = new JSONObject(new String(body, 0, offset, StandardCharsets.UTF_8));
            int x1 = json.getInt("x1");
            int y1 = json.getInt("y1");
            int x2 = json.getInt("x2");
            int y2 = json.getInt("y2");
            int duration = Math.max(1, Math.min(json.optInt("duration_ms", 250), 10000));
            String command = "input swipe " + x1 + " " + y1 + " " + x2 + " " + y2 + " " + duration;
            sendJson(output, 200, execRoot(command, false).toJson());
            return;
        }

        if ("POST".equals(method) && "/text".equals(path)) {
            JSONObject json = new JSONObject(new String(body, 0, offset, StandardCharsets.UTF_8));
            String value = json.getString("text");
            String safe = value.replace("%", "%25").replace(" ", "%s");
            safe = safe.replace("\\", "\\\\").replace(""", "\\"");
            sendJson(output, 200, execRoot("input text \"" + safe + "\"", false).toJson());
            return;
        }

        sendJson(output, 404, new JSONObject().put("error", "not_found"));
    }

    private boolean canRoot() {
        try {
            return execRoot("id", false).exitCode == 0;
        } catch (Exception error) {
            return false;
        }
    }

    private ExecResult execRoot(String command, boolean binary) throws Exception {
        Process process = new ProcessBuilder("su", "-c", command).redirectErrorStream(false).start();
        ByteArrayOutputStream stdout = new ByteArrayOutputStream();
        ByteArrayOutputStream stderr = new ByteArrayOutputStream();
        Thread out = pump(process.getInputStream(), stdout);
        Thread err = pump(process.getErrorStream(), stderr);
        int code = process.waitFor();
        out.join(3000);
        err.join(3000);
        return new ExecResult(code, stdout.toByteArray(), stderr.toByteArray(), binary);
    }

    private Thread pump(java.io.InputStream input, ByteArrayOutputStream output) {
        Thread thread = new Thread(() -> {
            byte[] buffer = new byte[8192];
            try {
                int read;
                while ((read = input.read(buffer)) >= 0) output.write(buffer, 0, read);
            } catch (Exception ignored) {}
        });
        thread.start();
        return thread;
    }

    private String readLine(BufferedInputStream input) throws Exception {
        ByteArrayOutputStream line = new ByteArrayOutputStream();
        int previous = -1;
        int current;
        while ((current = input.read()) >= 0) {
            if (previous == '\r' && current == '\n') {
                byte[] raw = line.toByteArray();
                int length = raw.length > 0 && raw[raw.length - 1] == '\r' ? raw.length - 1 : raw.length;
                return new String(raw, 0, length, StandardCharsets.UTF_8);
            }
            line.write(current);
            previous = current;
            if (line.size() > 8192) throw new IllegalArgumentException("header too large");
        }
        return line.size() == 0 ? null : line.toString("UTF-8");
    }

    private void sendJson(OutputStream output, int status, JSONObject json) throws Exception {
        sendBytes(output, status, "application/json; charset=utf-8", json.toString().getBytes(StandardCharsets.UTF_8));
    }

    private void sendBytes(OutputStream output, int status, String contentType, byte[] body) throws Exception {
        String reason = status == 200 ? "OK" : status == 400 ? "Bad Request" :
            status == 401 ? "Unauthorized" : status == 404 ? "Not Found" : "Error";
        String headers = "HTTP/1.1 " + status + " " + reason + "\r\n" +
            "Content-Type: " + contentType + "\r\n" +
            "Content-Length: " + body.length + "\r\n" +
            "Connection: close\r\n" +
            "Cache-Control: no-store\r\n\r\n";
        output.write(headers.getBytes(StandardCharsets.UTF_8));
        output.write(body);
        output.flush();
    }

    private static final class ExecResult {
        final int exitCode;
        final byte[] raw;
        final byte[] stderr;
        final boolean binary;

        ExecResult(int exitCode, byte[] raw, byte[] stderr, boolean binary) {
            this.exitCode = exitCode;
            this.raw = raw;
            this.stderr = stderr;
            this.binary = binary;
        }

        JSONObject toJson() throws Exception {
            JSONObject json = new JSONObject().put("exit_code", exitCode);
            if (!binary) json.put("stdout", new String(raw, StandardCharsets.UTF_8));
            json.put("stderr", new String(stderr, StandardCharsets.UTF_8));
            return json;
        }
    }
}
