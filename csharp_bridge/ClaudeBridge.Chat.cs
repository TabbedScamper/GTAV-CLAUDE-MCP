using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Threading;

namespace ClaudeBridge
{
    /// <summary>
    /// In-game chat plumbing so a player can command Claude from inside GTA — WITHOUT PyLoaderV.
    ///
    /// Flow:  F10 panel -> EnqueueUserMessage()  (in-process when the panel ships in this same DLL, or via the
    ///        queue_user_message command for testing)  ->  the PC-side host pulls with get_pending_messages  ->
    ///        Claude (Agent SDK, your subscription)  ->  chat_post  ->  transcript  ->  the panel renders it.
    ///
    /// The transcript lives here as plain C# state, so when the LemonUI panel is merged into this DLL it reads it
    /// in-process (no shared-memory IPC needed). Everything is thread-safe (socket threads + game thread + panel).
    /// </summary>
    public partial class ClaudeBridge
    {
        public sealed class ChatLine { public string role; public string text; public string time; }

        static readonly ConcurrentQueue<string> _userMessages = new ConcurrentQueue<string>();  // F10 msgs awaiting host pull
        static readonly List<ChatLine> _transcript = new List<ChatLine>();                       // rendered by the panel
        static readonly object _txLock = new object();
        const int TRANSCRIPT_MAX = 400;

        static volatile string _overlayText = "";
        static volatile string _overlayState = "";   // "searching" | "ok" | "error" | ...

        static string Now() { return DateTime.Now.ToString("HH:mm:ss"); }

        // ---- in-process API (used by the merged F10 panel) ----
        public static void EnqueueUserMessage(string text)
        {
            if (string.IsNullOrEmpty(text)) return;
            _userMessages.Enqueue(text);
            AddLine("user", text);
        }
        public static void PostAssistant(string text) { AddLine("assistant", text); }
        public static IReadOnlyList<ChatLine> TranscriptSnapshot(int max)
        {
            lock (_txLock)
            {
                int n = _transcript.Count;
                int take = (max <= 0 || max > n) ? n : max;
                return _transcript.GetRange(n - take, take).ToList();
            }
        }
        public static string OverlayText => _overlayText;
        public static string OverlayState => _overlayState;

        static void AddLine(string role, string text)
        {
            lock (_txLock)
            {
                _transcript.Add(new ChatLine { role = role, text = text ?? "", time = Now() });
                if (_transcript.Count > TRANSCRIPT_MAX) _transcript.RemoveRange(0, _transcript.Count - TRANSCRIPT_MAX);
            }
        }

        // ---- commands ----
        object QueueUserMessage(Dictionary<string, object> p) { EnqueueUserMessage(Str(p, "text") ?? Str(p, "message")); return new Dictionary<string, object> { ["success"] = true }; }

        object GetPendingMessages(Dictionary<string, object> p)
        {
            var msgs = new List<string>();
            while (_userMessages.TryDequeue(out var m)) msgs.Add(m);
            return new Dictionary<string, object> { ["messages"] = msgs, ["count"] = msgs.Count };
        }

        object HasPendingMessages(Dictionary<string, object> p)
            => new Dictionary<string, object> { ["has_messages"] = !_userMessages.IsEmpty, ["count"] = _userMessages.Count };

        // Block this socket thread until a message arrives or timeout (the host long-polls this).
        object AwaitUserMessage(Dictionary<string, object> p)
        {
            int timeoutMs = p != null && p.TryGetValue("timeout_ms", out var t) ? ToInt(t)
                          : (p != null && p.TryGetValue("timeout_seconds", out var ts) ? ToInt(ts) * 1000 : 30000);
            var sw = Stopwatch.StartNew();
            while (sw.ElapsedMilliseconds < timeoutMs)
            {
                if (_userMessages.TryDequeue(out var m))
                    return new Dictionary<string, object> { ["message"] = m, ["timed_out"] = false };
                Thread.Sleep(50);
            }
            return new Dictionary<string, object> { ["message"] = null, ["timed_out"] = true };
        }

        object ChatPost(Dictionary<string, object> p) { PostAssistant(Str(p, "message") ?? Str(p, "text")); return new Dictionary<string, object> { ["success"] = true }; }

        object HudMessage(Dictionary<string, object> p) { AddLine("system", Str(p, "message") ?? Str(p, "text")); return new Dictionary<string, object> { ["success"] = true }; }

        object SetOverlay(Dictionary<string, object> p)
        {
            _overlayText = Str(p, "text", "") ?? "";
            _overlayState = Str(p, "state", "searching") ?? "searching";
            return new Dictionary<string, object> { ["success"] = true };
        }

        object GetChatHistory(Dictionary<string, object> p)
        {
            int limit = p != null && p.TryGetValue("limit", out var l) ? ToInt(l) : 50;
            var lines = TranscriptSnapshot(limit)
                .Select(c => (object)new Dictionary<string, object> { ["role"] = c.role, ["text"] = c.text, ["time"] = c.time })
                .ToList();
            return new Dictionary<string, object> { ["history"] = lines, ["count"] = lines.Count };
        }

        // Lightweight health check (no natives -> safe even while the game is paused). The host calls this.
        object StatusInfo(Dictionary<string, object> p)
        {
            string edition = "Unknown";
            try { edition = Process.GetCurrentProcess().MainModule.ModuleName.ToLowerInvariant().Contains("enhanced") ? "Enhanced" : "Legacy"; } catch { }
            return new Dictionary<string, object>
            {
                ["ok"] = true,
                ["bridge"] = "ClaudeBridge",
                ["edition"] = edition,
                ["pending_user_messages"] = _userMessages.Count,
                ["transcript_lines"] = _transcript.Count,
                ["profiler_patched"] = _patched,
            };
        }
    }
}
