package com.alicepro.mobile

object LogRedactor {
    private val authorization = Regex("(?i)(authorization\\s*[:=]\\s*bearer\\s+)[^\\s,;]+")
    private val sensitiveKey = Regex(
        "(?i)(\\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|token|password|secret|cookie|set-cookie)\\b\\s*[:=]\\s*)([\\\"']?)[^\\s,;}'\\\"]+\\2"
    )
    private val jwt = Regex("\\beyJ[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{10,}\\b")
    private val email = Regex("\\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}\\b", RegexOption.IGNORE_CASE)

    fun redact(value: String): String {
        var result = value
        result = authorization.replace(result) { "${it.groupValues[1]}<redacted>" }
        result = sensitiveKey.replace(result) { "${it.groupValues[1]}<redacted>" }
        result = jwt.replace(result, "<redacted-jwt>")
        result = email.replace(result, "<redacted-email>")
        return result
    }
}
