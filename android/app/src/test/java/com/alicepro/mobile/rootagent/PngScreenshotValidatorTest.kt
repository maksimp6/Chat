package com.alicepro.mobile.rootagent

import java.io.ByteArrayOutputStream
import java.io.DataOutputStream
import java.util.zip.CRC32
import java.util.zip.DeflaterOutputStream
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PngScreenshotValidatorTest {
    private val signature = byteArrayOf(0x89.toByte(), 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A)

    private fun chunk(type: String, data: ByteArray): ByteArray = ByteArrayOutputStream().also { buffer ->
        DataOutputStream(buffer).use { out ->
            val name = type.toByteArray(Charsets.US_ASCII)
            out.writeInt(data.size)
            out.write(name)
            out.write(data)
            out.writeInt(CRC32().apply { update(name); update(data) }.value.toInt())
        }
    }.toByteArray()

    private fun header(width: Int = 1, height: Int = 1, color: Int = 6): ByteArray =
        chunk("IHDR", ByteArrayOutputStream().also { buffer ->
            DataOutputStream(buffer).use {
                it.writeInt(width); it.writeInt(height)
                it.write(byteArrayOf(8, color.toByte(), 0, 0, 0))
            }
        }.toByteArray())

    private fun compress(raw: ByteArray): ByteArray = ByteArrayOutputStream().also { buffer ->
        DeflaterOutputStream(buffer).use { it.write(raw) }
    }.toByteArray()

    private fun image(raw: ByteArray, color: Int = 6): ByteArray =
        signature + header(color = color) + chunk("IDAT", compress(raw)) + chunk("IEND", byteArrayOf())

    @Test
    fun rgbAndRgbaWithValidFiltersAreAccepted() {
        for (filter in 0..4) {
            assertTrue(PngScreenshotValidator.valid(image(byteArrayOf(filter.toByte(), 1, 2, 3, 4))))
            assertTrue(PngScreenshotValidator.valid(image(byteArrayOf(filter.toByte(), 1, 2, 3), color = 2)))
        }
    }

    @Test
    fun validCrcDoesNotExcuseBadZlibOrMissingScanlines() {
        for (compressed in listOf(byteArrayOf(1, 2, 3), compress(byteArrayOf(0, 1, 2)))) {
            assertFalse(PngScreenshotValidator.valid(signature + header() + chunk("IDAT", compressed) + chunk("IEND", byteArrayOf())))
        }
    }

    @Test
    fun invalidFilterOrTooManyPixelsAreRejected() {
        assertFalse(PngScreenshotValidator.valid(image(byteArrayOf(5, 1, 2, 3, 4))))
        assertFalse(PngScreenshotValidator.valid(image(byteArrayOf(0, 1, 2, 3, 4, 5))))
    }

    @Test
    fun truncatedAndTrailingCompressedDataAreRejected() {
        val data = compress(byteArrayOf(0, 1, 2, 3, 4))
        for (bad in listOf(data.copyOf(data.size - 1), data + byteArrayOf(0))) {
            assertFalse(PngScreenshotValidator.valid(signature + header() + chunk("IDAT", bad) + chunk("IEND", byteArrayOf())))
        }
    }

    @Test
    fun trailingFileBytesAndNonemptyEndChunkAreRejected() {
        assertFalse(PngScreenshotValidator.valid(image(byteArrayOf(0, 1, 2, 3, 4)) + byteArrayOf(0)))
        assertFalse(PngScreenshotValidator.valid(signature + header() + chunk("IDAT", compress(byteArrayOf(0, 1, 2, 3, 4))) + chunk("IEND", byteArrayOf(1))))
    }

    @Test
    fun hugeChunkLengthsAndDimensionBombsFailClosed() {
        val hugeChunk = byteArrayOf(0x7f, 0xff.toByte(), 0xff.toByte(), 0xff.toByte()) + "IDAT".toByteArray() + byteArrayOf(0, 0, 0, 0)
        assertFalse(PngScreenshotValidator.valid(signature + header() + hugeChunk))
        for ((w, h) in listOf(0 to 1, 1 to 0, -1 to 1, 8193 to 1, 8192 to 8192)) {
            assertFalse(PngScreenshotValidator.valid(signature + header(w, h) + chunk("IEND", byteArrayOf())))
        }
    }

    @Test
    fun chunksMustFollowPngOrderingRules() {
        val data = chunk("IDAT", compress(byteArrayOf(0, 1, 2, 3, 4)))
        val end = chunk("IEND", byteArrayOf())
        val palette = chunk("PLTE", byteArrayOf(0, 0, 0))
        for (body in listOf(data + header() + end, header() + header() + data + end,
            header() + chunk("ABCD", byteArrayOf()) + data + end,
            header() + palette + palette + data + end, header() + data + palette + end)) {
            assertFalse(PngScreenshotValidator.valid(signature + body))
        }
    }

    @Test
    fun multipleContiguousDataChunksAndAncillaryChunksAreAccepted() {
        val compressed = compress(byteArrayOf(0, 1, 2, 3, 4))
        val split = compressed.size / 2
        val first = chunk("IDAT", compressed.copyOfRange(0, split))
        val second = chunk("IDAT", compressed.copyOfRange(split, compressed.size))
        val note = chunk("tEXt", "label\u0000value".toByteArray())
        val end = chunk("IEND", byteArrayOf())
        assertTrue(PngScreenshotValidator.valid(signature + header() + note + first + second + note + end))
        assertFalse(PngScreenshotValidator.valid(signature + header() + first + note + second + end))
    }

    @Test
    fun redmi9SizedScanlinesCrossInflaterBufferBoundaries() {
        val width = 720
        val height = 1600
        val rowBytes = width * 4 + 1
        val raw = ByteArray(rowBytes * height)
        for (row in 0 until height) raw[row * rowBytes] = (row % 5).toByte()
        val png = signature + header(width, height) + chunk("IDAT", compress(raw)) + chunk("IEND", byteArrayOf())
        assertTrue(PngScreenshotValidator.valid(png))
        raw[1000 * rowBytes] = 5
        val bad = signature + header(width, height) + chunk("IDAT", compress(raw)) + chunk("IEND", byteArrayOf())
        assertFalse(PngScreenshotValidator.valid(bad))
    }
}
