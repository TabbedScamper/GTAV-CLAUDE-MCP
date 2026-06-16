# 14 — M8T deep dive: scenes, interiors, voice, enemies, loot (the full heist toolkit)

Mined from the FULL M8T corpus — all 35 heist mods decompiled (`Examples/_decompiled/m8t-*`, ~340k lines:
Casino, Liberty Big Score, UnionDepository, BigBank, NorthYankton, Bobcat, DepositBank, DrugHideout,
IslandTemple, CarryBody, Lombank + 24 more). PATTERNS/12 has the mission skeleton; this file is the
**content layer**: how shipped heists anchor animations, open interiors, play voice lines, spawn enemies,
and run the loot grab. Every native hash from the decompiles was resolved against `native_db.json` —
several differ from what the raw decompile suggests, marked ⚠ where the obvious reading is wrong.

> Decompiled source is git-ignored. These cards are our distilled writeup, with attribution.
> Data appendix (IPL/entity-set/door/anim/ped/audio string tables) at the bottom of this file.

---

### Synchronized scene = M8T's universal "animation at a location" anchor ⭐
**Category:** animation-scene
**Problem:** Play a ped animation exactly AT a world spot (vault, keypad, cash table) without manual teleport/heading fiddling.
**Method:** `scene = CREATE_SYNCHRONIZED_SCENE(x, y, z, 0f, 0f, rotZ, 2)` (rotOrder=2) at the target prop's position/rotation → `TASK_SYNCHRONIZED_SCENE(ped, scene, dict, clip, 4f, -15f, 3341, 16, 1148846080, 0)` (blendIn 4, blendOut -15; those magic ints are the house flag combo) → poll `GET_SYNCHRONIZED_SCENE_PHASE(scene) > 0.99f` to detect "beat finished" → next stage. Speed control mid-anim: `SET_SYNCHRONIZED_SCENE_RATE(scene, 0.5f..0.7f)`. The anim system pulls the ped into place — no SET_ENTITY_COORDS needed.
**Gotcha:** ⚠ The decompile shows only bare hashes — this IS the synchronized-scene API (CREATE=0x8C18E0F9080ADD73, TASK=0xEEA929141F699854, PHASE=0xE4A310B1D7FA73CC); don't mistake it for TASK_PLAY_ANIM_ADVANCED. One scene per ped (never shared). Phase test is `> 0.99f`, never `== 1.0`. Disable `Game.Player.CanControlCharacter` (+ often `IsInvincible=true`) during the beat, restore after. REQUEST_ANIM_DICT first; M8T never unloads dicts.
**Source:** casinoheist L19350-19410 (thermal); drughideoutheist L549-1100 (loot); uniondepository L2358-2680 (drill); 80+ sites corpus-wide

### Props join the ped's scene via PLAY_SYNCHRONIZED_ENTITY_ANIM (drill/bag/thermite)
**Category:** animation-scene
**Problem:** A drill/bag/thermite prop must move in lockstep with the ped's animation.
**Method:** Spawn the prop (`World.CreateProp`, often `HasCollision=false`), then `PLAY_SYNCHRONIZED_ENTITY_ANIM(prop, scene, "<phase>_<prop_model_name>", dict, 1f, -1f, 0, 1148846080)` using the SAME scene handle as the ped's TASK_SYNCHRONIZED_SCENE. The clip name encodes phase+prop: `enter_ch_prop_vault_drill_01a`, `action_ch_prop_vault_drill_01a`, `thermal_charge_male_hei_prop_heist_thermite`, `enter_p_m_bag_var22_arm_s` — R* ships a per-prop clip inside the same anim dict. Casino's thermal beat runs ped + bag + thermite as 3 calls on one scene.
**Gotcha:** ⚠ The string arg is a CLIP NAME in the dict, not a bone (decompile order makes it look like ATTACH_ENTITY_TO_ENTITY — it isn't; that native (0x6B9BBD38AB0796DF) appears separately, e.g. phone→hand bone 28422). Re-issue the prop anim at each phase change alongside the ped's. Props are deleted at beat end, not detached.
**Source:** uniondepository L2490-2530; casinoheist L19364-19403; harvestandtrustee L5579-5638

### The loot-grab sub-FSM: enter → mash-to-grab loop → bag transfer → exit
**Category:** loot
**Problem:** The GTA-O style "stuff cash into a bag" interaction with player-paced progress.
**Method:** Own int sub-machine (states 30→40→50→60→90→100): spawn bag prop + scene at the cash prop → `enter` clip → phase>0.99 → `grab` clip at `SET_SYNCHRONIZED_SCENE_RATE 0.5`; each `INPUT_SCRIPT_RDOWN` (Control 223) tap bumps the rate to 0.7 (mash = faster); when phase>0.99 AND the hand-cash prop is within range of the bag prop → `totaltake += amount` (per-loot-type: cash 5257, coke 9778...), `PLAY_SOUND_FRONTEND(-1, "ROBBERY_MONEY_TOTAL", "HUD_FRONTEND_CUSTOM_SOUNDSET", 1)` → `exit` clip → delete cash/bag/hand props, `lootcollected++`. Payout at mission end is simply `Game.Player.Money += totaltake` once wanted==0.
**Gotcha:** Money is granted ONCE on clean escape, never per grab — the during-heist number is UI only. The bag-proximity test is what confirms a grab "landed". Re-equip the visual backpack after (component variation card below).
**Source:** drughideoutheist L549-1289; uniondepository L6846-7443; casinoheist L15545+

### The TAKE counter HUD: UIText + "timerbars" texture sprite (not scaleform)
**Category:** hud
**Problem:** Show the running "TAKE $X" loot counter like GTA:O.
**Method:** `REQUEST_STREAMED_TEXTURE_DICT("timerbars")` → once `HAS_STREAMED_TEXTURE_DICT_LOADED` → `DRAW_SPRITE("timerbars", "all_black_bg", x≈0.21, y≈0.04, w, h, rot, r,g,b,a)` per frame as the backdrop, with `new UIText("TAKE", Point(1100,689), 0.4f, ...)` + a money UIText drawn over it. Flash feedback: keep `colorcash="~w~"`, on take increase set `"~g~"` + GameTime stamp, revert after 500ms.
**Gotcha:** ⚠ It's a streamed TEXTURE DICT + DRAW_SPRITE (0xE7FFAE5EBF23D890), not the timerbars scaleform. Pixel Points assume 1280x720-normalized UI; per-frame draws.
**Source:** islandtempleheist L2637-2863; drughideoutheist L1169-1289

### Open any DLC/heist interior: REQUEST_IPL + interior id + entity sets
**Category:** interior
**Problem:** Make a bank vault/casino/lab interior exist and configure its props.
**Method:** `REQUEST_IPL("<name>")` for each piece (often several: base + `hi@` high-LOD variants; see appendix table) early in setup → `id = GET_INTERIOR_AT_COORDS(x,y,z)` → toggle props with `ACTIVATE_INTERIOR_ENTITY_SET(id, "<set>")` / `DEACTIVATE_INTERIOR_ENTITY_SET(id, "<set>")` (vault layouts `SET_BASE_VAULT_00..09`, blockers `SET_ACCESS_BLOCKER`, decor, `SET_VAULT_DOOR_OPEN/CLOSED`...). Casino flips hundreds of sets to configure one space. `REMOVE_IPL` on cleanup for swapped worlds (North Yankton).
**Gotcha:** IPL load is async — do it a stage before you need it. Entity sets are per-interior-id; unknown set names silently no-op. ⚠ Hash 0x420BD37289EEE162 in decompiles is DEACTIVATE_INTERIOR_ENTITY_SET (ACTIVATE is 0x55E86AF2712B36A1) — check which polarity a harvested snippet actually calls.
**Source:** vicesmugglingheists L3112-3166; uniondepository L4134, L14998; casinoheist L55300+; asafehouse L1444-1450

### Enter interiors/swapped worlds: fade → teleport → restore control → fade in
**Category:** interior
**Problem:** Move the player into an interior (or North Yankton) without streaming pop or falling through.
**Method:** `Game.FadeScreenOut(1000); Script.Wait(1000);` → set player Position to the interior coords → activate entity sets / freeze door props → `Task.ClearAll()`, `HasCollision=true`, `CanControlCharacter=true` → `Script.Wait(1000); Game.FadeScreenIn(1000);`. North Yankton = same pattern after loading the `prologue01*` IPL family (it's a world swap, not an interior; coords like (3217..., -4834..., 111) / interiors often negative-Z e.g. -1325, 150, -100).
**Gotcha:** Collision loads with the IPL — M8T never calls REQUEST_COLLISION_AT_COORD; the 1s post-teleport wait IS the streaming buffer. Always re-assert HasCollision + control after.
**Source:** uniondepository L4173-4186; northyanktonjewelry L453, L7442

### Doors/vaults are PROPS: find nearby, freeze to lock, unfreeze+rotate to open
**Category:** doors
**Problem:** Lock a bank door until the heist says so; swing a vault door open.
**Method:** No DOOR_SYSTEM anywhere in 35 mods. Instead: `World.GetNearbyProps(coord, 1-3f, new Model("v_ilev_fin_vaultdoor"))` → match → `FreezePosition = true` (locked). To open: `FreezePosition=false` then set `Heading`/`Rotation.Z` — ramp the angle over ticks for a swing (`vdoorprop.Rotation = new Vector3(0,0,rotars)` with rotars increasing). Double doors/gates = L/R model pair rotated opposite. Blown/cut doors = `DOES_ENTITY_EXIST` check → `Delete()` (optionally swap in a broken variant prop). 40+ door models in the appendix.
**Gotcha:** The map's own door props can be grabbed this way — you don't spawn doors, you FIND them. Freeze, don't delete, for "locked" (delete is irreversible). Existence-check (0x7239B21A38F536BA DOES_ENTITY_EXIST) before every touch.
**Source:** casinoheist L10365-10407, L11102; northyanktonjewelry L2257-2301, L5140; uniondepository L4157

### Custom voice lines: ship .wav files + System.Media.SoundPlayer from scripts\
**Category:** voice
**Problem:** Heists need narrative voice lines GTA doesn't have.
**Method:** Ship wavs in `scripts\<ModName>\*.wav` (the corpus ships ~108). Class-level `SoundPlayer`; at a mission beat: `soundPlayer.SoundLocation = "scripts\\DepositBankHeists\\tellopen1.wav"; soundPlayer.Play();`. Triggering is purely stage-driven (state machine cases), sequencing by GameTime delays / next-beat gates — no overlap detection.
**Gotcha:** `.Play()` is fire-and-forget (background thread) — pacing comes from the state machine, so a skipped stage can talk over a previous line. WAV format only (PCM); relative paths resolve from GTAV root. For OUR runner the equivalent is the ClaudeRadio/NAudio path or `hud_message` + frontend sound.
**Source:** islandtempleheist L1420; depositbankheists L2250; asafehouse L3204; ballasheist L2988

### In-engine ped speech + CB-radio framing (PLAY_PED_AMBIENT_SPEECH_WITH_VOICE_NATIVE)
**Category:** voice
**Problem:** Make a ped audibly speak R* lines (Lester briefings, guard barks) with radio framing.
**Method:** `REQUEST_SCRIPT_AUDIO_BANK("CB_RADIO_SFX", 1)` → squelch in: `PLAY_SOUND_FRONTEND(-1, "Start_Squelch", "CB_RADIO_SFX", 1)` → `PLAY_PED_AMBIENT_SPEECH_WITH_VOICE_NATIVE(ped, "hs3f_jpaa", "lester", "SPEECH_PARAMS_FORCE_FRONTEND", false)` → poll `IS_AMBIENT_SPEECH_PLAYING(ped)` until done → squelch out / next line. Param strings: `SPEECH_PARAMS_FORCE_FRONTEND` (full volume anywhere), `SPEECH_PARAMS_FORCE_SHOUTED_CLEAR`.
**Gotcha:** Speech contexts are R* dialogue ids (`hs3f_*` = casino-heist finale lines) — they only exist for shipped dialogue; browse with gtadata audio tools before assuming. Poll IS_AMBIENT_SPEECH_PLAYING or lines overlap. Bank must be requested first.
**Source:** casinoheist L9220-9260, L9515-9612; bobcat L4035

### Heist mood music + sound beats: TRIGGER_MUSIC_EVENT / audio banks / entity loops
**Category:** audio
**Problem:** Score the heist phases like R* does (tension → action → escape).
**Method:** Phase transitions fire `TRIGGER_MUSIC_EVENT("<event>")` — the HEI4_FIN_* suite maps to mission moods: `_SUSPENSE_LOW/HIGH` (stealth), `_ACTION`/`_ACTION_MD` (combat), `_SAFE_ROOM`, `_DRIVING_MD` (escape), `_STOP_MUSIC` (end); also `MP_MC_ACTION`, `MP_MC_START_HEIST_4`. One-shot SFX: `REQUEST_SCRIPT_AUDIO_BANK("DLC_HEIST_FLEECA_SOUNDSET"...)` then `PLAY_SOUND_FRONTEND` / looped tool noise via `PLAY_SOUND_FROM_ENTITY(0, "Drill", drillProp, "DLC_HEIST_FLEECA_SOUNDSET", 1, 0)`.
**Gotcha:** Music events are fire-and-forget state pushes — always fire the matching STOP on pass/fail/cleanup or the score keeps playing forever. Looped entity sounds also need an explicit stop. Bank before sound, every time.
**Source:** asafehouse L1424-2169 (full HEI4 suite); betta L2579, L10475; gta6 L2451; harvestandtrustee L2870

### Enemy/guard stack: validate → arm → relationship-hate → task (no combat-attribute natives)
**Category:** enemies
**Problem:** Spawn guards that patrol, then fight; hostages that comply; allies that don't get shot.
**Method:** Model.Request(10000) + IsLoaded wait → `World.CreatePed(model, pos, heading)` → loop `DOES_ENTITY_EXIST` until live → `Weapons.Give(hash, 999, equip, true)` → `World.AddRelationshipGroup("m8tgardscum4")` once, `ped.RelationshipGroup = Game.GenerateHash("m8tgardscum4")`, `World.SetRelationshipBetweenGroups((Relationship)5, group, playerGroup)` (5=hate/attack-on-sight; 0=ally; set BOTH directions for guard-vs-ally wars). Behavior is pure tasks: idle `Task.WanderAround(pos, 20f)`; combat trigger `Task.FightAgainst(player)` + `Task.ShootAt(player)` per ped in the list; menace `Task.AimAt(ent, -1)`; hostages `Task.HandsUp(-1)`/`Task.Cower(-1)`; `Task.LeaveVehicle()` before fighting from cars. Track every ped in Lists; death loop removes blips (`CurrentBlip.Remove()`); cleanup deletes all.
**Gotcha:** NO SET_PED_COMBAT_ATTRIBUTES / accuracy / vision-cone natives in 35 mods — escalation is tasks + relationship 5 + direct `Game.Player.WantedLevel = N`. Stealth detection is event-driven only: `ped.IsShooting`, `player.IsShooting` (+ `IS_PED_CURRENT_WEAPON_SILENCED` exemption), wanted>0. HandsUp/Cower need `Task.ClearAll()` before re-tasking.
**Source:** depositbankheists L5617-5636, L2919; islandtempleheist L1171-1334; bobcat L4527-4548; bailenforcement L939

### Heist outfit/bag via SET_PED_COMPONENT_VARIATION (story vs freemode slots differ)
**Category:** player-setup
**Problem:** Put the duffel bag / heist gear on the player, and take it off.
**Method:** Freemode/other models: `SET_PED_COMPONENT_VARIATION(ped, 5, 45, 0, 2)` (slot 5 = hands/parachute-bag, variation 45 = duffel; 0 to remove). Story protagonists (player_zero/one/two): slot 9 (tasks/decal), variation 1 / 0. Wrapped as `eqbag(on)` and re-applied after scene beats (anims can clear the visual).
**Gotcha:** Slot/variation ids differ per model family — branch on `player.Model` like M8T does. Re-equip after each loot beat.
**Source:** drughideoutheist L1357-1389

### Mission-passed shard banner: MP_BIG_MESSAGE_FREEMODE scaleform
**Category:** hud
**Problem:** The big "HEIST PASSED / $324,221" center-screen banner.
**Method:** `new Scaleform("MP_BIG_MESSAGE_FREEMODE")` → poll `HAS_SCALEFORM_MOVIE_LOADED(handle)` (≤1s) → `CallFunction("SHOW_SHARD_CREW_RANKUP_MP_MESSAGE", title, subtitle)` (e.g. "~y~Bobcat Security Heist Success", "Money earned $324221") → `Render2D()` EVERY frame for the display window (~5s timer). Variant: `CallFunction("SHOW_MISSION_PASSED_MESSAGE", msg, "TESTING", 5000, true, 0, true)`.
**Gotcha:** Render2D is immediate-mode — stop calling it and the shard vanishes; M8T runs a timer field counting it down. Load-poll before first CallFunction or it silently no-ops.
**Source:** bobcat L11811-11841, L8641; humane-labs L8125

### Thermite burn / heist FX: START_PARTICLE_FX_LOOPED_AT_COORD
**Category:** ptfx
**Problem:** The burning-thermite visual on a vault door.
**Method:** After the thermal-charge scene beat's timer (~20s), `START_PARTICLE_FX_LOOPED_AT_COORD("scr_heist_ornate_thermal_burn", x, y, z, rx, ry, rz, scale, ...)` at the door coords; door prop is then deleted/swung.
**Gotcha:** Looped PTFX returns a handle that must be stopped/removed on cleanup; the `scr_heist_*` asset family needs its fx asset requested (REQUEST_NAMED_PTFX_ASSET + USE_PARTICLE_FX_ASSET) — see PATTERNS/08 for the full PTFX recipe.
**Source:** casinoheist thermal sequence L19721+; uniondepository reward stage

---

## Data appendix — harvested string tables (grounding data for the generator)

#### Anim dict/clip pairs (the heist-action vocabulary)
| Dict | Clips | Action |
|---|---|---|
| `anim@heists@ornate_bank@grab_cash` | `enter` / `grab` / `exit` (+ `..._bag` prop clips) | grab cash piles into bag |
| `anim@heists@money_grab@duffel` | `enter` / `exit` | duffel-bag cash grab |
| `anim@heists@ornate_bank@cash_trolley` | `enter` / `idle` / `exit` | cash trolley |
| `anim@heists@ornate_bank@hack` | `enter` / `idle` / `exit` | vault/safe hack |
| `anim@heists@humane_labs@emp@hack_door` | (varies) | keypad/door hack |
| `anim_heist@hs3f@ig1_hack_keypad@male@` | `action_var_02` | keypad press |
| `anim_heist@hs3f@ig13_thermal_charge@thermal_charge@male@` | `thermal_charge_male_male` + `..._p_m_bag_var22_arm_s` + `..._hei_prop_heist_thermite` | plant thermal charge (ped+bag+thermite) |
| `anim_heist@hs3f@ig10_lockbox_drill@pattern_01@lockbox_01..03@male@` | `enter`/`action`/`idle`/`reward`/`no_reward` (+ `..._ch_prop_vault_drill_01a` prop clips) | lockbox drilling minigame |
| `anim@scripted@heist@ig14_elevator_hack@male@` | `enter` / `idle` / `exit` | elevator keycard hack |
| `anim_heist@hs3f@ig3_cardswipe_insync@male@` | (varies) | keycard swipe |
| `anim@mp_player_intmenu@key_fob@` | `fob_click` | key-fob click |
| `dam_ko` | `drown` | unconscious pose (CarryBody attach) |

#### Heist props
`ch_prop_vault_drill_01a` (drill) · `h4_p_h4_m_bag_var22_arm_s` (duffel) · `ch_prop_moneybag_01a` ·
`hei_prop_heist_cash_pile` · `hei_prop_heist_thermite_flash` · `prop_phone_ing` (hand phone, bone 28422) ·
`bkr_cash_scatter_02`

#### Door/vault prop models (find-and-freeze targets)
Bank: `v_ilev_fin_vaultdoor`, `p_fin_vaultdoor_s`, `v_ilev_bk_door`, `hei_prop_hei_bankdoor_new`,
`apa_prop_hei_bankdoor_new`, `v_ilev_bl_doorsl_l/_r`, `v_ilev_bl_shutter2`, `hei_v_ilev_bk_gate_pris`,
`v_ilev_fingate` · Casino: `ch_prop_ch_vault_d_door_01a`, `ch_prop_ch_vaultdoor01x`,
`ch_prop_ch_vault_slide_door_lrg/_sm`, `ch_prop_ch_tunnel_door_01_l/_r`, `ch_prop_casino_door_01a/b/c/f`,
`ch_prop_ch_service_door_02b/c`, `ch_prop_ch_utility_door_01a`, `ch_prop_ch_bay_elev_door`,
`ch_prop_ch_lobay_gate01` · Misc: `prop_gold_vault_gate_01`, `prop_lrggate_01c_l/_r`,
`v_ilev_garageliftdoor`, `v_ilev_cor_doorglassa/b`, `v_ilev_cor_firedoorwide`, `v_ilev_rc_door1_st`,
`v_ilev_rc_door3_l/_r`, `xm_int_lev_xm17_base_door_02`, `des_vaultdoor001_start`

#### IPL names (interiors M8T opens)
Union Depository vault: `FINBANK` · Casino: `vw_int_placement_vw_interior_0_dlc_casino_main_milo`,
`ch_dlc_arcade`, `ch_dlc_plan`, `m23_2_dlc_int_casinobase` (+`hi@`), `m23_2_int_warehouse` ·
Mansion: `m25_2_int_mansion`(+`_2`,`_garage`,`hi@`, furniture `m25_2_ch1_06e_mansion_interior_a..d`) ·
North Yankton: `prologue01`..`prologue06` + `_lod` variants, `prologue03_grv_dug` ·
Bunker/X17: `xm_x17dlc_int_placement_interior_0..35_*_milo_` family (base/facility/silo/tunnels/lab),
`xm_bunkerentrance_door`, `xm_siloentranceclosed_x17` · Biker warehouse:
`bkr_biker_interior_placement_interior_4_biker_dlc_int_ware03_milo` · Smuggler:
`sm_smugdlc_interior_placement_interior_0_smugdlc_int_01_milo_` · Misc: `Coroner_Int_On`,
`m24_2_int_hacker_basement`, `m24_1_dlc_int_bounty`, `m25_1_int_tycoon_studio_mid`

#### Interior entity sets (Casino/arcade/mansion)
Vault layouts `SET_BASE_VAULT_00..09`, `SET_VAULT_DOOR_OPEN/CLOSED` · blockers `SET_ACCESS_BLOCKER`,
`SET_ARCADE_BLOCKER`, `SET_ARMORY_BLOCKER`, `SET_MOD_BLOCKER`, `SET_PODIUM_BLOCKER`, `SET_STEP_COLLISION` ·
heist-plan `Set_Plan_Vault_Drill_Alt`, `Set_Plan_Vault_Laser(_Alt)`, `Set_Plan_Vault_KeyCard_01a`,
`Set_Plan_Stealth_Outfits` · arcade decor `entity_set_arcade_set_*` (ceiling/derelict/trophy/floor_option_01..08,
mural_neon_option_01..08) · home decor `SET_XMAS`, `SET_HALLOWEEN`, `SET_WALLPAPER_*`, `SET_ELEV_*`, `SET_PET_*`

#### Ped models by role
Armed: `s_m_y_swat_01` (SWAT), `s_m_y_cop_01`, `s_m_m_snowcop_01` (Yankton), `mp_m_securoguard_01`,
`s_m_m_highsec_03`, `s_m_y_robber_01` · Unarmed/staff: `s_m_m_security_01`, `a_m_m_prolhost_01` (teller),
`s_f_y_bartender_01` · Crew/ally: `mp_m_freemode_01` · Civilians: `a_m_y_business_02`, `a_f_y_vinewood_01/03/04`,
`a_m_m_soucent_02`, `a_m_m_bevhills_01/02`, `a_m_m_eastsa_01/02`, `a_m_y_genstreet_01/02`

#### Music events / audio banks / frontend sounds
Music: `HEI4_FIN_SUSPENSE_LOW/HIGH`, `HEI4_FIN_ACTION(_MD)`, `HEI4_FIN_SAFE_ROOM(_AGGRO_MD)`,
`HEI4_FIN_DRIVING_MD`, `HEI4_FIN_START_STA`, `HEI4_FIN_STOP_MUSIC`, `MP_MC_ACTION`, `MP_MC_START_HEIST_4`,
`BKR_GUNRUN_DEAL/VAN`, `DROPZONE_LAND/HELI/JUMP`, `BG_SIGHTSEER_MID` · Banks: `DLC_HEIST_FLEECA_SOUNDSET`,
`DLC_MPHEIST\HEIST_FLEECA_DRILL(_2)`, `DLC_MPHEIST4\DLC_H4_anims_glass_cutter_Sounds`,
`DLC_MPHEIST4\DLCHEI4_GENERIC_01`, `CB_RADIO_SFX`, `HUD_FRONTEND_CUSTOM_SOUNDSET` · Sounds:
`ROBBERY_MONEY_TOTAL` (money tick), `Start_Squelch` (radio), `Drill` (looped from drill prop),
`DLC_HEI4_CUT_GLASS_OVERHEAT_00`/`dlchei4_generic_01` · Speech: contexts `hs3f_jpaa/jpab/jvaa/jwaa/mul_rp1`
voice `lester`, params `SPEECH_PARAMS_FORCE_FRONTEND`, `SPEECH_PARAMS_FORCE_SHOUTED_CLEAR`
