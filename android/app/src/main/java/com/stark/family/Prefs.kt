package com.stark.family

import android.content.Context

/** Only the relay address and shared token live here. API keys never touch the app. */
class Prefs(ctx: Context) {
    private val sp = ctx.getSharedPreferences("stark_prefs", Context.MODE_PRIVATE)

    var relayUrl: String
        get() = sp.getString("relay_url", "") ?: ""
        set(v) { sp.edit().putString("relay_url", v.trim()).apply() }

    var token: String
        get() = sp.getString("token", "") ?: ""
        set(v) { sp.edit().putString("token", v.trim()).apply() }
}
