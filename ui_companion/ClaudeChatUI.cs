using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.MemoryMappedFiles;
using System.Text;
using System.Windows.Forms;
using GTA;
using GTA.UI;
using LemonUI.Elements;
using Font = GTA.UI.Font;

namespace ClaudeChatUI
{
    // In-game terminal mirror + chat input.
    // Rendering rebuilt on LemonUI (ScaledText/ScaledRectangle): automatic
    // resolution/aspect scaling, Monospace font, outline, a real scrollbar,
    // and a faster refresh than the old raw-native draw.
    public class Main : Script
    {
        private readonly string msgDir;
        private readonly string userMsgFile;
        private readonly string chatHistoryFile;

        private bool terminalVisible = false;   // F11 toggles
        private int terminalScrollOffset = 0;    // 0 = bottom (newest); negative = scrolled up
        private int loadTimer = 0;

        // Shared memory mirror of the Claude Code terminal
        private const string SHARED_MEM_NAME = "GTAV_Claude_Console";
        private MemoryMappedFile sharedMem;
        private MemoryMappedViewAccessor sharedMemAccessor;
        private string[] terminalLines = new string[0];

        // Resolved at runtime from %LOCALAPPDATA%\GTAV-Claude-MCP\host_path.txt, which
        // gtav_host.py writes on first run (portable - no hardcoded per-machine path).
        private string hostBatPath;

        // ---- Layout (1080p reference px; LemonUI scales to the real resolution) ----
        private const int VISIBLE_LINES = 30;
        private const float LINE_PITCH = 24f;
        private const float TEXT_SCALE = 0.34f;
        private const float PANEL_W = 700f;
        private const float HEADER_H = 36f;
        private const float FOOTER_H = 30f;
        private const float PAD = 12f;
        private const int WRAP_CHARS = 64;       // monospace -> char-count wrap is accurate

        // ---- Reused LemonUI elements (created once, repositioned each frame) ----
        private ScaledRectangle bg, header, footer, scrollTrack, scrollThumb;
        private ScaledText title, footerText, empty;
        private ScaledText[] lineTexts;

        public Main()
        {
            msgDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "GTAV-Claude-MCP");
            userMsgFile = Path.Combine(msgDir, "user_message.txt");
            chatHistoryFile = Path.Combine(msgDir, "chat_history.txt");
            Directory.CreateDirectory(msgDir);

            BuildUI();
            AutoLaunchClaude();

            try
            {
                sharedMem = MemoryMappedFile.OpenExisting(SHARED_MEM_NAME);
                sharedMemAccessor = sharedMem.CreateViewAccessor();
            }
            catch { }

            Tick += OnTick;
            KeyDown += OnKeyDown;
        }

        private void BuildUI()
        {
            bg          = new ScaledRectangle(PointF.Empty, SizeF.Empty) { Color = Color.FromArgb(225, 12, 14, 20) };
            header      = new ScaledRectangle(PointF.Empty, SizeF.Empty) { Color = Color.FromArgb(245, 92, 56, 138) };
            footer      = new ScaledRectangle(PointF.Empty, SizeF.Empty) { Color = Color.FromArgb(205, 22, 24, 34) };
            scrollTrack = new ScaledRectangle(PointF.Empty, SizeF.Empty) { Color = Color.FromArgb(120, 70, 70, 95) };
            scrollThumb = new ScaledRectangle(PointF.Empty, SizeF.Empty) { Color = Color.FromArgb(235, 150, 130, 210) };

            title = new ScaledText(PointF.Empty, "Claude Terminal", 0.4f, Font.ChaletLondon)
            { Color = Color.White, Outline = true };
            footerText = new ScaledText(PointF.Empty, "F10: chat   F11: close   PgUp/PgDn: scroll", 0.3f, Font.ChaletLondon)
            { Color = Color.FromArgb(255, 165, 165, 195), Outline = false };
            empty = new ScaledText(PointF.Empty, "Waiting for terminal...", 0.34f, Font.ChaletLondon)
            { Color = Color.Gray, Alignment = Alignment.Center };

            lineTexts = new ScaledText[VISIBLE_LINES];
            for (int i = 0; i < VISIBLE_LINES; i++)
            {
                lineTexts[i] = new ScaledText(PointF.Empty, "", TEXT_SCALE, Font.Monospace)
                {
                    Color = Color.FromArgb(255, 210, 210, 210),
                    Outline = true,
                    Alignment = Alignment.Left,
                };
            }
        }

        private void AutoLaunchClaude()
        {
            try
            {
                // Find the host launcher path that gtav_host.py self-registered.
                string cfg = Path.Combine(msgDir, "host_path.txt");
                if (File.Exists(cfg))
                    hostBatPath = File.ReadAllText(cfg).Trim();

                if (string.IsNullOrWhiteSpace(hostBatPath) || !File.Exists(hostBatPath))
                {
                    Notification.Show("~y~Run run_host.bat once to enable Claude auto-launch");
                    return;
                }

                // Already running? (python process started from our bat keeps the host alive)
                if (Process.GetProcessesByName("python").Length > 0 ||
                    Process.GetProcessesByName("ConsoleTrigger").Length > 0)
                    return;

                Process.Start(new ProcessStartInfo
                {
                    FileName = hostBatPath,
                    WorkingDirectory = Path.GetDirectoryName(hostBatPath),
                    UseShellExecute = true,
                    WindowStyle = ProcessWindowStyle.Minimized,  // never steal focus from the game
                });
                Notification.Show("~g~Claude host launched");
            }
            catch (Exception ex)
            {
                Notification.Show("~r~Failed to launch Claude host: " + ex.Message);
            }
        }

        private void OnTick(object sender, EventArgs e)
        {
            // Throttle the (relatively expensive) shared-memory re-read; draw every frame.
            if (++loadTimer > 8)   // ~130ms at 60fps (was ~500ms)
            {
                loadTimer = 0;
                LoadTerminal();
            }
            if (terminalVisible)
                DrawTerminal();
        }

        private void LoadTerminal()
        {
            try
            {
                if (sharedMemAccessor == null)
                {
                    try
                    {
                        sharedMem = MemoryMappedFile.OpenExisting(SHARED_MEM_NAME);
                        sharedMemAccessor = sharedMem.CreateViewAccessor();
                    }
                    catch { return; }
                }
                int len = sharedMemAccessor.ReadInt32(0);
                if (len <= 0 || len > 60000) return;
                byte[] data = new byte[len];
                sharedMemAccessor.ReadArray(4, data, 0, len);
                string content = Encoding.UTF8.GetString(data);
                terminalLines = content.Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries);
            }
            catch { }
        }

        private void DrawTerminal()
        {
            float vw = 1080f * GTA.UI.Screen.AspectRatio;     // virtual width (≈1920 at 16:9)
            float panelH = HEADER_H + VISIBLE_LINES * LINE_PITCH + FOOTER_H + 2 * PAD;
            float x = vw - PANEL_W - 24f;                     // right-anchored
            float y = 80f;

            bg.Position = new PointF(x, y); bg.Size = new SizeF(PANEL_W, panelH); bg.Draw();
            header.Position = new PointF(x, y); header.Size = new SizeF(PANEL_W, HEADER_H); header.Draw();
            title.Position = new PointF(x + PAD, y + 7f); title.Draw();

            // Wrap raw lines into display lines (monospace -> char-count wrap is accurate)
            var display = new List<string>();
            foreach (var raw in terminalLines)
                foreach (var w in WrapText(raw ?? "", WRAP_CHARS))
                    display.Add(w);

            int total = display.Count;
            float contentX = x + PAD;
            float contentY = y + HEADER_H + PAD;

            if (total == 0)
            {
                empty.Position = new PointF(x + PANEL_W / 2f, y + panelH / 2f);
                empty.Draw();
            }
            else
            {
                int firstRow = Math.Max(0, total - VISIBLE_LINES + terminalScrollOffset);
                if (firstRow > Math.Max(0, total - 1)) firstRow = Math.Max(0, total - 1);
                for (int i = 0; i < VISIBLE_LINES; i++)
                {
                    int idx = firstRow + i;
                    if (idx >= total) break;
                    var t = lineTexts[i];
                    t.Text = display[idx];
                    t.Color = LineColor(display[idx]);
                    t.Position = new PointF(contentX, contentY + i * LINE_PITCH);
                    t.Draw();
                }

                // Scrollbar (track + thumb) when content overflows
                if (total > VISIBLE_LINES)
                {
                    float trackX = x + PANEL_W - 8f;
                    float trackY = contentY;
                    float trackH = VISIBLE_LINES * LINE_PITCH;
                    scrollTrack.Position = new PointF(trackX, trackY); scrollTrack.Size = new SizeF(4f, trackH); scrollTrack.Draw();
                    float thumbH = Math.Max(22f, trackH * VISIBLE_LINES / total);
                    float scrollFrac = firstRow / (float)Math.Max(1, total - VISIBLE_LINES);
                    float thumbY = trackY + scrollFrac * (trackH - thumbH);
                    scrollThumb.Position = new PointF(trackX, thumbY); scrollThumb.Size = new SizeF(4f, thumbH); scrollThumb.Draw();
                }
            }

            footer.Position = new PointF(x, y + panelH - FOOTER_H); footer.Size = new SizeF(PANEL_W, FOOTER_H); footer.Draw();
            footerText.Position = new PointF(x + PAD, y + panelH - FOOTER_H + 5f); footerText.Draw();
        }

        // Color-code by line type (ported heuristic)
        private static Color LineColor(string line)
        {
            string low = line.ToLowerInvariant();
            string trimmed = line.TrimStart();
            if (line.StartsWith(">") || line.StartsWith("$"))
                return Color.FromArgb(255, 130, 200, 255);   // user input - blue
            if (low.Contains("error") || low.Contains("failed") || low.Contains("warning"))
                return Color.FromArgb(255, 255, 120, 120);   // red
            if (low.Contains("done") || low.Contains("success") || low.Contains("works") || low.Contains("fixed"))
                return Color.FromArgb(255, 120, 255, 150);   // green
            if (line.EndsWith(":") && line.Length < 30)
                return Color.FromArgb(255, 255, 220, 100);   // header - yellow
            if (low.Contains("try ") || low.Contains("check ") || low.Contains("press ") || low.Contains("reload") || low.Contains("test"))
                return Color.FromArgb(255, 255, 180, 100);   // action - orange
            if (trimmed.StartsWith("-") || trimmed.StartsWith("*"))
                return Color.FromArgb(255, 180, 220, 255);   // bullet - light blue
            return Color.FromArgb(255, 210, 210, 210);       // default - light gray
        }

        private static List<string> WrapText(string text, int maxChars)
        {
            var lines = new List<string>();
            if (text.Length == 0) { lines.Add(""); return lines; }
            while (text.Length > maxChars)
            {
                int splitAt = text.LastIndexOf(' ', Math.Min(maxChars, text.Length - 1));
                if (splitAt <= 0) splitAt = maxChars;
                lines.Add(text.Substring(0, splitAt));
                text = text.Substring(splitAt).TrimStart();
            }
            if (text.Length > 0) lines.Add(text);
            return lines;
        }

        private void OnKeyDown(object sender, KeyEventArgs e)
        {
            if (e.KeyCode == Keys.F11)
            {
                terminalVisible = !terminalVisible;
                if (terminalVisible) LoadTerminal();
            }
            else if (e.KeyCode == Keys.F10)
            {
                SendMessage();
            }
            else if (terminalVisible && e.KeyCode == Keys.PageUp)
            {
                terminalScrollOffset = Math.Max(terminalScrollOffset - 5, -2000);
            }
            else if (terminalVisible && e.KeyCode == Keys.PageDown)
            {
                terminalScrollOffset = Math.Min(terminalScrollOffset + 5, 0);
            }
        }

        private void SendMessage()
        {
            var input = Game.GetUserInput(WindowTitle.EnterMessage60, "", 256);
            if (!string.IsNullOrWhiteSpace(input))
            {
                try
                {
                    File.WriteAllText(userMsgFile, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "|" + input);
                    File.AppendAllText(chatHistoryFile, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "|You|" + input + "\n");
                    Notification.Show("~g~Sent to Claude");
                }
                catch
                {
                    Notification.Show("~r~Failed");
                }
            }
        }
    }
}
