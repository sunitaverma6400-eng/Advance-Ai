package com.stark.family

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

class MainActivity : ComponentActivity() {
    private val vm: ChatViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { StarkTheme(vm.character) { ChatScreen(vm) } }
    }
}

@Composable
fun StarkTheme(ch: Character, content: @Composable () -> Unit) {
    val scheme = darkColorScheme(
        primary = ch.accent,
        secondary = ch.accent2,
        background = Color(0xFF080C14),
        surface = Color(0xFF101828),
        onPrimary = Color(0xFF05080F),
        onBackground = Color(0xFFE6EDF7),
        onSurface = Color(0xFFE6EDF7),
    )
    MaterialTheme(colorScheme = scheme, content = content)
}

@Composable
fun ChatScreen(vm: ChatViewModel) {
    val ch = vm.character
    val msgs = vm.messages()
    val listState = rememberLazyListState()
    var input by remember { mutableStateOf("") }

    LaunchedEffect(msgs.size, vm.busy) {
        if (msgs.isNotEmpty()) listState.animateScrollToItem(msgs.lastIndex)
    }

    Column(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                "स्टार्क परिवार", color = ch.accent, fontSize = 20.sp,
                fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f),
            )
            TextButton(onClick = { vm.openBrain() }) { Text("🧠 दिमाग़", color = ch.accent2) }
            TextButton(onClick = { vm.showSettings = true }) { Text("⚙", color = ch.accent2, fontSize = 20.sp) }
        }

        Row(Modifier.padding(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Characters.all.forEach { c ->
                val sel = c.id == ch.id
                val shape = RoundedCornerShape(20.dp)
                Box(
                    Modifier.clip(shape)
                        .background(if (sel) c.accent.copy(alpha = 0.25f) else Color.Transparent)
                        .border(1.dp, c.accent.copy(alpha = if (sel) 1f else 0.4f), shape)
                        .clickable { vm.select(c.id) }
                        .padding(horizontal = 14.dp, vertical = 8.dp),
                ) { Text(c.name, color = c.accent) }
            }
        }
        Spacer(Modifier.height(8.dp))

        LazyColumn(
            state = listState,
            modifier = Modifier.weight(1f).fillMaxWidth().padding(horizontal = 12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            items(msgs) { m -> Bubble(m, ch) }
        }

        if (vm.waking) {
            val t = rememberInfiniteTransition(label = "wake")
            val a by t.animateFloat(
                initialValue = 0.3f, targetValue = 1f,
                animationSpec = infiniteRepeatable(tween(900), RepeatMode.Reverse), label = "alpha",
            )
            Text(
                "⚡ Relay जाग रहा है… थोड़ा रुकिए", color = ch.accent.copy(alpha = a),
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
            )
        } else if (vm.busy) {
            Text(
                "${ch.name} सोच रहा है…", color = ch.accent2,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
            )
        }

        Row(Modifier.fillMaxWidth().padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(
                value = input, onValueChange = { input = it },
                modifier = Modifier.weight(1f),
                placeholder = { Text("${ch.name} से बात करें…") },
                maxLines = 4,
            )
            Spacer(Modifier.width(8.dp))
            Button(
                onClick = { vm.send(input); input = "" },
                enabled = !vm.busy && input.isNotBlank(),
            ) { Text("भेजें") }
        }
    }

    if (vm.showSettings) SettingsDialog(vm)
    vm.brainText?.let { BrainDialog(it) { vm.brainText = null } }
}

@Composable
fun Bubble(m: Msg, ch: Character) {
    val isUser = m.kind == Kind.USER
    val bg = when (m.kind) {
        Kind.USER -> ch.accent.copy(alpha = 0.22f)
        Kind.AI -> Color(0xFF16213A)
        Kind.NOTE -> Color(0xFF2A1F26)
    }
    Column(
        Modifier.fillMaxWidth(),
        horizontalAlignment = if (isUser) Alignment.End else Alignment.Start,
    ) {
        Box(Modifier.widthIn(max = 320.dp).clip(RoundedCornerShape(16.dp)).background(bg).padding(12.dp)) {
            Text(
                m.text, fontSize = 16.sp, lineHeight = 24.sp,
                color = if (m.kind == Kind.NOTE) Color(0xFFE8B4B4) else MaterialTheme.colorScheme.onBackground,
            )
        }
        if (m.meta.isNotEmpty()) {
            Text(
                m.meta, color = Color(0xFF6B7A90), fontSize = 11.sp,
                modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
            )
        }
    }
}

@Composable
fun SettingsDialog(vm: ChatViewModel) {
    var url by remember { mutableStateOf(vm.relayUrl) }
    var tok by remember { mutableStateOf(vm.token) }
    AlertDialog(
        onDismissRequest = { vm.showSettings = false },
        title = { Text("Relay सेटिंग") },
        text = {
            Column {
                Text("Render पर बने relay का पता और APP_TOKEN डालिए। API keys ऐप में कभी नहीं रहतीं।", fontSize = 13.sp)
                Spacer(Modifier.height(12.dp))
                OutlinedTextField(
                    value = url, onValueChange = { url = it },
                    label = { Text("Relay URL (https://…)") }, singleLine = true,
                )
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(
                    value = tok, onValueChange = { tok = it },
                    label = { Text("APP_TOKEN") }, singleLine = true,
                    visualTransformation = PasswordVisualTransformation(),
                )
            }
        },
        confirmButton = { TextButton(onClick = { vm.saveSettings(url, tok) }) { Text("सेव") } },
        dismissButton = { TextButton(onClick = { vm.showSettings = false }) { Text("बंद") } },
    )
}

@Composable
fun BrainDialog(text: String, onClose: () -> Unit) {
    AlertDialog(
        onDismissRequest = onClose,
        title = { Text("दिमाग़ की स्थिति (Brain Status)") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                Text(text, fontFamily = FontFamily.Monospace, fontSize = 12.sp, lineHeight = 18.sp)
            }
        },
        confirmButton = { TextButton(onClick = onClose) { Text("ठीक है") } },
    )
}
