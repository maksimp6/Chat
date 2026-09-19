package com.alicepro.mobile

import androidx.core.graphics.Insets

object WindowInsetsResolver {
    fun resolve(
        systemBars: Insets,
        displayCutout: Insets,
        ime: Insets,
        waterfall: Insets = Insets.NONE,
        mandatorySystemGestures: Insets = Insets.NONE,
    ): Insets = Insets.of(
        maxOf(systemBars.left, displayCutout.left, waterfall.left, mandatorySystemGestures.left),
        maxOf(systemBars.top, displayCutout.top, waterfall.top),
        maxOf(systemBars.right, displayCutout.right, waterfall.right, mandatorySystemGestures.right),
        maxOf(
            systemBars.bottom,
            displayCutout.bottom,
            waterfall.bottom,
            mandatorySystemGestures.bottom,
            ime.bottom,
        ),
    )
}
