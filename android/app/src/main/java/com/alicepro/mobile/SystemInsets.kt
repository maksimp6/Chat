package com.alicepro.mobile

import android.view.View
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding

object SystemInsets {
    fun resolve(insets: WindowInsetsCompat): androidx.core.graphics.Insets {
        val bars = insets.getInsets(
            WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout()
        )
        val gestures = insets.getInsets(WindowInsetsCompat.Type.mandatorySystemGestures())
        val ime = insets.getInsets(WindowInsetsCompat.Type.ime())

        return WindowInsetsResolver.resolve(
            systemBars = bars,
            displayCutout = androidx.core.graphics.Insets.NONE,
            ime = ime,
            mandatorySystemGestures = gestures,
        )
    }

    fun applySafePadding(
        view: View,
        baseLeft: Int = view.paddingLeft,
        baseTop: Int = view.paddingTop,
        baseRight: Int = view.paddingRight,
        baseBottom: Int = view.paddingBottom,
    ) {
        ViewCompat.setOnApplyWindowInsetsListener(view) { target, insets ->
            val safe = resolve(insets)
            target.updatePadding(
                left = baseLeft + safe.left,
                top = baseTop + safe.top,
                right = baseRight + safe.right,
                bottom = baseBottom + safe.bottom,
            )
            insets
        }
        ViewCompat.requestApplyInsets(view)
    }
}
