package com.alicepro.mobile

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class UpdateManagerArtifactTest {
    @Test
    fun matchesCiDebugApkArtifactWithRunNumberSuffix() {
        assertTrue(UpdateManager.isDebugApkArtifactName("alice-pro-debug-apk-482"))
    }

    @Test
    fun rejectsUnsuffixedAndUnrelatedArtifactNames() {
        assertFalse(UpdateManager.isDebugApkArtifactName("alice-pro-debug-apk"))
        assertFalse(UpdateManager.isDebugApkArtifactName("alice-pro-release-apk-482"))
        assertFalse(UpdateManager.isDebugApkArtifactName(""))
    }

    @Test
    fun matchesSingleDigitRunNumber() {
        assertTrue(UpdateManager.isDebugApkArtifactName("alice-pro-debug-apk-1"))
    }

    @Test
    fun rejectsPrefixWithoutRunNumber() {
        assertFalse(UpdateManager.isDebugApkArtifactName("alice-pro-debug-apk-"))
    }

    @Test
    fun rejectsPrefixWithNonNumericSuffix() {
        assertFalse(UpdateManager.isDebugApkArtifactName("alice-pro-debug-apk-latest"))
    }

    @Test
    fun rejectsPrefixWithDottedOrDashedSuffix() {
        assertFalse(UpdateManager.isDebugApkArtifactName("alice-pro-debug-apk-482.zip"))
        assertFalse(UpdateManager.isDebugApkArtifactName("alice-pro-debug-apk-482-logs"))
    }

    @Test
    fun rejectsTrailingWhitespace() {
        assertFalse(UpdateManager.isDebugApkArtifactName("alice-pro-debug-apk-482 "))
    }

    @Test
    fun rejectsDifferentProductPrefix() {
        assertFalse(UpdateManager.isDebugApkArtifactName("other-product-debug-apk-482"))
    }

    @Test
    fun rejectsNonAsciiDigits() {
        assertFalse(UpdateManager.isDebugApkArtifactName("alice-pro-debug-apk-٤٨٢"))
    }
}
