package com.alicepro.mobile.rootagent

import java.io.IOException
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URI

/** In-app self-check against the loopback RootAgent API; the token never leaves the process. */
class RootAgentClient(private val port: Int, private val token: () -> String) {
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
            connection.readTimeout = TIMEOUT_MS
            connection.instanceFollowRedirects = false
            connection.setRequestProperty("Authorization", "Bearer " + token())
            val status = connection.responseCode
            val stream = if (status < 400) connection.inputStream else connection.errorStream
            val body = stream?.use { readBounded(it) } ?: ""
            return status to body
        } finally {
            connection.disconnect()
        }
    }

    private fun readBounded(input: InputStream): String {
        val buffer = ByteArray(MAX_BODY)
        var total = 0
        while (total < MAX_BODY) {
            val n = input.read(buffer, total, MAX_BODY - total)
            if (n < 0) break
            total += n
        }
        return String(buffer, 0, total, Charsets.UTF_8)
    }

    private companion object {
        const val TIMEOUT_MS = 8_000
        const val MAX_BODY = 4096
    }
}
