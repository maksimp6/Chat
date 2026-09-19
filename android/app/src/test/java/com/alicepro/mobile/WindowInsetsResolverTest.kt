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
            waterfall = Insets.of(6, 5, 7, 4),
            mandatorySystemGestures = Insets.of(9, 2, 10, 700),
        )

        assertEquals(9, result.left)
        assertEquals(32, result.top)
        assertEquals(10, result.right)
        assertEquals(700, result.bottom)
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

    @Test
    fun waterfallAndGestureInsetsProtectEdgeContentWhenBarsAreZero() {
        val result = WindowInsetsResolver.resolve(
            systemBars = Insets.NONE,
            displayCutout = Insets.NONE,
            ime = Insets.NONE,
            waterfall = Insets.of(5, 3, 6, 4),
            mandatorySystemGestures = Insets.of(8, 1, 9, 12),
        )

        assertEquals(8, result.left)
        assertEquals(3, result.top)
        assertEquals(9, result.right)
        assertEquals(12, result.bottom)
    }
}
