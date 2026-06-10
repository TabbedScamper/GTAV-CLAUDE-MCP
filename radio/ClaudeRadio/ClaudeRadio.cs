using System;
using System.Collections.Generic;
using System.IO;
using GTA;
using GTA.Math;
using GTA.Native;
using NAudio.Wave;
using NAudio.Wave.SampleProviders;
using NAudio.Dsp;
using Newtonsoft.Json.Linq;

namespace ClaudeRadio
{
    /// <summary>
    /// Claude Radio (Phase 1) - plays local audio files in-game via NAudio, driven by the Python
    /// backend (mcp_server/radio.py) through a file command channel. While a track plays in a
    /// vehicle, the GTA radio is set to OFF so our audio is the only thing you hear ("soft station").
    /// No Spotify, no Premium, no DRM. See radio/SPEC.md.
    /// </summary>
    public class ClaudeRadioScript : Script
    {
        // Shared mod runtime dir (same one the host/UI use) - writable, native to the mod.
        private readonly string baseDir;
        private readonly string cmdFile;
        private readonly string statusFile;
        private readonly string logFile;
        private readonly string volFile;

        private IWavePlayer output;
        private AudioFileReader reader;
        private LowPassSampleProvider lowpass;   // muffle filter between reader and output
        // second slot for the 3-second crossfade: the incoming track plays here while the outgoing fades out
        private IWavePlayer outputB;
        private AudioFileReader readerB;
        private LowPassSampleProvider lowpassB;
        private bool crossfading;
        private int crossfadeStart;
        private string nextFile = "", nextTitle = "", nextArtist = "";
        private const int CROSSFADE_MS = 3000;
        private readonly Queue<string[]> queue = new Queue<string[]>(); // [file, title, artist]
        private string currentTitle = "";
        private string currentArtist = "";
        private string currentFile = "";
        private readonly Random rng = new Random();
        private bool stationMode = true;   // station: when a track ends, shuffle to the next library track
        private int songsSinceAd = 0;      // advert rotation: play an ad after this many songs (like GTA radio)
        private bool viaStation = false;   // true when activated by tuning the wheel to our CLAUDE FM station
        private string triggerStation;     // the DLC station's internal name we watch for (from config)
        private bool mutedAway = false;    // broadcast keeps rolling but muted (player tuned to another station)
        private bool onClaudeStation = false; // player's vehicle radio is (or last was, on foot) our station
        private float volume = 0.6f;       // calibration trim (loudness at MAX Music slider); persisted
        private float gameMusic01 = 1.0f;  // in-game Music Volume slider as 0..1 (GET_PROFILE_SETTING 301)
        private const int MUSIC_VOLUME_SETTING = 301;  // confirmed: profile setting 301 = Music Volume (0-10)

        // Positional/muffled audio: inside the car = full + clear; outside = quieter (distance falloff)
        // and low-pass "muffled"; a door open = louder + clearer; radio only audible while engine runs.
        private float posGain = 1.0f;          // 0..1 positional gain multiplier (smoothed)
        private float smoothedCutoff = 22000f; // low-pass cutoff Hz (high = bypass/clear, low = muffled)
        private Vehicle lastVehicle;           // last car the player was in (for the outside-the-car effect)
        // last-computed positional inputs (surfaced in radio_status.json for auto-diagnosis)
        private bool diagInside, diagEngineOn;
        private float diagDist = -1f, diagDoorOpen;
        private string savedStation;           // the in-game station to hand back to when the song ends
        private bool isPlaying;
        private bool isPaused;
        private bool manualStop;
        private volatile bool trackEndedNaturally;

        private int lastSeq = -1;
        private int pollCounter;
        private int lastStatusWrite;
        private int playStartedAt;   // GameTime when the current track started (grace for radio-OFF)

        // Freeze detection: OnTick (game thread) bumps heartbeat every frame. A background thread
        // watches it; if it stalls, the game is frozen (pause menu / loading) and SHVDN isn't ticking,
        // so we mute our audio (which keeps playing otherwise) - matching how the radio goes quiet.
        private volatile int heartbeat;
        private volatile bool gameFrozen;
        private volatile bool watcherRunning = true;
        private System.Threading.Thread freezeWatcher;

        public ClaudeRadioScript()
        {
            baseDir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "GTAV-Claude-MCP");
            Directory.CreateDirectory(baseDir);
            cmdFile = Path.Combine(baseDir, "radio_cmd.json");
            statusFile = Path.Combine(baseDir, "radio_status.json");
            logFile = Path.Combine(baseDir, "ClaudeRadio.log");
            volFile = Path.Combine(baseDir, "radio_volume.txt");

            // Persisted volume: set once to match your in-game radio and it sticks across sessions.
            try { if (File.Exists(volFile) && int.TryParse(File.ReadAllText(volFile).Trim(), out int v))
                      volume = Math.Max(0, Math.Min(100, v)) / 100f; } catch { }

            // Ignore any command that already existed before we started (don't replay on load).
            lastSeq = ReadSeq();
            Log("ClaudeRadio started. baseDir=" + baseDir + " lastSeq=" + lastSeq);

            freezeWatcher = new System.Threading.Thread(FreezeWatcher) { IsBackground = true };
            freezeWatcher.Start();

            Tick += OnTick;
            Aborted += (s, e) => { watcherRunning = false; StopInternal(); };
        }

        private void OnTick(object sender, EventArgs e)
        {
            heartbeat++;   // freeze-detection pulse (the background watcher reads this)
            if (++pollCounter > 6) { pollCounter = 0; PollCommand(); }   // ~100ms

            CrossfadeUpdate();   // ~3s before the end, blend into the next track; promote when done

            // Advance to the next track (only when NOT mid-crossfade - the crossfade promotes B itself).
            // Primary: NAudio's end-of-track event. Backstop: if that event is missed (WaveOut's
            // PlaybackStopped is flaky), detect end-of-file by stream position so the station never stalls.
            if (!crossfading)
            {
                if (trackEndedNaturally) { trackEndedNaturally = false; PlayNext(); }
                else if (isPlaying && !isPaused)
                {
                    var rd = reader;
                    try { if (rd != null && rd.Length > 0 && rd.Position >= rd.Length) { Log("track end (position backstop)"); PlayNext(); } } catch { }
                }
            }

            // Positional audio: the radio plays while the car's engine runs. Inside = full & clear;
            // outside = quieter with distance + low-pass "muffle"; a door open = louder & clearer.
            if (isPlaying)
            {
                var ped = Game.Player.Character;
                var cur = (ped != null) ? ped.CurrentVehicle : null;
                if (cur != null && cur.Exists()) lastVehicle = cur;
                bool inside = cur != null && cur.Exists();
                var veh = inside ? cur : ((lastVehicle != null && lastVehicle.Exists()) ? lastVehicle : null);

                // Yield to the player: if they tune to a REAL station (inside), stop Claude Radio
                // instead of fighting the wheel. Short grace so our own one-time radio-OFF isn't misread.
                // (Skip when viaStation: there the player is deliberately ON our station, so GET returns
                //  our station name, not OFF - the tune-away handling lives in CheckStationTrigger.)
                if (inside && !viaStation && Game.GameTime - playStartedAt > 1200)
                {
                    string st = Function.Call<string>(Hash.GET_PLAYER_RADIO_STATION_NAME);
                    if (!string.IsNullOrEmpty(st) && !st.Equals("OFF", StringComparison.OrdinalIgnoreCase))
                    {
                        Log("Player tuned to " + st + " - yielding (stopping Claude Radio).");
                        StopInternal();
                        savedStation = null;   // they picked their own station; don't override it on stop
                    }
                }

                UpdatePositional(veh, inside);
            }

            if (Game.GameTime - lastStatusWrite > 500)
            {
                lastStatusWrite = Game.GameTime;
                CheckStationTrigger();   // tune to CLAUDE FM -> start us; tune away -> stop us
                UpdateGameVolume();
                WriteStatus();
            }
        }

        // ---- command channel ----------------------------------------------------------------
        private int ReadSeq()
        {
            try
            {
                if (!File.Exists(cmdFile)) return -1;
                return (int)(JObject.Parse(SafeRead(cmdFile))["seq"] ?? -1);
            }
            catch { return lastSeq; }
        }

        private void PollCommand()
        {
            try
            {
                if (!File.Exists(cmdFile)) return;
                var j = JObject.Parse(SafeRead(cmdFile));
                int seq = (int)(j["seq"] ?? -1);
                if (seq <= lastSeq) return;
                lastSeq = seq;
                string cmd = (string)j["cmd"] ?? "";
                switch (cmd)
                {
                    case "play":
                        queue.Clear();
                        Play((string)j["file"], (string)j["title"] ?? "", (string)j["artist"] ?? "");
                        break;
                    case "queue":
                        queue.Enqueue(new[] { (string)j["file"], (string)j["title"] ?? "", (string)j["artist"] ?? "" });
                        if (!isPlaying) PlayNext();
                        break;
                    case "pause": PausePlayback(); break;
                    case "resume": ResumePlayback(); break;
                    case "skip": Skip(); break;
                    case "stop": StopInternal(); RestoreGameRadio(); viaStation = false; mutedAway = false; onClaudeStation = false; break;
                    case "volume": SetVolume((int)(j["volume"] ?? 100)); break;
                }
            }
            catch (Exception ex) { Log("PollCommand error: " + ex.Message); }
        }

        // ---- playback -----------------------------------------------------------------------
        private void Play(string file, string title, string artist)
        {
            if (string.IsNullOrEmpty(file) || !File.Exists(file)) { Log("Play: file not found: " + file); return; }
            try
            {
                StopInternal();
                trackEndedNaturally = false;   // clear any stale end-flag so a late event can't double-skip
                reader = new AudioFileReader(file) { Volume = Effective() };
                lowpass = new LowPassSampleProvider(reader) { Cutoff = smoothedCutoff };
                output = new WaveOutEvent();
                output.PlaybackStopped += OnPlaybackStopped;
                output.Init(new SampleToWaveProvider16(lowpass));   // reader -> low-pass -> 16-bit -> device
                output.Play();
                currentTitle = title; currentArtist = artist; currentFile = file;
                isPlaying = true; isPaused = false;
                playStartedAt = Game.GameTime;
                // Command-driven playback silences the in-game radio so it doesn't compete. But when we
                // were activated BY the wheel (viaStation), we must STAY tuned to CLAUDE FM (its baked
                // audio is the silent placeholder) - forcing OFF here would un-tune us and stop ourselves.
                if (!viaStation)
                {
                    if (savedStation == null)   // remember what was on so we can hand back when the song ends
                    {
                        try { savedStation = Function.Call<string>(Hash.GET_PLAYER_RADIO_STATION_NAME); }
                        catch { savedStation = "OFF"; }
                    }
                    var v = Game.Player.Character != null ? Game.Player.Character.CurrentVehicle : null;
                    if (v != null && v.Exists()) Function.Call(Hash.SET_VEH_RADIO_STATION, v, "OFF");  // veh radio
                    Function.Call(Hash.SET_RADIO_TO_STATION_NAME, "OFF");  // PLAYER radio too - the one
                    // GET_PLAYER_RADIO_STATION_NAME reads, so the yield-check sees OFF (was the bug: we
                    // set the vehicle radio but read the player radio -> always saw a station -> yielded).
                }
                Notify("~b~Claude Radio: ~w~" + title);
                Log("Playing: " + title + " (" + file + ")");
                WriteStatus();
            }
            catch (Exception ex) { Log("Play error: " + ex.Message); isPlaying = false; }
        }

        private void PlayNext()
        {
            if (queue.Count > 0) { var t = queue.Dequeue(); Play(t[0], t[1], t[2]); }
            else if (stationMode && PlayNextStation()) { /* station keeps rolling: songs + adverts */ }
            else { isPlaying = false; currentTitle = ""; RestoreGameRadio(); WriteStatus(); }  // hand back, no silence
        }

        // Station mode: pick what plays next - mostly a random song, but every few songs slot in a random
        // ADVERT (like GTA radio cycles Music -> Advert). Avoids immediately repeating the current song.
        private bool PlayNextStation()
        {
            var t = PickNextTrack();
            if (t == null) return false;
            Play(t[0], t[1], t[2]);
            return true;
        }

        // Choose what plays next (random song, or an advert every few songs) WITHOUT playing it - so the
        // crossfade can pre-load the incoming track while the current one is still finishing.
        private string[] PickNextTrack()
        {
            var songs = LoadLibrary();
            var ads = LoadAdverts();
            int adsEvery = AdsEvery();
            if (adsEvery > 0 && ads.Count > 0 && songsSinceAd >= adsEvery) { songsSinceAd = 0; return ads[rng.Next(ads.Count)]; }
            if (songs.Count == 0) return ads.Count > 0 ? ads[rng.Next(ads.Count)] : null;
            int pick = rng.Next(songs.Count);
            if (songs.Count > 1) { int g = 0; while (songs[pick][0] == currentFile && g++ < 8) pick = rng.Next(songs.Count); }
            songsSinceAd++;
            return songs[pick];
        }

        // ---- 3-second crossfade -------------------------------------------------------------------
        // Centralized volume application so it works whether or not a crossfade is in progress: outside a
        // crossfade the one track gets Effective(); during a crossfade A ramps down and B ramps up.
        private void ApplyVolume()
        {
            float e = Effective();
            if (crossfading && reader != null && readerB != null)
            {
                float t = Clamp01((Game.GameTime - crossfadeStart) / (float)CROSSFADE_MS);
                try { reader.Volume = e * (1f - t); } catch { }
                try { readerB.Volume = e * t; } catch { }
            }
            else if (reader != null) { try { reader.Volume = e; } catch { } }
        }

        private void CrossfadeUpdate()
        {
            try
            {
                if (crossfading)
                {
                    if (lowpassB != null) lowpassB.Cutoff = smoothedCutoff;
                    ApplyVolume();
                    float t = Clamp01((Game.GameTime - crossfadeStart) / (float)CROSSFADE_MS);
                    bool aEnded = true; try { aEnded = reader == null || (reader.Length > 0 && reader.Position >= reader.Length); } catch { }
                    if (t >= 1f || aEnded) FinishCrossfade();
                }
                else if (isPlaying && !isPaused && reader != null)
                {
                    int abps = reader.WaveFormat.AverageBytesPerSecond;
                    long left = 0; try { left = reader.Length - reader.Position; } catch { }
                    double secsLeft = left / (double)Math.Max(1, abps);
                    if (secsLeft <= 3.0 && secsLeft > 0.05)   // ~3s from the end -> start blending into the next
                    {
                        var nxt = PickNextTrack();
                        if (nxt != null) StartCrossfade(nxt);
                    }
                }
            }
            catch (Exception ex) { Log("CrossfadeUpdate error: " + ex.Message); crossfading = false; }
        }

        private void StartCrossfade(string[] t)
        {
            try
            {
                readerB = new AudioFileReader(t[0]) { Volume = 0f };
                lowpassB = new LowPassSampleProvider(readerB) { Cutoff = smoothedCutoff };
                outputB = new WaveOutEvent();
                outputB.Init(new SampleToWaveProvider16(lowpassB));
                outputB.Play();
                nextFile = t[0]; nextTitle = t[1]; nextArtist = t[2];
                crossfading = true; crossfadeStart = Game.GameTime;
                Log("Crossfading -> " + t[1]);
            }
            catch (Exception ex) { Log("StartCrossfade error: " + ex.Message); crossfading = false; DisposeB(); }
        }

        private void FinishCrossfade()
        {
            try { var o = output; var r = reader; if (o != null) { o.Stop(); o.Dispose(); } if (r != null) r.Dispose(); } catch { }
            output = outputB; reader = readerB; lowpass = lowpassB;   // promote B -> current
            outputB = null; readerB = null; lowpassB = null;
            currentFile = nextFile; currentTitle = nextTitle; currentArtist = nextArtist;
            crossfading = false; trackEndedNaturally = false;
            ApplyVolume();
            WriteStatus();
            Log("Now playing: " + currentTitle);
        }

        private void DisposeB()
        {
            try { if (outputB != null) outputB.Dispose(); } catch { }
            try { if (readerB != null) readerB.Dispose(); } catch { }
            outputB = null; readerB = null; lowpassB = null; crossfading = false;
        }

        // Adverts live in their OWN folder (baseDir\adverts) so they're never shuffled in as "songs" -
        // they're interleaved by the rotation above. AdsEvery is configurable (radio_config.json, 0 = off).
        private System.Collections.Generic.List<string[]> LoadAdverts()
        {
            var list = new System.Collections.Generic.List<string[]>();
            try
            {
                string adir = Path.Combine(baseDir, "adverts");
                if (Directory.Exists(adir))
                    foreach (var f in Directory.GetFiles(adir, "*.mp3"))
                        list.Add(new[] { f, Path.GetFileNameWithoutExtension(f), "CLAUDE FM Advert" });
            }
            catch { }
            return list;
        }

        private int AdsEvery()
        {
            try
            {
                string cfg = Path.Combine(baseDir, "radio_config.json");
                if (File.Exists(cfg)) { var v = JObject.Parse(SafeRead(cfg))["ads_every"]; if (v != null) return (int)v; }
            }
            catch { }
            return 3;   // default: an advert after roughly every 3 songs
        }

        // ---- custom-station activation: tune the radio wheel to CLAUDE FM to start/stop us -----------
        // The DLC adds a real (silent-placeholder) station to the wheel. We watch the player's station;
        // tuning IN starts our shuffle+ads, tuning AWAY stops us. No Claude command needed.
        private void CheckStationTrigger()
        {
            string trig = TriggerStation();
            if (string.IsNullOrEmpty(trig)) return;
            try
            {
                // Only re-read the station while IN a vehicle; on foot we keep the last value, so the
                // positional "hear it from outside the car" effect still works and a switched-away
                // broadcast stays muted (not mistaken for a tune-away because GET returns OFF on foot).
                var ped = Game.Player.Character;
                bool inVeh = ped != null && ped.CurrentVehicle != null && ped.CurrentVehicle.Exists();
                if (inVeh)
                {
                    string st = Function.Call<string>(Hash.GET_PLAYER_RADIO_STATION_NAME);
                    onClaudeStation = trig.Equals(st, StringComparison.OrdinalIgnoreCase);
                }

                if (onClaudeStation)
                {
                    if (!isPlaying)            // first tune-in -> start the broadcast
                    {
                        viaStation = true;
                        Log("Tuned to " + trig + " - starting CLAUDE FM.");
                        if (!PlayNextStation()) viaStation = false;
                    }
                    mutedAway = false;         // audible (positional handles in/out of the car)
                }
                else if (viaStation && isPlaying)
                {
                    // Switched to another station / off: keep the broadcast rolling, just mute it - so
                    // tuning back rejoins the song mid-stream like a real radio station.
                    mutedAway = true;
                }
            }
            catch { }
        }

        private string TriggerStation()
        {
            if (triggerStation != null) return triggerStation;   // cached; re-read happens on script reload
            triggerStation = "RADIO_49_COMMUNITYSLOT";           // default; overridable via radio_config.json
            try
            {
                string cfg = Path.Combine(baseDir, "radio_config.json");
                if (File.Exists(cfg)) { var v = JObject.Parse(SafeRead(cfg))["trigger_station"]; if (v != null) triggerStation = (string)v; }
            }
            catch { }
            return triggerStation;
        }

        // The downloaded-songs library = the same index Python maintains, in the (configurable) music folder.
        private string MusicFolder()
        {
            try
            {
                string cfg = Path.Combine(baseDir, "radio_config.json");
                if (File.Exists(cfg))
                {
                    var mf = (string)JObject.Parse(SafeRead(cfg))["music_folder"];
                    if (!string.IsNullOrEmpty(mf) && Directory.Exists(mf)) return mf;
                }
            }
            catch { }
            return Path.Combine(baseDir, "music");
        }

        private System.Collections.Generic.List<string[]> LoadLibrary()
        {
            var list = new System.Collections.Generic.List<string[]>();
            try
            {
                string mf = MusicFolder();
                string idx = Path.Combine(mf, "_claude_radio_index.json");
                if (File.Exists(idx))
                    foreach (var e in JArray.Parse(SafeRead(idx)))
                    {
                        string f = (string)e["file"];
                        if (!string.IsNullOrEmpty(f) && File.Exists(f))
                            list.Add(new[] { f, (string)e["title"] ?? "", (string)e["artist"] ?? "" });
                    }
                if (list.Count == 0 && Directory.Exists(mf))   // fallback: any mp3s sitting in the folder
                    foreach (var f in Directory.GetFiles(mf, "*.mp3"))
                        list.Add(new[] { f, Path.GetFileNameWithoutExtension(f), "" });
            }
            catch { }
            return list;
        }

        // When Claude Radio is done (song ended with nothing queued, or you stopped it), put the in-game
        // radio back to whatever station was on before we took over - so you're never left in silence.
        private void RestoreGameRadio()
        {
            try
            {
                if (!string.IsNullOrEmpty(savedStation) && !savedStation.Equals("OFF", StringComparison.OrdinalIgnoreCase))
                {
                    Function.Call(Hash.SET_RADIO_TO_STATION_NAME, savedStation);
                    Log("Handed back to in-game radio: " + savedStation);
                }
            }
            catch { }
            savedStation = null;
        }

        private void OnPlaybackStopped(object sender, StoppedEventArgs e)
        {
            // Distinguish a natural end (advance queue) from a manual stop/skip (handled inline).
            if (!manualStop) trackEndedNaturally = true;
            manualStop = false;
        }

        private void PausePlayback() { if (output != null && isPlaying && !isPaused) { output.Pause(); isPaused = true; WriteStatus(); } }
        private void ResumePlayback() { if (output != null && isPlaying && isPaused) { output.Play(); isPaused = false; WriteStatus(); } }
        private void Skip() { manualStop = true; PlayNext_AfterStop(); }

        private void PlayNext_AfterStop()
        {
            // Stop current, then immediately advance (don't wait for the async stopped event).
            DisposePlayback();
            PlayNext();
        }

        private void SetVolume(int level)
        {
            volume = Math.Max(0, Math.Min(100, level)) / 100f;
            ApplyVolume();
            try { File.WriteAllText(volFile, level.ToString()); } catch { }   // persist across sessions
            WriteStatus();
        }

        // Effective NAudio volume = calibration trim x in-game Music slider (0..1) x positional gain,
        // x 0 when the broadcast is "playing in the background" (player tuned to another station).
        private float Effective() { return volume * gameMusic01 * posGain * (mutedAway ? 0f : 1f); }

        private void UpdateGameVolume()
        {
            try
            {
                int mv = Function.Call<int>(Hash.GET_PROFILE_SETTING, MUSIC_VOLUME_SETTING);
                gameMusic01 = Math.Max(0, Math.Min(10, mv)) / 10f;
                ApplyVolume();
            }
            catch { }
        }

        // Compute + apply positional gain and low-pass cutoff from the player's spot relative to the car.
        // (Natives scouted by in-game Claude - see radio/POSITIONAL_AUDIO_NATIVES.md.)
        private void UpdatePositional(Vehicle veh, bool inside)
        {
            float tGain, tCut;
            diagInside = inside; diagEngineOn = false; diagDist = -1f; diagDoorOpen = 0f;
            try
            {
                if (veh == null) { tGain = 0f; tCut = 800f; }            // no car -> silent
                else if (inside) { tGain = 1f; tCut = 22000f; diagEngineOn = true; }  // inside -> full & clear
                else
                {
                    bool eng = Function.Call<bool>(Hash.GET_IS_VEHICLE_ENGINE_RUNNING, veh);
                    diagEngineOn = eng;
                    if (!eng) { tGain = 0f; tCut = 800f; }              // engine off -> radio off
                    else
                    {
                        Vector3 cam = Function.Call<Vector3>(Hash.GET_GAMEPLAY_CAM_COORD);
                        float dist = (cam - veh.Position).Length(); diagDist = dist;
                        float fall = Clamp01(1f - (dist - 2.5f) / (22f - 2.5f));  // full just outside, ~0 by 22m
                        float door = MaxDoorOpen(veh); diagDoorOpen = door;       // 0..1 most-open passenger door
                        tGain = Math.Min(1f, fall * (1f + 0.25f * door));        // door open = a touch louder
                        tCut = Lerp(900f, 4500f, door) * (0.5f + 0.5f * fall);   // shut+far = muffled; open/near = clearer
                    }
                }
            }
            catch { tGain = posGain; tCut = smoothedCutoff; }

            posGain += (tGain - posGain) * 0.15f;             // glide, don't click
            smoothedCutoff += (tCut - smoothedCutoff) * 0.15f;
            ApplyVolume();
            if (lowpass != null) lowpass.Cutoff = smoothedCutoff;
        }

        private float MaxDoorOpen(Vehicle veh)
        {
            float m = 0f;
            try
            {
                for (int d = 0; d <= 3; d++)   // doors 0-3 = the passenger doors (ignore hood/trunk)
                {
                    if (Function.Call<bool>(Hash.GET_IS_DOOR_VALID, veh, d))
                    {
                        float r = Function.Call<float>(Hash.GET_VEHICLE_DOOR_ANGLE_RATIO, veh, d);
                        if (r > m) m = r;
                    }
                }
            }
            catch { }
            return Clamp01(m);
        }

        private static float Clamp01(float v) { return v < 0f ? 0f : (v > 1f ? 1f : v); }
        private static float Lerp(float a, float b, float t) { return a + (b - a) * Clamp01(t); }

        private void StopInternal()
        {
            manualStop = true;
            DisposePlayback();
            isPlaying = false; isPaused = false; currentTitle = "";
            WriteStatus();
        }

        private void DisposePlayback()
        {
            try
            {
                if (output != null) { output.PlaybackStopped -= OnPlaybackStopped; output.Stop(); output.Dispose(); output = null; }
                if (reader != null) { reader.Dispose(); reader = null; }
                lowpass = null;
                DisposeB();   // also tear down any in-progress crossfade slot
            }
            catch (Exception ex) { Log("Dispose error: " + ex.Message); }
        }

        // ---- status + util ------------------------------------------------------------------
        private void WriteStatus()
        {
            try
            {
                double pos = 0, dur = 0;
                if (reader != null) { pos = reader.CurrentTime.TotalSeconds; dur = reader.TotalTime.TotalSeconds; }
                var o = new JObject
                {
                    ["state"] = isPlaying ? (isPaused ? "paused" : "playing") : "idle",
                    ["title"] = currentTitle,
                    ["artist"] = currentArtist,
                    ["position_s"] = Math.Round(pos, 1),
                    ["duration_s"] = Math.Round(dur, 1),
                    ["queue_len"] = queue.Count,
                    ["volume"] = (int)Math.Round(volume * 100),
                    // --- instrumentation: does OnTick run during the pause menu? ---
                    ["wallclock"] = DateTime.Now.ToString("HH:mm:ss.fff"),  // advances only if OnTick ticks
                    ["menu_paused"] = SafeBool(Hash.IS_PAUSE_MENU_ACTIVE),  // can we detect the menu from a tick?
                    ["music_setting"] = Math.Round(gameMusic01 * 10),       // committed Music 0-10 we currently see
                    // --- positional audio diagnostics (for auto-testing the muffle/distance/door logic) ---
                    ["pos_gain"] = Math.Round(posGain, 3),                  // 0..1 positional gain applied
                    ["cutoff_hz"] = Math.Round(smoothedCutoff),             // low-pass cutoff (22000 = clear, low = muffled)
                    ["inside"] = diagInside,                                // in the car?
                    ["engine_on"] = diagEngineOn,                          // engine running?
                    ["dist_m"] = Math.Round(diagDist, 1),                  // listener->car distance (-1 if inside/n-a)
                    ["door_open"] = Math.Round(diagDoorOpen, 2)            // most-open door 0..1
                };
                File.WriteAllText(statusFile, o.ToString(Newtonsoft.Json.Formatting.None));
            }
            catch (Exception ex) { Log("WriteStatus error: " + ex.Message); }
        }

        private static string SafeRead(string path)
        {
            using (var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
            using (var sr = new StreamReader(fs))
                return sr.ReadToEnd();
        }

        private void Notify(string msg)
        {
            try { GTA.UI.Notification.Show(msg); } catch { }
        }

        private static bool SafeBool(Hash h) { try { return Function.Call<bool>(h); } catch { return false; } }

        // Runs on its OWN thread, so it keeps going even when SHVDN/OnTick is frozen (pause menu,
        // loading screens). If OnTick's heartbeat stops advancing, the game is frozen: mute our audio
        // (it would otherwise keep blaring over the menu). On resume, restore to the live volume.
        private void FreezeWatcher()
        {
            int last = heartbeat;
            while (watcherRunning)
            {
                try { System.Threading.Thread.Sleep(200); } catch { }
                int cur = heartbeat;
                bool frozen = (cur == last);   // OnTick didn't run during the last 200ms
                last = cur;
                if (frozen != gameFrozen)
                {
                    gameFrozen = frozen;
                    var rd = reader; var rb = readerB;   // local copies; OnTick may dispose concurrently
                    if (frozen) { try { if (rd != null) rd.Volume = 0f; if (rb != null) rb.Volume = 0f; } catch { } }
                    else ApplyVolume();   // unfreeze -> restore (crossfade-aware)
                }
            }
        }

        private void Log(object message)
        {
            try { File.AppendAllText(logFile, DateTime.Now + " : " + message + Environment.NewLine); } catch { }
        }
    }

    /// <summary>
    /// Streams `source` through a per-channel low-pass BiQuad filter with a runtime-settable cutoff, so
    /// we can "muffle" the audio when the listener is outside the car. Cutoff high (~22 kHz) = bypass/clear.
    /// </summary>
    internal class LowPassSampleProvider : ISampleProvider
    {
        private readonly ISampleProvider source;
        private readonly BiQuadFilter[] filters;
        private readonly int channels;
        private readonly float sampleRate;
        private float cutoff = 22000f;

        public LowPassSampleProvider(ISampleProvider src)
        {
            source = src;
            channels = Math.Max(1, src.WaveFormat.Channels);
            sampleRate = src.WaveFormat.SampleRate;
            filters = new BiQuadFilter[channels];
            float c = ClampHz(cutoff);
            for (int i = 0; i < channels; i++) filters[i] = BiQuadFilter.LowPassFilter(sampleRate, c, 0.7f);
        }

        public WaveFormat WaveFormat { get { return source.WaveFormat; } }

        public float Cutoff
        {
            set
            {
                float c = ClampHz(value);
                if (Math.Abs(c - cutoff) < 2f) return;
                cutoff = c;
                for (int i = 0; i < filters.Length; i++) filters[i].SetLowPassFilter(sampleRate, c, 0.7f);
            }
        }

        private float ClampHz(float v) { float max = sampleRate / 2.1f; return v < 100f ? 100f : (v > max ? max : v); }

        public int Read(float[] buffer, int offset, int count)
        {
            int n = source.Read(buffer, offset, count);
            for (int i = 0; i < n; i++) buffer[offset + i] = filters[i % channels].Transform(buffer[offset + i]);
            return n;
        }
    }
}
