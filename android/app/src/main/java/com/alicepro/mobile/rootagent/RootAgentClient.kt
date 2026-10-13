package com.alicepro.mobile.rootagent

import java.io.IOException
import java.io.InputStream
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URI

/** In-app self-check against the loopback RootAgent API; the token never leaves the process. */
class RootAgentClient(private val port: Int, private val token: () -> String) {
    enum class Endpoint(val path: String, val maxBytes: Int = 4096) {
        HEALTH("/v1/health"), ROOT("/v1/root-probe"), STATUS("/v1/status"),
        DEVICE("/v1/device-info"), TREE("/v1/screen/tree", 12 * 1024 * 1024),
        CAPTURE("/v1/screen/capture", 13 * 1024 * 1024),
    }

    class Response(val code: Int?, val body: String, val error: String?) {
        // Screen content and credentials must never reach diagnostic logs via toString().
        override fun toString(): String = "Response(code=$code, error=$error)"
    }

    fun request(endpoint: Endpoint): Response = try {
        val (code, body) = get(endpoint.path)
        Response(code, body, null)
    } catch (_: ResponseTooLarge) {
        Response(null, "", "response_too_large")
    } catch (_: java.net.SocketTimeoutException) {
        Response(null, "", "timeout")
    } catch (_: IOException) {
        Response(null, "", "unreachable")
    }

    data class Report(val healthy: Boolean, val root: Boolean?, val error: String?)

    /** Screen-route self-check: only the lock flag and HTTP codes, never screen content. */
    data class ScreenReport(val keyguardLocked: Boolean?, val tree: Int?, val capture: Int?, val error: String?)

    fun selfCheck(): Report {
        return try {
            val (healthStatus, _) = get("/v1/health")
            if (healthStatus != 200) return Report(false, null, "health_http_$healthStatus")
            val (probeStatus, probeBody) = get("/v1/root-probe")
            when {
                probeStatus == 504 -> Report(true, null, "root_probe_timeout")
                probeStatus != 200 -> Report(true, null, "root_probe_http_$probeStatus")
                else -> Report(true, probeBody.contains("\"root\":true"), null)
            }
        } catch (_: IOException) {
            Report(false, null, "unreachable")
        }
    }

    fun screenCheck(): ScreenReport {
        return try {
            val (statusCode, statusBody) = get("/v1/status")
            val locked = when {
                statusCode != 200 -> null
                statusBody.contains("\"keyguard_locked\":false") -> false
                else -> true
            }
            val tree = get("/v1/screen/tree").first
            val capture = get("/v1/screen/capture").first
            ScreenReport(locked, tree, capture, if (statusCode != 200) "status_http_$statusCode" else null)
        } catch (_: IOException) {
            ScreenReport(null, null, null, "unreachable")
        }
    }

    private fun get(path: String): Pair<Int, String> {
        val connection = URI("http://${RootAgentServer.LOOPBACK}:$port$path").toURL().openConnection() as HttpURLConnection
        try {
            connection.connectTimeout = TIMEOUT_MS
            connection.readTimeout = if (path == Endpoint.TREE.path || path == Endpoint.CAPTURE.path) 28_000 else TIMEOUT_MS
            connection.instanceFollowRedirects = false
            connection.setRequestProperty("Authorization", "Bearer " + token())
            val status = connection.responseCode
            val stream = if (status < 400) connection.inputStream else connection.errorStream
            val limit = Endpoint.entries.first { it.path == path }.maxBytes
            val body = stream?.use { readBounded(it, if (status < 400) limit else 4096) } ?: ""
            return status to body
        } finally {
            connection.disconnect()
        }
    }

    private fun readBounded(input: InputStream, limit: Int): String {
        val buffer = ByteArray(4096)
        val output = ByteArrayOutputStream()
        while (true) {
            val n = input.read(buffer)
            if (n < 0) break
            if (output.size() + n > limit) throw ResponseTooLarge()
            output.write(buffer, 0, n)
        }
        return output.toString(Charsets.UTF_8.name())
    }

    private class ResponseTooLarge : IOException()

    private companion object {
        const val TIMEOUT_MS = 8_000
    }
}
