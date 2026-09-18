package com.alicepro.mobile

import android.content.ClipData
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log
import androidx.core.content.FileProvider
import org.json.JSONObject
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.text.SimpleDateFormat
import java.util.ArrayDeque
import java.util.Date
import java.util.Locale

object AppLogger {
    enum class LogLevel(val label: String, val priority: Int) {
        DEBUG("DEBUG", 10),
        INFO("INFO", 20),
        WARNING("WARNING", 30),
        ERROR("ERROR", 40);

        companion object {
            fun fromLabel(value: String): LogLevel =
                entries.firstOrNull { it.label.equals(value, ignoreCase = true) } ?: INFO
        }
    }

    data class LogEntry(
        val timestamp: Long,
        val level: LogLevel,
        val tag: String,
        val message: String,
        val context: Map<String, String> = emptyMap(),
    )

    private const val MAX_FILE_BYTES = 256 * 1024L
    private const val MAX_MEMORY_ENTRIES = 500
    private const val LOG_DIR = "diagnostics"
    private const val LOG_FILE = "app.log"
    private const val ROTATED_FILE = "app.log.1"

    private val lock = Any()
    private var appContext: Context? = null
    private val recentEntries = ArrayDeque<LogEntry>()

    fun initialize(context: Context) {
        synchronized(lock) {
            appContext = context.applicationContext
            ensureDirectory()
        }
        log(LogLevel.INFO, "AppLogger", "Logger initialized", mapOf("debug" to BuildConfig.DEBUG.toString()))
    }

    fun debug(tag: String, message: String, context: Map<String, String> = emptyMap()) =
        log(LogLevel.DEBUG, tag, message, context)

    fun info(tag: String, message: String, context: Map<String, String> = emptyMap()) =
        log(LogLevel.INFO, tag, message, context)

    fun warning(tag: String, message: String, context: Map<String, String> = emptyMap()) =
        log(LogLevel.WARNING, tag, message, context)

    fun error(
        tag: String,
        message: String,
        throwable: Throwable? = null,
        context: Map<String, String> = emptyMap(),
    ) {
        val details = if (throwable == null) message
        else "${message}\n${Log.getStackTraceString(throwable)}"
        log(LogLevel.ERROR, tag, details, context)
    }

    fun log(
        level: LogLevel,
        tag: String,
        message: String,
        context: Map<String, String> = emptyMap(),
    ) {
        if (!BuildConfig.DEBUG && level.priority < LogLevel.WARNING.priority) return

        val safeTag = LogRedactor.redact(tag).take(80)
        val safeMessage = LogRedactor.redact(message).take(12_000)
        val safeContext = context.mapValues { LogRedactor.redact(it.value).take(500) }
        val entry = LogEntry(System.currentTimeMillis(), level, safeTag, safeMessage, safeContext)

        synchronized(lock) {
            remember(entry)
            append(entry)
        }

        when (level) {
            LogLevel.DEBUG -> Log.d(safeTag, safeMessage)
            LogLevel.INFO -> Log.i(safeTag, safeMessage)
            LogLevel.WARNING -> Log.w(safeTag, safeMessage)
            LogLevel.ERROR -> Log.e(safeTag, safeMessage)
        }
    }

    fun readEntries(): List<LogEntry> = synchronized(lock) {
        val loaded = mutableListOf<LogEntry>()
        readLogFile(File(directory(), ROTATED_FILE), loaded)
        readLogFile(File(directory(), LOG_FILE), loaded)
        if (loaded.isNotEmpty()) {
            recentEntries.clear()
            loaded.takeLast(MAX_MEMORY_ENTRIES).forEach { recentEntries.addLast(it) }
        }
        loaded.takeLast(2000)
    }

    fun clear() {
        synchronized(lock) {
            File(directory(), LOG_FILE).delete()
            File(directory(), ROTATED_FILE).delete()
            recentEntries.clear()
        }
    }

    fun createShareIntent(context: Context): Intent {
        val exportDir = File(context.cacheDir, LOG_DIR).apply { mkdirs() }
        val export = File(exportDir, "alice-pro-diagnostics-${System.currentTimeMillis()}.log")
        export.bufferedWriter().use { writer ->
            readEntries().forEach { entry ->
                writer.appendLine(toJson(entry).toString())
            }
        }

        val uri = FileProvider.getUriForFile(
            context,
            "${context.packageName}.fileprovider",
            export,
        )
        return Intent(Intent.ACTION_SEND).apply {
            type = "text/plain"
            putExtra(Intent.EXTRA_SUBJECT, "Alice Pro diagnostics")
            putExtra(Intent.EXTRA_STREAM, uri)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            clipData = ClipData.newRawUri("Alice Pro diagnostics", uri)
        }
    }

    fun formatTimestamp(timestamp: Long): String =
        SimpleDateFormat("yyyy-MM-dd HH:mm:ss.SSS", Locale.US).format(Date(timestamp))

    private fun remember(entry: LogEntry) {
        recentEntries.addLast(entry)
        while (recentEntries.size > MAX_MEMORY_ENTRIES) recentEntries.removeFirst()
    }

    private fun append(entry: LogEntry) {
        val directory = directory().apply { mkdirs() }
        val file = File(directory, LOG_FILE)
        if (file.exists() && file.length() >= MAX_FILE_BYTES) {
            val rotated = File(directory, ROTATED_FILE)
            rotated.delete()
            if (!file.renameTo(rotated)) {
                FileInputStream(file).use { input ->
                    FileOutputStream(rotated).use { output -> input.copyTo(output) }
                }
                file.delete()
            }
        }
        file.appendText(toJson(entry).toString() + "\n")
    }

    private fun readLogFile(file: File, target: MutableList<LogEntry>) {
        if (!file.exists()) return
        file.useLines { lines ->
            lines.forEach { line ->
                try {
                    val json = JSONObject(line)
                    val context = mutableMapOf<String, String>()
                    val contextJson = json.optJSONObject("context")
                    if (contextJson != null) {
                        contextJson.keys().forEach { key ->
                            context[key] = contextJson.optString(key)
                        }
                    }
                    target += LogEntry(
                        timestamp = json.optLong("timestamp"),
                        level = LogLevel.fromLabel(json.optString("level")),
                        tag = json.optString("tag"),
                        message = json.optString("message"),
                        context = context,
                    )
                } catch (_: Exception) {
                    // Ignore a partially-written/corrupt diagnostic line.
                }
            }
        }
    }

    private fun toJson(entry: LogEntry): JSONObject =
        JSONObject().apply {
            put("timestamp", entry.timestamp)
            put("level", entry.level.label)
            put("tag", entry.tag)
            put("message", entry.message)
            put("context", JSONObject(entry.context))
        }

    private fun ensureDirectory() {
        directory().mkdirs()
    }

    private fun directory(): File {
        val context = appContext ?: error("AppLogger.initialize(context) must be called first")
        return File(context.filesDir, LOG_DIR)
    }
}
