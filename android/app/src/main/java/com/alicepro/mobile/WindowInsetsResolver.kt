package com.alicepro.mobile

import androidx.core.graphics.Insets

object WindowInsetsResolver {
    fun resolve(
        systemBars: Insets,
        displayCutout: Insets,
        ime: Insets,
    ): Insets = Insets.of(
        maxOf(systemBars.left, displayCutout.left),
        maxOf(systemBars.top, displayCutout.top),
        maxOf(systemBars.right, displayCutout.right),
        maxOf(systemBars.bottom, displayCutout.bottom, ime.bottom),
    )
}
