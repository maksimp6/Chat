package com.alicepro.mobile.printer

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import java.io.EOFException
import java.net.Inet4Address
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import org.json.JSONObject

/**
 * Native read-only A1 mini discovery. Not exported as an Android service and
 * never changes process-wide network binding or printer state.
 */
internal object BambuWifiDiscovery {
    internal fun privateIpv4(address: InetAddress): Boolean {
        if (address !is Inet4Address) return false
        val bytes = address.address.map { it.toInt() and 255 }
        return bytes[0] == 10 ||
            (bytes[0] == 172 && bytes[1] in 16..31) ||
            (bytes[0] == 192 && bytes[1] == 168)
    }

    internal fun validateHost(host: String) {
        require(host.isNotBlank() && host.length <= 253)
        require(host.all { it.isLetterOrDigit() || it == '.' || it == '-' })
        require(host.split('.').all { it.isNotBlank() && it.length <= 63 })
    }

    internal fun detectFrame(): ByteArray {
        val payload = """{"login":{"command":"detect","sequence_id":"1"}}""".toByteArray(Charsets.UTF_8)
        val length = payload.size + 6
        return byteArrayOf(0xa5.toByte(), 0xa5.toByte(),
            (length and 255).toByte(), (length shr 8).toByte()) +
            payload + byteArrayOf(0xa7.toByte(), 0xa7.toByte())
    }

    internal fun parseFrame(data: ByteArray): JSONObject {
        require(data.size in 6..65535 && data[0] == 0xa5.toByte() && data[1] == 0xa5.toByte())
        val length = (data[2].toInt() and 255) or ((data[3].toInt() and 255) shl 8)
        require(length == data.size && data[data.size - 2] == 0xa7.toByte() &&
            data[data.size - 1] == 0xa7.toByte())
        val message = JSONObject(String(data, 4, data.size - 6, Charsets.UTF_8))
        val login = message.getJSONObject("login")
        require(login.getString("command") == "detect" && login.getString("model").isNotBlank())
        return login
    }

    internal data class Identity(val pinnedIp: String, val modelCode: String)

    fun discover(context: Context, host: String): Identity {
        validateHost(host)
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val wifiNetworks = cm.allNetworks.filter { network ->
            cm.getNetworkCapabilities(network)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true
        }
        check(wifiNetworks.size == 1) { "Exactly one Wi-Fi network required" }
        val network = wifiNetworks.single()
        // DNS resolution is explicitly scoped to the selected Wi-Fi network.
        val addresses = network.getAllByName(host)
        check(addresses.isNotEmpty() && addresses.all(::privateIpv4)) {
            "DNS returned an untrusted or non-private address"
        }
        val pinned = addresses.first()
        Socket().use { socket ->
            socket.soTimeout = 3000
            // Android's Network.bindSocket works without root; fail closed on any error.
            network.bindSocket(socket)
            socket.connect(InetSocketAddress(pinned, 3000), 3000)
            check(socket.inetAddress == pinned) { "Peer mismatch" }
            socket.getOutputStream().write(detectFrame())
            val input = socket.getInputStream()
            val header = ByteArray(4)
            readFully(input, header)
            check(header[0] == 0xa5.toByte() && header[1] == 0xa5.toByte())
            val length = (header[2].toInt() and 255) or ((header[3].toInt() and 255) shl 8)
            check(length in 6..65535)
            val tail = ByteArray(length - 4)
            readFully(input, tail)
            val login = parseFrame(header + tail)
            return Identity(pinned.hostAddress ?: error("missing IP"), login.getString("model"))
        }
    }

    private fun readFully(input: java.io.InputStream, destination: ByteArray) {
        var offset = 0
        while (offset < destination.size) {
            val count = input.read(destination, offset, destination.size - offset)
            if (count < 0) throw EOFException("truncated printer response")
            offset += count
        }
    }
}
