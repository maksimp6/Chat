package com.alicepro.mobile.rootagent

import java.io.ByteArrayOutputStream
import java.util.zip.CRC32
import java.util.zip.DataFormatException
import java.util.zip.Inflater

/** Bounded validation of the non-interlaced 8-bit RGB/RGBA PNGs produced by screencap. */
internal object PngScreenshotValidator {
    private val MAGIC = byteArrayOf(0x89.toByte(), 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A)
    private const val MAX_BYTES = 9 * 1024 * 1024
    private const val MAX_DIMENSION = 8192
    private const val MAX_PIXELS = 16 * 1024 * 1024

    fun valid(png: ByteArray): Boolean {
        if (png.size !in 8..MAX_BYTES || !png.copyOf(8).contentEquals(MAGIC)) return false
        var offset = 8
        var rowBytes = 0
        var height = 0
        var seenData = false
        var dataEnded = false
        var seenPalette = false
        val compressed = ByteArrayOutputStream()
        while (offset < png.size) {
            if (png.size - offset < 12) return false
            val size = unsignedInt(png, offset)
            if (size > png.size - offset - 12) return false
            val length = size.toInt()
            val typeOffset = offset + 4
            val type = String(png, typeOffset, 4, Charsets.US_ASCII)
            if ((typeOffset until typeOffset + 4).any {
                val c = png[it].toInt() and 255
                c !in 65..90 && c !in 97..122
            } || png[typeOffset + 2].toInt() and 32 != 0) return false
            val crc = CRC32().apply { update(png, typeOffset, length + 4) }.value
            if (crc != unsignedInt(png, offset + 8 + length)) return false
            val dataOffset = offset + 8
            when (type) {
                "IHDR" -> {
                    if (offset != 8 || length != 13) return false
                    val width = unsignedInt(png, dataOffset)
                    val rows = unsignedInt(png, dataOffset + 4)
                    val color = png[dataOffset + 9].toInt() and 255
                    if (width !in 1..MAX_DIMENSION.toLong() || rows !in 1..MAX_DIMENSION.toLong() ||
                        width * rows > MAX_PIXELS || png[dataOffset + 8] != 8.toByte() ||
                        color !in listOf(2, 6) || (10..12).any { png[dataOffset + it] != 0.toByte() }
                    ) return false
                    height = rows.toInt()
                    rowBytes = width.toInt() * (if (color == 6) 4 else 3) + 1
                }
                "IDAT" -> {
                    if (rowBytes == 0 || dataEnded) return false
                    seenData = true
                    compressed.write(png, dataOffset, length)
                }
                "IEND" -> {
                    if (!seenData || length != 0 || offset + 12 != png.size) return false
                    return validPixels(compressed.toByteArray(), rowBytes, height)
                }
                "PLTE" -> {
                    // Optional suggested palette for truecolor must precede the image data.
                    if (rowBytes == 0 || seenData || seenPalette || length !in 3..768 || length % 3 != 0) return false
                    seenPalette = true
                }
                else -> {
                    // Unknown critical chunks cannot be decoded safely; ancillary chunks are bounded/CRC checked.
                    if (rowBytes == 0 || png[typeOffset].toInt() and 32 == 0) return false
                    if (seenData) dataEnded = true
                }
            }
            offset += length + 12
        }
        return false
    }

    private fun validPixels(compressed: ByteArray, rowBytes: Int, height: Int): Boolean {
        val inflater = Inflater()
        try {
            inflater.setInput(compressed)
            val buffer = ByteArray(32 * 1024)
            val expected = rowBytes.toLong() * height
            var total = 0L
            while (!inflater.finished()) {
                val count = inflater.inflate(buffer)
                if (count == 0 && !inflater.finished()) return false
                if (total + count > expected) return false
                // A PNG scanline begins with exactly one filter byte in the range 0..4.
                var filter = ((rowBytes - total % rowBytes) % rowBytes).toInt()
                while (filter < count) {
                    if (buffer[filter].toInt() !in 0..4) return false
                    filter += rowBytes
                }
                total += count
            }
            return total == expected && inflater.remaining == 0
        } catch (_: DataFormatException) {
            return false
        } finally {
            inflater.end()
        }
    }

    private fun unsignedInt(bytes: ByteArray, offset: Int): Long {
        var value = 0L
        for (i in offset until offset + 4) value = (value shl 8) or (bytes[i].toLong() and 255)
        return value
    }
}
