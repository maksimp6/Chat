package com.alicepro.mobile

enum class DiagnosticTimeRange(val label: String, val durationMs: Long?) {
    ALL("Все", null),
    LAST_15_MINUTES("15 минут", 15 * 60 * 1000L),
    LAST_HOUR("1 час", 60 * 60 * 1000L),
    LAST_DAY("24 часа", 24 * 60 * 60 * 1000L),
}

object DiagnosticLogQuery {
    fun filter(
        entries: List<AppLogger.LogEntry>,
        level: AppLogger.LogLevel?,
        timeRange: DiagnosticTimeRange,
        query: String,
        nowMs: Long = System.currentTimeMillis(),
    ): List<AppLogger.LogEntry> {
        val normalized = query.trim().lowercase()
        val since = timeRange.durationMs?.let { nowMs - it }

        return entries.filter { entry ->
            val levelMatches = level == null || entry.level == level
            val timeMatches = since == null || entry.timestamp >= since
            val queryMatches = normalized.isBlank() ||
                entry.message.lowercase().contains(normalized) ||
                entry.tag.lowercase().contains(normalized) ||
                entry.context.values.any { it.lowercase().contains(normalized) }
            levelMatches && timeMatches && queryMatches
        }
    }
}
