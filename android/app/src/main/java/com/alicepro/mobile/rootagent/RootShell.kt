package com.alicepro.mobile.rootagent

import java.io.ByteArrayOutputStream
import java.io.IOException
import java.util.concurrent.TimeUnit

/** Outcome of one bounded process run. [exitCode] is null when it timed out or never started. */
data class RootShellResult(
    val exitCode: Int?,
    val output: String,
    val timedOut: Boolean,
    val truncated: Boolean,
    val failedToStart: Boolean = false,
)

/**
 * Runs one argv (never a shell string) with a hard timeout and a bounded,
 * merged stdout/stderr buffer. The process is destroyed on timeout. Thread-safe:
 * each call owns its process and reader thread.
 */
class RootShell(
    private val timeoutMs: Long,
    private val maxOutputBytes: Int,
    private val starter: (List<String>) -> Process = { argv ->
        ProcessBuilder(argv).redirectErrorStream(true).start()
    },
) {
    init {
        require(timeoutMs > 0) { "timeout must be positive" }
        require(maxOutputBytes > 0) { "output bound must be positive" }
    }

    fun run(argv: List<String>): RootShellResult {
        require(argv.isNotEmpty()) { "argv must not be empty" }
        val process = try {
            starter(argv)
        } catch (_: IOException) {
            return RootShellResult(null, "", timedOut = false, truncated = false, failedToStart = true)
        }
        process.outputStream.close()
        val buffer = ByteArrayOutputStream()
        var truncated = false
        val reader = Thread {
            val chunk = ByteArray(4096)
            try {
                process.inputStream.use { input ->
                    while (true) {
                        val n = input.read(chunk)
                        if (n < 0) break
                        synchronized(buffer) {
                            val room = maxOutputBytes - buffer.size()
                            if (room > 0) buffer.write(chunk, 0, minOf(n, room))
                            if (n > room) truncated = true
                        }
                    }
                }
            } catch (_: IOException) {
                // Stream closed by destroy() after a timeout.
            }
        }
        reader.isDaemon = true
        reader.start()
        val finished = process.waitFor(timeoutMs, TimeUnit.MILLISECONDS)
        if (!finished) {
            process.destroyForcibly()
            process.waitFor(1, TimeUnit.SECONDS)
        }
        reader.join(1_000)
        val output = synchronized(buffer) { buffer.toString(Charsets.UTF_8.name()) }
        val wasTruncated = synchronized(buffer) { truncated }
        return if (finished) {
            RootShellResult(process.exitValue(), output, timedOut = false, truncated = wasTruncated)
        } else {
            RootShellResult(null, output, timedOut = true, truncated = wasTruncated)
        }
    }
}
