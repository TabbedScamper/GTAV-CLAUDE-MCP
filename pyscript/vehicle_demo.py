"""
vehicle_demo.py — spawn an Adder in front of the player when F6 is pressed.

Demonstrates:
  - gta.invoke(hash, *args, return_type=...) with mixed int/float/str/bool args
  - return_type='vector3' to unpack Vector3 natives
  - gta.wait(ms) to cooperatively yield inside on_tick
"""

import gta

INTERVAL_MS = 0        # tick every frame so F6 feels snappy
__priority__ = 50


# --- Native helpers ----------------------------------------------------------

def get_player_ped():
    # PLAYER_PED_ID (returns Ped handle)
    return gta.invoke(0xD80958FC74E988A6, return_type="handle")


def get_entity_coords(entity, alive=True):
    # GET_ENTITY_COORDS(entity, alive)
    return gta.invoke(0x3FEF770D40960D5A, entity, alive, return_type="vector3")


def get_entity_heading(entity):
    # GET_ENTITY_HEADING(entity)
    return gta.invoke(0xE83D4F9BA2A38914, entity, return_type="float")


def get_offset_from_entity(entity, off):
    # GET_OFFSET_FROM_ENTITY_IN_WORLD_COORDS(entity, x, y, z)
    return gta.invoke(0x1899F328B0E12848, entity, *off, return_type="vector3")


def request_model(model_hash, timeout_ms=5000):
    # REQUEST_MODEL
    gta.invoke(0x963D27A58DF860AC, model_hash, return_type="void")
    # HAS_MODEL_LOADED
    elapsed = 0
    while not gta.invoke(0x98A4EB5D89A0C952, model_hash, return_type="bool"):
        gta.wait(50)
        elapsed += 50
        if elapsed > timeout_ms:
            return False
    return True


def create_vehicle(model_hash, pos, heading):
    # CREATE_VEHICLE(model, x, y, z, heading, networkHandle=False, netMission=False)
    return gta.invoke(
        0xAF35D0D2583051B0,
        model_hash,
        float(pos.x), float(pos.y), float(pos.z),
        float(heading),
        False, False,
        return_type="handle",
    )


def set_model_no_longer_needed(model_hash):
    # SET_MODEL_AS_NO_LONGER_NEEDED
    gta.invoke(0xE532F5D78798DAAB, model_hash, return_type="void")


# --- Callbacks ---------------------------------------------------------------

def on_start():
    gta.notify("~y~vehicle_demo loaded — press F6 to spawn an Adder")


def on_key_down(vk, down, shift, ctrl, alt):
    if not down or vk != 0x75:  # VK_F6
        return

    ped = get_player_ped()
    if ped <= 0:
        gta.notify("~r~No player ped")
        return

    # Hash of "adder" computed at compile time. You can also use
    #   GET_HASH_KEY = 0xD24D37CC275948CC
    # to compute at runtime, but literal hashes avoid a native round-trip.
    ADDER = 0xB779A091  # Jhash("adder")

    # We can't compute the Jhash locally without the algorithm; use GET_HASH_KEY.
    model_hash = gta.invoke(0xD24D37CC275948CC, "adder", return_type="uint")

    if not request_model(model_hash):
        gta.notify("~r~Model load timed out")
        return

    heading = get_entity_heading(ped)
    # Spawn 5 metres in front of the player.
    spawn_pos = get_offset_from_entity(ped, (0.0, 5.0, 0.0))
    veh = create_vehicle(model_hash, spawn_pos, heading + 90.0)

    set_model_no_longer_needed(model_hash)
    gta.notify(f"~g~Spawned adder (handle {veh})")
