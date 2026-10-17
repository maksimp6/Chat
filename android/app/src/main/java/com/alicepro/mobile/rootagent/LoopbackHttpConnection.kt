package com.alicepro.mobile.rootagent

import java.io.EOFException
import java.io.IOException
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import java.net.URI
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/** Minimal GET transport for this server's Content-Length responses. Closing owns the socket. */
internal class LoopbackHttpConnection(uri: URI) : HttpURLConnection(uri.toURL()) {
    private val socket = AtomicReference<Socket?>()
    private val closed = AtomicBoolean(false)
    private var code: Int? = null
    private var body: InputStream? = null

    init {
        require(uri.scheme == "http" && uri.host == RootAgentServer.LOOPBACK && uri.port in 1..65535)
        require(uri.rawQuery == null && uri.userInfo == null && uri.fragment == null)
        require(RootAgentClient.Endpoint.entries.any { it.path == uri.rawPath })
    }

    override fun connect() {
        if (connected) return
        if (closed.get()) throw IOException("cancelled")
        val candidate = Socket()
        socket.set(candidate)
        if (closed.get()) {
            candidate.close()
            throw IOException("cancelled")
        }
        candidate.connect(InetSocketAddress(InetAddress.getByAddress(byteArrayOf(127, 0, 0, 1)), url.port), connectTimeout)
        candidate.soTimeout = readTimeout
        val auth = getRequestProperty("Authorization") ?: throw IOException("authorization missing")
        if (auth.any { it == '\r' || it == '\n' }) throw IOException("invalid authorization")
        val request = "GET ${url.path} HTTP/1.1\r\nHost: 127.0.0.1:${url.port}\r\nAuthorization: $auth\r\nConnection: close\r\n\r\n"
        candidate.getOutputStream().write(request.toByteArray(Charsets.US_ASCII))
        connected = true
    }

    private fun response() {
        if (code != null) return
        connect()
        val input = socket.get()?.getInputStream() ?: throw IOException("cancelled")
        val header = StringBuilder()
        while (!header.endsWith("\r\n\r\n")) {
            if (header.length >= 16 * 1024) throw IOException("response header too large")
            val value = input.read()
            if (value < 0) throw EOFException()
            if (value == 0 || value > 127) throw IOException("invalid response header")
            header.append(value.toChar())
        }
        val lines = header.toString().split("\r\n")
        val status = lines.first().split(' ')
        if (status.size < 2 || status[0] !in listOf("HTTP/1.0", "HTTP/1.1")) throw IOException("invalid status")
        val parsed = status[1].toIntOrNull()?.takeIf { it in 200..599 } ?: throw IOException("invalid status")
        val fields = HashMap<String, String>()
        for (line in lines.drop(1)) {
            if (line.isEmpty()) continue
            val colon = line.indexOf(':')
            if (colon <= 0 || line[0].isWhitespace()) throw IOException("invalid header")
            val name = line.substring(0, colon).lowercase()
            if (fields.put(name, line.substring(colon + 1).trim()) != null) throw IOException("duplicate header")
        }
        if (fields.containsKey("transfer-encoding")) throw IOException("unsupported transfer encoding")
        val length = fields["content-length"]?.toLongOrNull()?.takeIf { it >= 0 } ?: throw IOException("invalid length")
        body = object : InputStream() {
            private var remaining = length
            override fun read(): Int {
                if (remaining == 0L) return -1
                val value = input.read()
                if (value < 0) throw EOFException()
                remaining--
                return value
            }
            override fun read(bytes: ByteArray, offset: Int, count: Int): Int {
                if (count == 0) return 0
                if (remaining == 0L) return -1
                val n = input.read(bytes, offset, minOf(count.toLong(), remaining).toInt())
                if (n < 0) throw EOFException()
                remaining -= n
                return n
            }
            override fun close() = disconnect()
        }
        code = parsed
    }

    override fun getResponseCode(): Int { response(); return code!! }
    override fun getInputStream(): InputStream { response(); return body!! }
    override fun getErrorStream(): InputStream? { response(); return if (code!! >= 400) body else null }
    override fun usingProxy() = false
    override fun disconnect() {
        closed.set(true)
        socket.getAndSet(null)?.close()
    }
}
