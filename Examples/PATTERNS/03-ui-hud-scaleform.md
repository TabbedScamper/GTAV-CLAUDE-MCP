# 03 — UI, HUD, text, scaleform, input & sound

Mined from **LemonUI**, **NativeUI**, **RAGENativeUI**, **ikt's MenuBase**. Cross-agent duplicates are
merged; "seen in N mods" = corroborated. The overarching rule: **everything here is immediate-mode —
re-issue every frame.**

---

### Normalize coordinates to a 1080-tall virtual canvas (X divisor uses aspect ratio!)
**Category:** ui · *seen in 3 mods*
**Problem:** Draw at fixed positions that look identical at any resolution. Draw natives take 0.0–1.0 coords.
**Method:** Design in a space 1080px tall, `1080*aspectRatio` wide. `aspect = GET_ASPECT_RATIO(false)`. Then `relX = pixelX / (1080.0*aspect)`, `relY = pixelY / 1080.0`.
**Gotcha:** **X divisor is `1080*aspect`, Y is bare `1080`.** Using 1080 for X squashes everything on non-1:1. Use `GET_ASPECT_RATIO`, not screenW/screenH, when letterboxing/eyefinity is in play (they differ). LemonUI uses this; NativeUI hand-rolls `screenW/screenH`.
**Source:** lemonui/LemonUI/Tools/Extensions.cs, nativeui-guad/NativeUI/Screen.cs

### DRAW_RECT/DRAW_SPRITE are CENTER-anchored; text is TOP-LEFT
**Category:** ui · *seen in 3 mods*
**Problem:** A box/sprite appears shifted down-right by half its size.
**Method:** `DRAW_RECT(x,y,w,h,r,g,b,a)` and `DRAW_SPRITE(dict,name,x,y,w,h,heading,r,g,b,a)` treat x,y as the CENTER. From a top-left origin: `x = px + w*0.5; y = py + h*0.5`. Colors 0–255. `DRAW_SCALEFORM_MOVIE` is ALSO center-anchored.
**Gotcha:** Rect/sprite/scaleform = center; text (`END_TEXT_COMMAND_DISPLAY_TEXT`) = top-left. Apply the +half-size only to the former.
**Source:** nativeui-guad/.../Sprite.cs, lemonui/.../ScaledRectangle.cs, ragenativeui/.../Scaleform.cs

### Draw text via the BEGIN→set-every-property→ADD→END sandwich, every frame
**Category:** ui · *seen in 4 mods*
**Problem:** Drawing a string is a stateful command buffer rebuilt each frame, not one call.
**Method:** Set style FIRST: `SET_TEXT_FONT`, `SET_TEXT_SCALE(0.0, scale)`, `SET_TEXT_COLOUR(r,g,b,a)`, `SET_TEXT_JUSTIFICATION`/`_CENTRE`/`_RIGHT_JUSTIFY`, optional `SET_TEXT_WRAP(start,end)`, `SET_TEXT_DROP_SHADOW`/`_OUTLINE`. Then: `BEGIN_TEXT_COMMAND_DISPLAY_TEXT(label)` → `ADD_TEXT_COMPONENT_SUBSTRING_PLAYER_NAME(text)` → `END_TEXT_COMMAND_DISPLAY_TEXT(x,y)`.
**Gotcha:** Set font/scale/colour/justification on EVERY frame before END — state is consumed and reset, so an unset property inherits garbage from the last drawer. Use label `"CELL_EMAIL_BCON"` (not `"STRING"`) when pushing multiple substrings — it concatenates.
**Source:** lemonui/.../ScaledText.cs, nativeui-guad/.../UIResText.cs, ragenativeui/Source/TextCommands.cs

### Split strings into ≤~98 UTF-8 BYTE chunks (not chars)
**Category:** ui · *seen in 3 mods*
**Problem:** Long text truncates/corrupts; the substring native has a hard per-call BYTE limit.
**Method:** Before `ADD_TEXT_COMPONENT_SUBSTRING_*`, slice so each pushed chunk is under the cap (LemonUI 90, NativeUI 99 bytes), measured by `UTF8.GetByteCount`. Push chunks in order under one BEGIN.
**Gotcha:** Limit is BYTES — accented/CJK hits it far sooner than length suggests. Slice on the UTF-8 byte boundary or you split a codepoint → mojibake. Don't split inside a `~...~` color/icon token.
**Source:** lemonui/.../ScaledText.cs, nativeui-guad/.../UIResText.cs

### Request a streamed texture dict, then poll HAS_…_LOADED before DRAW_SPRITE — every frame
**Category:** streaming · *seen in 3 mods*
**Problem:** DRAW_SPRITE draws nothing because the texture dict isn't resident.
**Method:** On the draw path each frame: `if (!HAS_STREAMED_TEXTURE_DICT_LOADED(dict)) REQUEST_STREAMED_TEXTURE_DICT(dict, true);` then `DRAW_SPRITE(dict, name, ...)`.
**Gotcha:** REQUEST is async — may not be ready the same frame, so request+check live on the draw path, not once at init. The sprite simply won't appear for the first frame(s); that's expected, not an error.
**Source:** lemonui/.../ScaledTexture.cs, nativeui-guad/.../Sprite.cs, ragenativeui/.../BarTimerBar.cs

### Measure text width / line count with a parallel BEGIN…END pair
**Category:** ui
**Problem:** Need a string's on-screen width (centering/layout) or wrapped line count before drawing.
**Method:** Width: `BEGIN_TEXT_COMMAND_GET_SCREEN_WIDTH_OF_DISPLAY_TEXT("CELL_EMAIL_BCON")` → push substrings + set font/scale → `END_TEXT_COMMAND_GET_SCREEN_WIDTH_OF_DISPLAY_TEXT(true)` returns 0–1 relative width; ×`1080*aspect` for pixels. Line count: `BEGIN_…_GET_NUMBER_OF_LINES_FOR_STRING` … `END_…(x,y)`. Char height: `GET_RENDERED_CHARACTER_HEIGHT(scale, font)` ×1080.
**Gotcha:** Measurement uses the SAME set-font/scale ritual on its own BEGIN/END pair — set them before END or you measure the wrong font. Returned width is relative; scale it back up yourself.
**Source:** lemonui/.../ScaledText.cs, nativeui-guad/.../Screen.cs

### Honor the safe-zone with the SCRIPT_GFX_ALIGN envelope
**Category:** ui · *seen in 2 mods*
**Problem:** Edge-anchored UI gets clipped by overscan / safe-zone, especially corners.
**Method:** `SET_SCRIPT_GFX_ALIGN(hAlign, vAlign)` (chars 'L'/'R'/'C'/'T'/'B' as ints) → `SET_SCRIPT_GFX_ALIGN_PARAMS(x,y,w,h)` (offset + bounding box of what you draw) → your draws → `RESET_SCRIPT_GFX_ALIGN()`. Read back aligned coords with `GET_SCRIPT_GFX_ALIGN_POSITION(relX,relY,&outX,&outY)`.
**Gotcha:** For any align other than left/top you MUST pass real w/h in `_PARAMS` or the start position is wrong. **Always RESET afterward** or every later draw this frame inherits the offset. Timer-bar HUD also `HIDE_HUD_COMPONENT_THIS_FRAME(6,7,8,9)` each frame to avoid overlap.
**Source:** lemonui/.../SafeZone.cs, ragenativeui/.../TimerBarPool.cs

### Disable game controls each frame; re-enable only what you need
**Category:** input · *seen in 3 mods*
**Problem:** While your UI is up, the player still shoots/moves behind it.
**Method:** Every frame: `DISABLE_ALL_CONTROL_ACTIONS(group)` then `ENABLE_CONTROL_ACTION(0, control, true)` for the few you keep (look, frontend nav). Group `2` = frontend/menu, `0` = gameplay.
**Gotcha:** Lasts ONE frame — re-issue every tick or controls snap back. Re-enable LookLeftRight/LookUpDown if you still want camera. Re-enable gamepad Aim/Attack/Look conditionally on `IS_USING_KEYBOARD_AND_MOUSE(2)`.
**Source:** lemonui/.../Controls.cs, nativeui-guad/.../Controls.cs, menubase-ikt/menucontrols.cpp

### After disabling controls, READ them with the IS_DISABLED_CONTROL_* family
**Category:** input · *seen in 3 mods*
**Problem:** After `DISABLE_ALL_CONTROL_ACTIONS`, `IS_CONTROL_JUST_PRESSED` stops firing for your menu keys.
**Method:** Use the disabled variants: `IS_DISABLED_CONTROL_JUST_PRESSED(0, control)`, `IS_DISABLED_CONTROL_PRESSED(0, control)`. Analog/cursor: `GET_CONTROL_NORMAL(0, control)` → -1..1 (still works on disabled controls; how you read the mouse cursor via CursorX/Y).
**Gotcha:** Plain `IS_CONTROL_*` returns false for a control you disabled this frame — so navigation dies the instant you `DISABLE_ALL`. Always pair disable with the `IS_DISABLED_*` readers.
**Source:** lemonui/.../Controls.cs, menubase-ikt/menucontrols.cpp, ragenativeui/Source/Natives.cs

### Drive any scaleform method: BEGIN → ADD_PARAM (string = 3-native sandwich) → END
**Category:** scaleform · *seen in 3 mods*
**Problem:** Call a scaleform method with mixed int/float/bool/string/texture args in the right order.
**Method:** `BEGIN_SCALEFORM_MOVIE_METHOD(handle, method)` → per arg in order: int `SCALEFORM_MOVIE_METHOD_ADD_PARAM_INT`, float `…_FLOAT`, bool `…_BOOL`, texture `…_TEXTURE_NAME_STRING`; **string = `BEGIN_TEXT_COMMAND_SCALEFORM_STRING("STRING")` → `ADD_TEXT_COMPONENT_SUBSTRING_PLAYER_NAME(s)` → `END_TEXT_COMMAND_SCALEFORM_STRING()`** → finally `END_SCALEFORM_MOVIE_METHOD()`.
**Gotcha:** A string param is NOT a single native — the 3-native text sandwich is mandatory; any other way yields an empty/garbage param. Args must be pushed left-to-right in the method's expected order.
**Source:** menubase-ikt/Scaleform.h, ragenativeui/.../Scaleform.cs, lemonui/.../BaseScaleform.cs

### Instructional-button bar — full rebuild order + rebuild-on-input-change
**Category:** scaleform · *seen in 3 mods*
**Problem:** The bottom-right button-hint bar that auto-swaps KB/controller glyphs.
**Method:** `REQUEST_SCALEFORM_MOVIE("instructional_buttons")`, wait `HAS_SCALEFORM_MOVIE_LOADED`. Per rebuild (via the method-call sequence above): `CLEAR_ALL` → `TOGGLE_MOUSE_BUTTONS(bool)` → `SET_MAX_WIDTH(float)` → per slot `SET_DATA_SLOT(int slot, string buttonId, string label)` → `SET_BACKGROUND_COLOUR(r,g,b,a)` → `DRAW_INSTRUCTIONAL_BUTTONS(0)`. Then `DRAW_SCALEFORM_MOVIE_FULLSCREEN(handle,255,255,255,255,0)` every frame. Get a control's glyph token with `GET_CONTROL_INSTRUCTIONAL_BUTTON(2, control, true)`.
**Gotcha:** Rebuild only on content change OR `HAS_INPUT_JUST_CHANGED(2)` — else glyphs won't swap when the player switches KB↔pad. **Don't cache the glyph token** (differs per input scheme); store the control id, re-query each rebuild. ButtonId encoding: `"t_"/"T_"/"w_"` by caption length, `"b_<id>"` raw symbol (b_44 spinner), group join with `%` in REVERSE order, empty slot = `"t_"`.
**Source:** ragenativeui/.../InstructionalButtons.cs, lemonui/.../InstructionalButtons.cs

### Read a scaleform method's RETURN value asynchronously
**Category:** scaleform
**Problem:** Methods like `GET_NUMBER_OF_ROWS` return a value that isn't ready the same frame.
**Method:** `BEGIN_SCALEFORM_MOVIE_METHOD(...,"GET_NUMBER_OF_ROWS")` → `END_SCALEFORM_MOVIE_METHOD_RETURN_VALUE()` gives an id; later poll `IS_SCALEFORM_MOVIE_METHOD_RETURN_VALUE_READY(id)` then `GET_SCALEFORM_MOVIE_METHOD_RETURN_VALUE_INT(id)`.
**Gotcha:** Async — not ready the same frame; reuse the previous value while polling (re-issue if not ready within ~2 frames). Fallback: `BUSYSPINNER_IS_ON() ? 1 : 0`.
**Source:** ragenativeui/.../InstructionalButtons.cs

### Big-message / shard scaleform (mission passed, rank up, shard banners)
**Category:** scaleform
**Problem:** The large center-screen banners.
**Method:** `REQUEST_SCALEFORM_MOVIE("MP_BIG_MESSAGE_FREEMODE")`. Methods (via the call sequence): `SHOW_MISSION_PASSED_MESSAGE(msg, sub, 100, true, 0, true)`; `SHOW_SHARD_CENTERED_MP_MESSAGE(title, desc, textColor, bgColor)`; `SHOW_BIG_MP_MESSAGE`; `SHOW_WEAPON_PURCHASED`. Draw each frame; dismiss with method `TRANSITION_OUT`; dispose `SET_SCALEFORM_MOVIE_AS_NO_LONGER_NEEDED(&handle)`.
**Gotcha:** Must be drawn every frame to persist. `SHOW_CENTERED_MP_MESSAGE_LARGE` needs an explicit `TRANSITION_IN` follow-up; most others animate in automatically.
**Source:** ragenativeui/.../BigMessage.cs

### Post a feed notification (top-left ticker)
**Category:** notification
**Problem:** Standard GTA notification.
**Method:** `BEGIN_TEXT_COMMAND_THEFEED_POST("STRING")` → `ADD_TEXT_COMPONENT_SUBSTRING_PLAYER_NAME(text)` (split if long) → `END_TEXT_COMMAND_THEFEED_POST_TICKER(blink, showInBrief)`.
**Gotcha:** Same text sandwich as DISPLAY_TEXT but the THEFEED BEGIN/END pair — using the wrong END (e.g. DISPLAY_TEXT's) posts nothing. The 98-byte split rule applies here too.
**Source:** ragenativeui/Source/TextCommands.cs

### Frontend nav sounds: GET_SOUND_ID → PLAY_SOUND_FRONTEND → RELEASE_SOUND_ID
**Category:** audio
**Problem:** Authentic menu blips without leaking sound handles.
**Method:** `id = GET_SOUND_ID()` → `PLAY_SOUND_FRONTEND(id, name, ref, true)` → `RELEASE_SOUND_ID(id)`. `ref = "HUD_FRONTEND_DEFAULT_SOUNDSET"`; names: `NAV_UP_DOWN`, `NAV_LEFT_RIGHT`, `SELECT`, `BACK`, `ERROR`.
**Gotcha:** GET_SOUND_ID allocates from a small pool — for one-shots RELEASE immediately or you exhaust the pool and sounds stop. Keep the id only if you intend to `STOP_SOUND(id)` later. (Relevant to our Claude FM work.)
**Source:** lemonui/.../Sound.cs, nativeui-guad/.../UIMenu.cs
