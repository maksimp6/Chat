package com.alicepro.mobile

import androidx.core.graphics.Insets
import org.junit.Assert.assertEquals
import org.junit.Test

class WindowInsetsResolverTest {
    @Test
    fun keepsTheLargestSafeInsetOnEveryEdge() {
        val result = WindowInsetsResolver.resolve(
            systemBars = Insets.of(1, 24, 1, 20),
            displayCutout = Insets.of(4, 32, 8, 3),
            ime = Insets.of(0, 0, 0, 600),
        )

        assertEquals(4, result.left)
        assertEquals(32, result.top)
        assertEquals(8, result.right)
        assertEquals(600, result.bottom)
    }

    @Test
    fun imeDoesNotShrinkNavigationInset() {
        val result = WindowInsetsResolver.resolve(
            systemBars = Insets.of(0, 0, 0, 80),
            displayCutout = Insets.NONE,
            ime = Insets.of(0, 0, 0, 20),
        )

        assertEquals(80, result.bottom)
    }
}
