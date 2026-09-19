package com.alicepro.mobile

import androidx.core.graphics.Insets

object WindowInsetsResolver {
    fun resolve(
        systemBars: Insets,
        displayCutout: Insets,
        ime: Insets,
        mandatorySystemGestures: Insets = Insets.NONE,
    ): Insets = Insets.of(
        maxOf(systemBars.left, displayCutout.left, mandatorySystemGestures.left),
        maxOf(systemBars.top, displayCutout.top),
        maxOf(systemBars.right, displayCutout.right, mandatorySystemGestures.right),
        maxOf(
            systemBars.bottom,
            displayCutout.bottom,
            mandatorySystemGestures.bottom,
            ime.bottom,
        ),
    )
}
