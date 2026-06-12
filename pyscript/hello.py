"""
hello.py — minimal PyloaderV demo.

Callbacks recognized by the host:
    on_start()                              : called once, ~first tick
    on_tick()                               : called every INTERVAL_MS (0 = every frame)
    on_key_down(vk, down, shift, ctrl, alt) : called on keyboard events
    on_aborted()                            : called on shutdown / reload

Module-level configuration:
    INTERVAL_MS    : int, tick period (default 0 = every frame)
    __priority__   : int, load order (lower loads first, default 100)
"""

import gta

INTERVAL_MS = 1000          # tick every second
__priority__ = 0            # load before anything else

_frames = 0


def on_start():
    gta.notify("~g~hello.py loaded")
    gta.log("info", "hello.py on_start()")


def on_tick():
    global _frames
    _frames += 1
    if _frames % 10 == 0:
        gta.log("info", f"hello.py alive, tick #{_frames}")


def on_key_down(vk, down, shift, ctrl, alt):
    if down and vk == 0x70:  # F1
        peds = gta.get_all_peds()
        vehs = gta.get_all_vehicles()
        gta.notify(f"~b~peds={len(peds)}  vehicles={len(vehs)}")


def on_aborted():
    gta.log("info", "hello.py on_aborted()")
