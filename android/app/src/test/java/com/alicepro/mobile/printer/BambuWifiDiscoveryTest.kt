package com.alicepro.mobile.printer

import java.net.InetAddress
import org.junit.Assert.*
import org.junit.Test

class BambuWifiDiscoveryTest {
    @Test fun acceptsOnlyRfc1918Ipv4() {
        for (ip in listOf("10.0.0.1", "172.16.0.1", "172.31.255.254", "192.168.31.12")) {
            assertTrue(BambuWifiDiscovery.privateIpv4(InetAddress.getByName(ip)))
        }
        for (ip in listOf("8.8.8.8", "127.0.0.1", "169.254.1.1", "100.64.0.1", "::1")) {
            assertFalse(BambuWifiDiscovery.privateIpv4(InetAddress.getByName(ip)))
        }
    }

    @Test fun rejectsHostInjection() {
        for (host in listOf("", "http://printer.local", "printer.local:3000", "bad/name", "a..b")) {
            try {
                BambuWifiDiscovery.validateHost(host)
                fail("accepted invalid host: $host")
            } catch (_: IllegalArgumentException) { }
        }
        BambuWifiDiscovery.validateHost("printer.local")
    }

    @Test fun parserRejectsInvalidFrameAndAcceptsDetect() {
        val payload = """{"login":{"command":"detect","model":"N1"}}""".toByteArray(Charsets.UTF_8)
        val size = payload.size + 6
        val frame = byteArrayOf(0xa5.toByte(), 0xa5.toByte(),
            (size and 255).toByte(), (size shr 8).toByte()) + payload +
            byteArrayOf(0xa7.toByte(), 0xa7.toByte())
        assertEquals("N1", BambuWifiDiscovery.parseFrame(frame).getString("model"))
        try {
            BambuWifiDiscovery.parseFrame(frame.copyOf(frame.size - 1))
            fail("truncated frame accepted")
        } catch (_: IllegalArgumentException) { }
    }

    @Test fun discoveryFrameIsReadOnlyAndLengthMatched() {
        val frame = BambuWifiDiscovery.detectFrame()
        val declared = (frame[2].toInt() and 255) or ((frame[3].toInt() and 255) shl 8)
        assertEquals(frame.size, declared)
        assertEquals(0xa5.toByte(), frame[0])
        assertEquals(0xa7.toByte(), frame.last())
        val payload = String(frame, 4, frame.size - 6, Charsets.UTF_8)
        assertTrue(payload.contains("\"detect\""))
        assertFalse(payload.contains("\"print\""))
        assertFalse(payload.contains("\"heat\""))
    }
}
