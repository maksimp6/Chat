package com.alicepro.mobile.rootagent

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ScreenTreeTest {
    private val xml = "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>" +
        "<hierarchy rotation=\"0\"><node text=\"Привет\" bounds=\"[0,0][720,1600]\" /></hierarchy>"

    private fun ops(result: RootShellResult, seen: MutableList<List<String>> = mutableListOf()) = RootOperations(
        runRoot = { error("small runner must not be used for the tree") },
        deviceInfo = { emptyMap() },
        runTree = { argv -> seen.add(argv); result },
    )

    @Test
    fun dumpIsExtractedFromUiautomatorOutput() {
        val seen = mutableListOf<List<String>>()
        val out = RootShellResult(0, xml + "UI hierchary dumped to: /proc/self/fd/1\n", false, false)
        val tree = ops(out, seen).screenTree()
        assertEquals(RootOperations.ScreenTree.Answered(xml), tree)
        assertEquals(listOf(RootOperations.TREE_ARGV), seen)
    }

    @Test
    fun treeUsesAFixedArgv() {
        // ProcessBuilder gives su no controlling terminal, so /dev/tty cannot be opened;
        // uiautomator writes the dump to its own stdout pipe instead.
        assertEquals(listOf("su", "-c", "uiautomator dump /proc/self/fd/1"), RootOperations.TREE_ARGV)
    }

    @Test
    fun timeoutIsReported() {
        assertEquals(RootOperations.ScreenTree.TimedOut, ops(RootShellResult(null, "", true, false)).screenTree())
    }

    @Test
    fun failedTruncatedOrForeignOutputIsUnavailable() {
        val cases = listOf(
            RootShellResult(1, xml, false, false),
            RootShellResult(0, xml, false, truncated = true),
            RootShellResult(0, "ERROR: could not get idle state.\n", false, false),
            RootShellResult(0, "<hierarchy rotation=\"0\">", false, false),
            RootShellResult(null, "", false, false, failedToStart = true),
        )
        for (case in cases) {
            assertEquals(case.toString(), RootOperations.ScreenTree.Unavailable, ops(case).screenTree())
        }
    }

    @Test
    fun unavailableNeverCarriesRawOutput() {
        val tree = ops(RootShellResult(1, "secret text", false, false)).screenTree()
        assertTrue(tree === RootOperations.ScreenTree.Unavailable)
    }
}
