package com.alicepro.mobile

/** Pure calculation; BitmapFactory.Options.inSampleSize defaults to zero. */
internal object PreviewSampling {
    fun sampleSize(width: Int, height: Int): Int {
        require(width in 1..8192 && height in 1..8192)
        require(width.toLong() * height <= 16 * 1024 * 1024)
        var sample = 1
        while ((maxOf(width, height) + sample - 1) / sample > 1024) sample *= 2
        return sample
    }
}
