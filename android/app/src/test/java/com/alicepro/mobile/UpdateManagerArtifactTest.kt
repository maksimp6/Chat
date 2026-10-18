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
}
