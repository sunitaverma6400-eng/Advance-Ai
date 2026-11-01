package com.stark.family

import android.app.Application
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshots.SnapshotStateList
import androidx.compose.ui.graphics.Color
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

data class Character(val id: String, val name: String, val accent: Color, val accent2: Color)

object Characters {
    val JARVIS = Character("jarvis", "J.A.R.V.I.S.", Color(0xFF2BB8FF), Color(0xFF8FE3FF))
    val VISION = Character("vision", "Vision", Color(0xFFD7344F), Color(0xFFF2C14E))
    val all = listOf(JARVIS, VISION)
    fun byId(id: String): Character = all.first { it.id == id }
}

enum class Kind { USER, AI, NOTE }

data class Msg(val kind: Kind, val text: String, val meta: String = "")

class ChatViewModel(app: Application) : AndroidViewModel(app) {
    private val prefs = Prefs(app)
    private val client = RelayClient()
    private val histories = mutableMapOf<String, SnapshotStateList<Msg>>()

    var character by mutableStateOf(Characters.JARVIS)
    var relayUrl by mutableStateOf(prefs.relayUrl)
    var token by mutableStateOf(prefs.token)
    var busy by mutableStateOf(false)
    var waking by mutableStateOf(false)
    var showSettings by mutableStateOf(prefs.relayUrl.isBlank() || prefs.token.isBlank())
    var brainText by mutableStateOf<String?>(null)

    fun messages(): SnapshotStateList<Msg> = histories.getOrPut(character.id) { mutableStateListOf() }

    fun select(id: String) { character = Characters.byId(id) }

    fun saveSettings(url: String, tok: String) {
        prefs.relayUrl = url
        prefs.token = tok
        relayUrl = prefs.relayUrl
        token = prefs.token
        showSettings = false
    }

    fun openBrain() {
        if (relayUrl.isBlank() || token.isBlank()) { showSettings = true; return }
        brainText = "लोड हो रहा है…"
        viewModelScope.launch {
            brainText = try {
                formatBrain(client.brainStatus(relayUrl, token) { waking = it })
            } catch (e: Exception) {
                e.message ?: "त्रुटि"
            } finally {
                waking = false
            }
        }
    }

    fun send(text: String) {
        val t = text.trim()
        if (t.isEmpty() || busy) return
        if (relayUrl.isBlank() || token.isBlank()) { showSettings = true; return }
        val ch = character
        val list = histories.getOrPut(ch.id) { mutableStateListOf() }
        val history = list.filter { it.kind != Kind.NOTE }.takeLast(12)
            .map { (if (it.kind == Kind.USER) "user" else "assistant") to it.text }
        list.add(Msg(Kind.USER, t))
        busy = true
        viewModelScope.launch {
            try {
                var tries = 0
                while (true) {
                    val r = client.chat(relayUrl, token, ch.id, t, history) { waking = it }
                    if (!r.degraded) {
                        var meta = "${r.provider} · ${r.model}"
                        if (!r.languageOk) meta += " · ⚠ जवाब पूरी तरह हिंदी में नहीं"
                        list.add(Msg(Kind.AI, r.speech, meta))
                        break
                    }
                    tries++
                    if (tries == 1) list.add(Msg(Kind.NOTE, r.speech))
                    if (tries >= 3) {
                        list.add(Msg(Kind.NOTE, "अभी जवाब नहीं मिल पाया। थोड़ी देर बाद फिर भेजिए।"))
                        break
                    }
                    delay(maxOf(r.retryAfterS, 8) * 1000L)
                }
            } catch (e: RelayClient.RelayException) {
                list.add(Msg(Kind.NOTE, e.message ?: "त्रुटि"))
            } catch (e: Exception) {
                list.add(Msg(Kind.NOTE, "अनपेक्षित त्रुटि: ${e.javaClass.simpleName}"))
            } finally {
                busy = false
                waking = false
            }
        }
    }
}
