#
# Army At 6-Stars — PyloaderV 0.6 port (idiomatic API).
#
# Same behavior as the army_v3_example, but using the new
# high-level wrappers: gta.player, gta.world, gta.ped, gta.ui, gta.math.
#
# Put in <GTA V>/pyscript/ and press F9 in-game to reload.
#

import random
import time

import gta
from gta import math as gmath
from gta import player as gplayer
from gta import ui
from gta import world
from gta.natives.player import PLAYER_ID  # for is-shooting/jacking access via ped
from gta.ped import Ped


INTERVAL_MS         = 100
CRIME_THRESHOLD     = 350
DISPATCH_INTERVAL_S = 10.0
DESPAWN_DISTANCE_M  = 200.0
PATROL_OFFSET_MIN   = 30.0
PATROL_OFFSET_MAX   = 60.0

SOLDIER_MODELS      = ("s_m_y_marine_01", "s_m_y_marine_02", "s_m_y_marine_03")
JEEP_MODELS         = ("crusader", "barracks")
WEAPON_CARBINERIFLE = 0xFAD1F743
PED_TYPE_MILITARY   = 29


_crimes           = 0
_six_stars        = False
_next_dispatch_at = 0.0
_spawned: list    = []   # list[Entity]


def _spawn_ground_patrol() -> None:
    me = gplayer.current()
    origin = me.ped.position
    angle = random.uniform(0, 6.28)
    radius = random.uniform(PATROL_OFFSET_MIN, PATROL_OFFSET_MAX)
    pos = gmath.offset_around(origin, radius, angle)

    veh = world.create_vehicle(random.choice(JEEP_MODELS), pos)
    if veh is None:
        return
    _spawned.append(veh)

    for seat, model in zip((-1, 0), random.sample(SOLDIER_MODELS, 2)):
        soldier = world.create_ped(model, pos, ped_type=PED_TYPE_MILITARY)
        if soldier is None:
            continue
        soldier.set_into(veh, seat)
        soldier.give_weapon(WEAPON_CARBINERIFLE, ammo=999)
        soldier.set_as_cop(True)
        soldier.task_combat(me.ped)
        _spawned.append(soldier)

    ui.notify("~g~Army ~w~ground patrol deployed.")


def _release_far_spawns() -> None:
    origin = gplayer.current().ped.position
    for e in list(_spawned):
        if not e.exists:
            _spawned.remove(e)
            continue
        if gmath.distance(e.position, origin) > DESPAWN_DISTANCE_M:
            e.dismiss()
            _spawned.remove(e)


def _release_all_spawns() -> None:
    for e in _spawned:
        if e.exists:
            e.dismiss()
    _spawned.clear()


def _accrue_crimes() -> None:
    global _crimes
    ped: Ped = gplayer.current().ped
    if ped.is_shooting:
        _crimes += 10
    if ped.is_jacking:
        _crimes += 2


def on_start() -> None:
    gta.log("info", "army@6stars script loaded")


def on_tick() -> None:
    global _crimes, _six_stars, _next_dispatch_at

    me = gplayer.current()
    wanted = me.wanted_level

    if wanted < 5:
        if _six_stars or _crimes:
            _release_all_spawns()
            _crimes = 0
            _six_stars = False
        return

    if not _six_stars:
        _accrue_crimes()
        if _crimes > CRIME_THRESHOLD:
            _six_stars = True
            _next_dispatch_at = time.monotonic()
            ui.notify("~w~This is the US President. ~g~All military agencies "
                      "~w~have been mobilized. Remain calm.")
        return

    now = time.monotonic()
    if now >= _next_dispatch_at:
        _release_far_spawns()
        _spawn_ground_patrol()
        _next_dispatch_at = now + DISPATCH_INTERVAL_S


def on_key_down(vk: int, down: bool, shift: bool, ctrl: bool, alt: bool) -> None:
    if down and vk == 0x6D:  # numpad minus = reset
        _release_all_spawns()
        globals().update(_crimes=0, _six_stars=False)
        ui.notify("Army script reset.")


def on_aborted() -> None:
    _release_all_spawns()
