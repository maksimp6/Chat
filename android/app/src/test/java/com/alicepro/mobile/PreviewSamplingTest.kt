package com.alicepro.mobile

import org.junit.Assert.assertEquals
import org.junit.Test

class PreviewSamplingTest {
    @Test
    fun smallAndRedmiSizedImagesAlwaysHavePositiveSample() {
        assertEquals(1, PreviewSampling.sampleSize(1, 1))
        assertEquals(1, PreviewSampling.sampleSize(1024, 1024))
        assertEquals(2, PreviewSampling.sampleSize(720, 1600))
        assertEquals(4, PreviewSampling.sampleSize(2049, 1))
        assertEquals(8, PreviewSampling.sampleSize(8192, 1))
    }

    @Test(expected = IllegalArgumentException::class)
    fun zeroDimensionsAreRefused() { PreviewSampling.sampleSize(0, 1600) }

    @Test(expected = IllegalArgumentException::class)
    fun PixelBombIsRefused() { PreviewSampling.sampleSize(8192, 8192) }
}
