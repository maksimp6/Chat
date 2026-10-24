package com.alicepro.mobile.rootagent

/** One monotonic budget for all startup probes and pauses, not one budget per attempt. */
internal object StartupHealthCheck {
    fun await(
        probe: (Long) -> RootAgentClient.Response,
        timeoutMs: Long = 10_000,
        nowMs: () -> Long = { System.nanoTime() / 1_000_000 },
        pause: (Long) -> Unit = { Thread.sleep(it) },
    ): RootAgentClient.Response {
        require(timeoutMs > 0)
        val started = nowMs()
        fun remaining() = timeoutMs - (nowMs() - started)
        while (true) {
            val budget = remaining()
            if (budget <= 0) return RootAgentClient.Response(null, "", "timeout")
            val response = probe(budget)
            if (response.error == "cancelled") return response
            if (remaining() <= 0) return RootAgentClient.Response(null, "", "timeout")
            if (response.code == 200) return response
            val rest = remaining()
            if (rest <= 0) return RootAgentClient.Response(null, "", "timeout")
            pause(minOf(150L, rest))
        }
    }
}
