package com.stark.family

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

data class ChatReply(
    val speech: String,
    val degraded: Boolean,
    val retryAfterS: Int,
    val languageOk: Boolean,
    val provider: String,
    val model: String,
)

class RelayClient {
    class RelayException(message: String) : Exception(message)

    private val http = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(75, TimeUnit.SECONDS)
        .writeTimeout(20, TimeUnit.SECONDS)
        .build()
    private val jsonType = "application/json; charset=utf-8".toMediaType()

    suspend fun chat(
        base: String, token: String, character: String, message: String,
        history: List<Pair<String, String>>, onWaking: (Boolean) -> Unit,
    ): ChatReply {
        val hist = JSONArray()
        history.forEach { (role, content) -> hist.put(JSONObject().put("role", role).put("content", content)) }
        val body = JSONObject().put("character", character).put("message", message).put("history", hist)
        val j = request(base, "/chat", token, body.toString(), onWaking)
        return ChatReply(
            speech = j.optString("speech", ""),
            degraded = j.optBoolean("degraded", false),
            retryAfterS = j.optInt("retry_after_s", 15),
            languageOk = j.optBoolean("language_ok", true),
            provider = j.optString("provider", ""),
            model = j.optString("model", ""),
        )
    }

    suspend fun brainStatus(base: String, token: String, onWaking: (Boolean) -> Unit): JSONObject =
        request(base, "/brain_status", token, null, onWaking)

    /** Render's free tier sleeps: retry with backoff and tell the UI we are waking it up. */
    private suspend fun request(
        base: String, path: String, token: String, body: String?, onWaking: (Boolean) -> Unit,
    ): JSONObject {
        val url = base.trim().trimEnd('/') + path
        val req = try {
            val b = Request.Builder().url(url).header("X-App-Token", token)
            if (body != null) b.post(body.toRequestBody(jsonType)) else b.get()
            b.build()
        } catch (e: IllegalArgumentException) {
            throw RelayException("Relay URL सही नहीं लग रहा। सेटिंग में पूरा https://… पता डालिए।")
        }
        var wait = 3000L
        for (attempt in 1..5) {
            try {
                val (code, text) = withContext(Dispatchers.IO) {
                    http.newCall(req).execute().use { it.code to (it.body?.string() ?: "") }
                }
                when {
                    code in 200..299 -> { onWaking(false); return JSONObject(text) }
                    code == 401 -> throw RelayException("टोकन ग़लत है — सेटिंग में APP_TOKEN जाँचिए।")
                    code == 503 && text.contains("server_not_configured") ->
                        throw RelayException("Relay पर APP_TOKEN सेट नहीं है (Render के environment में डालिए)।")
                    code == 502 || code == 503 || code == 504 -> { /* waking up: retry */ }
                    else -> throw RelayException("Relay ने त्रुटि दी ($code)।")
                }
            } catch (e: IOException) {
                /* network hiccup or cold start: retry */
            }
            if (attempt == 5) break
            onWaking(true)
            delay(wait)
            wait = minOf(wait * 2, 20000L)
        }
        onWaking(false)
        throw RelayException("Relay जाग नहीं पाया। इंटरनेट और Render की स्थिति जाँचिए, फिर कोशिश कीजिए।")
    }
}

fun formatBrain(j: JSONObject): String {
    val sb = StringBuilder()
    val provs = j.getJSONObject("providers")
    provs.keys().forEach { name ->
        val p = provs.getJSONObject(name)
        sb.append("${name.uppercase()}  keys:${p.getInt("configured_keys")}  breaker:${p.getString("breaker")}\n")
        val ks = p.getJSONArray("keys")
        for (i in 0 until ks.length()) {
            val k = ks.getJSONObject(i)
            val st = if (k.getString("state") == "cooling") "cooling ${k.getInt("cooling_for_s")}s" else "healthy"
            sb.append("  ${k.getString("key")}  $st  req:${k.getInt("req_today")} ok:${k.getInt("ok_today")}")
            val le = k.optString("last_error", "")
            if (le.isNotEmpty()) sb.append("  ($le)")
            sb.append("\n")
        }
        sb.append("\n")
    }
    sb.append("अभी कौन-सा मॉडल:\n")
    val chars = j.getJSONObject("characters")
    chars.keys().forEach { c ->
        val o = chars.getJSONObject(c)
        sb.append("  $c → ${o.optString("model_now", "—").ifEmpty { "—" }}  (${o.getString("tier")})\n")
    }
    return sb.toString()
}
