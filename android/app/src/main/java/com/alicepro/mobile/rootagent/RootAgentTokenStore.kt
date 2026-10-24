package com.alicepro.mobile.rootagent

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.io.File
import java.security.GeneralSecurityException
import java.security.KeyStore
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * RootAgent API token, encrypted at rest with a non-exportable AES-256-GCM key
 * in AndroidKeyStore (the Android backend of Secret Store #755). The plaintext
 * exists only in this process; it is never logged or written unencrypted.
 */
class RootAgentTokenStore(context: Context) {
    private val file = File(File(context.noBackupFilesDir, "root-agent"), "token.enc")

    @Synchronized
    fun loadOrCreate(): String {
        val key = key()
        if (file.isFile) {
            try {
                return decrypt(key, file.readBytes())
            } catch (_: GeneralSecurityException) {
                // Key was reset or the file is damaged: issue a new token below.
            } catch (_: IllegalArgumentException) {
            }
        }
        val raw = ByteArray(32).also { SecureRandom().nextBytes(it) }
        val token = Base64.encodeToString(raw, Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)
        writeAtomically(encrypt(key, token))
        return token
    }

    private fun key(): SecretKey {
        val store = KeyStore.getInstance(KEYSTORE).apply { load(null) }
        (store.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE)
        generator.init(
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build(),
        )
        return generator.generateKey()
    }

    private fun encrypt(key: SecretKey, token: String): ByteArray {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, key)
        val iv = cipher.iv
        require(iv.size == IV_BYTES) { "unexpected IV size" }
        return iv + cipher.doFinal(token.toByteArray(Charsets.US_ASCII))
    }

    private fun decrypt(key: SecretKey, blob: ByteArray): String {
        require(blob.size > IV_BYTES) { "token blob too short" }
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(128, blob, 0, IV_BYTES))
        val token = String(cipher.doFinal(blob, IV_BYTES, blob.size - IV_BYTES), Charsets.US_ASCII)
        require(token.length >= RootAgentServer.MIN_TOKEN_LENGTH) { "token too short" }
        return token
    }

    private fun writeAtomically(bytes: ByteArray) {
        val dir = file.parentFile ?: throw IllegalStateException("no token directory")
        if (!dir.isDirectory && !dir.mkdirs()) throw IllegalStateException("cannot create token directory")
        val tmp = File(dir, "token.enc.tmp")
        tmp.outputStream().use { out ->
            out.write(bytes)
            out.fd.sync()
        }
        if (!tmp.renameTo(file)) throw IllegalStateException("cannot store token")
    }

    private companion object {
        const val KEYSTORE = "AndroidKeyStore"
        const val ALIAS = "alice_root_agent_token"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val IV_BYTES = 12
    }
}
