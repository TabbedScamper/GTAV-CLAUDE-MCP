using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Windows.Forms;
using GTA;
using GTA.UI;
using LemonUI.Elements;
using Font = GTA.UI.Font;

namespace ClaudeBridge
{
    /// <summary>
    /// In-game chat panel — a SECOND SHVDN Script shipped in the same DLL as the bridge, so it reads the chat
    /// transcript straight from ClaudeBridge's in-process state (no shared memory, no files, no PyLoaderV).
    ///   F11 toggles the panel · F10 opens the on-screen keyboard to message Claude · PgUp/PgDn scroll.
    /// The PC-side host pulls F10 messages via get_pending_messages and posts replies via chat_post.
    /// </summary>
    public class ClaudeChatPanel : Script
    {
        bool _visible = false;
        int _scroll = 0;          // 0 = newest at bottom; negative = scrolled up
        int _reloadTimer = 0;
        int _lastTxLen = -1;

        // Configurable keys (default F8 toggle / F9 chat) — F10/F11/F12 collide with GPU/Steam overlays.
        Keys _toggleKey = Keys.F8, _chatKey = Keys.F9;
        // Socket-driven visibility so the panel can be shown without a keypress: -1 no-op, 0 hide, 1 show.
        internal static volatile int CmdVisible = -1;

        const int VISIBLE_LINES = 30;
        const float LINE_PITCH = 24f, TEXT_SCALE = 0.34f, PANEL_W = 700f, HEADER_H = 36f, FOOTER_H = 30f, PAD = 12f;
        const int WRAP_CHARS = 58;

        ScaledRectangle _bg, _header, _footer, _track, _thumb;
        ScaledText _title, _footerText, _empty;
        ScaledText[] _lines;
        readonly List<string> _display = new List<string>();   // wrapped, color-tagged display lines

        public ClaudeChatPanel()
        {
            LoadConfig();
            BuildUI();
            Tick += OnTick;
            KeyDown += OnKeyDown;
        }

        // Read scripts\ClaudeBridge.ini [Keys] TogglePanel / Chat (creates it with defaults if missing).
        void LoadConfig()
        {
            try
            {
                string path = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "scripts", "ClaudeBridge.ini");
                if (!File.Exists(path))
                    File.WriteAllText(path,
                        "[Keys]\n; .NET key names (F1..F12, Insert, Home, OemTilde, NumPad0, ...).\n" +
                        "; Avoid keys your GPU/Steam/Windows overlay uses (F10/F11/F12 often collide).\n" +
                        "TogglePanel=F8\nChat=F9\n");
                foreach (var raw in File.ReadAllLines(path))
                {
                    var line = raw.Trim();
                    if (line.Length == 0 || line.StartsWith(";") || line.StartsWith("[") || !line.Contains("=")) continue;
                    var kv = line.Split(new[] { '=' }, 2);
                    string k = kv[0].Trim(), v = kv[1].Trim();
                    if (k.Equals("TogglePanel", StringComparison.OrdinalIgnoreCase) && Enum.TryParse<Keys>(v, true, out var t)) _toggleKey = t;
                    else if (k.Equals("Chat", StringComparison.OrdinalIgnoreCase) && Enum.TryParse<Keys>(v, true, out var c)) _chatKey = c;
                }
            }
            catch { }
        }

        void BuildUI()
        {
            _bg     = new ScaledRectangle(PointF.Empty, SizeF.Empty) { Color = Color.FromArgb(225, 12, 14, 20) };
            _header = new ScaledRectangle(PointF.Empty, SizeF.Empty) { Color = Color.FromArgb(245, 92, 56, 138) };
            _footer = new ScaledRectangle(PointF.Empty, SizeF.Empty) { Color = Color.FromArgb(205, 22, 24, 34) };
            _track  = new ScaledRectangle(PointF.Empty, SizeF.Empty) { Color = Color.FromArgb(120, 70, 70, 95) };
            _thumb  = new ScaledRectangle(PointF.Empty, SizeF.Empty) { Color = Color.FromArgb(235, 150, 130, 210) };
            _title  = new ScaledText(PointF.Empty, "Claude", 0.4f, Font.ChaletLondon) { Color = Color.White, Outline = true };
            _footerText = new ScaledText(PointF.Empty, $"{_chatKey}: chat   {_toggleKey}: close   PgUp/PgDn: scroll", 0.3f, Font.ChaletLondon)
            { Color = Color.FromArgb(255, 165, 165, 195) };
            _empty = new ScaledText(PointF.Empty, "Press F10 to talk to Claude.", 0.34f, Font.ChaletLondon)
            { Color = Color.Gray, Alignment = Alignment.Center };
            _lines = new ScaledText[VISIBLE_LINES];
            for (int i = 0; i < VISIBLE_LINES; i++)
                _lines[i] = new ScaledText(PointF.Empty, "", TEXT_SCALE, Font.ChaletLondon) { Color = Color.FromArgb(255, 210, 210, 210), Outline = true };
        }

        void OnTick(object sender, EventArgs e)
        {
            // Apply any socket-driven show/hide (panel command) on the game thread.
            int cv = CmdVisible;
            if (cv >= 0) { CmdVisible = -1; _visible = cv == 1; if (_visible) Rebuild(); }

            if (!_visible) return;
            if (++_reloadTimer > 8) { _reloadTimer = 0; Rebuild(); }   // ~130ms
            Draw();
        }

        // Pull the transcript from the bridge (in-process) and turn it into wrapped, prefixed display lines.
        void Rebuild()
        {
            var tx = ClaudeBridge.TranscriptSnapshot(0);
            if (tx.Count == _lastTxLen) return;   // nothing new
            _lastTxLen = tx.Count;
            _display.Clear();
            foreach (var line in tx)
            {
                string who = line.role == "user" ? ">" : (line.role == "assistant" ? "Claude:" : "·");
                foreach (var w in Wrap(AsciiFold(who + " " + line.text), WRAP_CHARS)) _display.Add(w);
            }
        }

        void Draw()
        {
            float vw = 1080f * GTA.UI.Screen.AspectRatio;
            float panelH = HEADER_H + VISIBLE_LINES * LINE_PITCH + FOOTER_H + 2 * PAD;
            float x = vw - PANEL_W - 24f, y = 80f;
            _bg.Position = new PointF(x, y); _bg.Size = new SizeF(PANEL_W, panelH); _bg.Draw();
            _header.Position = new PointF(x, y); _header.Size = new SizeF(PANEL_W, HEADER_H); _header.Draw();
            _title.Position = new PointF(x + PAD, y + 7f); _title.Draw();

            int total = _display.Count;
            float cx = x + PAD, cy = y + HEADER_H + PAD;
            if (total == 0) { _empty.Position = new PointF(x + PANEL_W / 2f, y + panelH / 2f); _empty.Draw(); }
            else
            {
                int first = Math.Max(0, total - VISIBLE_LINES + _scroll);
                if (first > Math.Max(0, total - 1)) first = Math.Max(0, total - 1);
                for (int i = 0; i < VISIBLE_LINES; i++)
                {
                    int idx = first + i; if (idx >= total) break;
                    var t = _lines[i]; t.Text = _display[idx]; t.Color = LineColor(_display[idx]);
                    t.Position = new PointF(cx, cy + i * LINE_PITCH); t.Draw();
                }
                if (total > VISIBLE_LINES)
                {
                    float trackX = x + PANEL_W - 8f, trackH = VISIBLE_LINES * LINE_PITCH;
                    _track.Position = new PointF(trackX, cy); _track.Size = new SizeF(4f, trackH); _track.Draw();
                    float thumbH = Math.Max(22f, trackH * VISIBLE_LINES / total);
                    float frac = first / (float)Math.Max(1, total - VISIBLE_LINES);
                    _thumb.Position = new PointF(trackX, cy + frac * (trackH - thumbH)); _thumb.Size = new SizeF(4f, thumbH); _thumb.Draw();
                }
            }
            _footer.Position = new PointF(x, y + panelH - FOOTER_H); _footer.Size = new SizeF(PANEL_W, FOOTER_H); _footer.Draw();
            _footerText.Position = new PointF(x + PAD, y + panelH - FOOTER_H + 5f); _footerText.Draw();
        }

        void OnKeyDown(object sender, KeyEventArgs e)
        {
            if (e.KeyCode == _toggleKey) { _visible = !_visible; if (_visible) Rebuild(); }
            else if (e.KeyCode == _chatKey)
            {
                var input = Game.GetUserInput(WindowTitle.EnterMessage60, "", 240);
                if (!string.IsNullOrWhiteSpace(input))
                {
                    ClaudeBridge.EnqueueUserMessage(input);   // in-process -> host pulls via get_pending_messages
                    if (!_visible) _visible = true;
                    Rebuild();
                    Notification.Show("~g~Sent to Claude");
                }
            }
            else if (_visible && e.KeyCode == Keys.PageUp) _scroll = Math.Max(_scroll - 5, -4000);
            else if (_visible && e.KeyCode == Keys.PageDown) _scroll = Math.Min(_scroll + 5, 0);
        }

        static Color LineColor(string line)
        {
            string low = line.ToLowerInvariant();
            if (line.StartsWith(">")) return Color.FromArgb(255, 130, 200, 255);          // you - blue
            if (line.StartsWith("Claude")) return Color.FromArgb(255, 180, 255, 190);     // claude - green
            if (low.Contains("error") || low.Contains("failed")) return Color.FromArgb(255, 255, 120, 120);
            return Color.FromArgb(255, 215, 215, 215);
        }

        static List<string> Wrap(string text, int max)
        {
            var outp = new List<string>();
            if (string.IsNullOrEmpty(text)) { outp.Add(""); return outp; }
            while (text.Length > max)
            {
                int at = text.LastIndexOf(' ', Math.Min(max, text.Length - 1));
                if (at <= 0) at = max;
                outp.Add(text.Substring(0, at)); text = text.Substring(at).TrimStart();
            }
            if (text.Length > 0) outp.Add(text);
            return outp;
        }

        // GTA fonts only render ~ASCII; fold smart punctuation/arrows/etc. so nothing draws as a box.
        static string AsciiFold(string s)
        {
            if (string.IsNullOrEmpty(s)) return s;
            var sb = new System.Text.StringBuilder(s.Length);
            foreach (char ch in s.Normalize(System.Text.NormalizationForm.FormKD))
            {
                int c = ch;
                if (c < 128) { sb.Append(ch); continue; }
                switch (c)
                {
                    case 0x2018: case 0x2019: sb.Append('\''); break;
                    case 0x201C: case 0x201D: sb.Append('"'); break;
                    case 0x2013: case 0x2014: case 0x2212: sb.Append('-'); break;
                    case 0x2026: sb.Append("..."); break;
                    case 0x2022: case 0x00B7: sb.Append('-'); break;
                    case 0x2192: sb.Append("->"); break;
                    default: break;   // drop emoji / unrenderable
                }
            }
            return sb.ToString();
        }
    }
}
