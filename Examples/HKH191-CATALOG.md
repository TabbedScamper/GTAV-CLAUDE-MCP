# HKH191 mod catalog — the harvestable capability index (~128 mods)

The full HKH191 SP-mod catalog is in `Examples/_sources/HKH191/` (git-ignored DLLs). Every mod is SHVDN C#
and depends on **HKHModHelperNew.dll** (the shared toolkit — PATTERNS/17). This index says *what each mod
provides* so Claude can pick the right one to decompile + harvest when the user wants a capability. Decompile
any with `ilspycmd <dll>`; distill techniques into PATTERNS (never copy code). ⭐ = already studied
(PATTERNS/14-17). Build on Legacy (the user's edition); most ship Enhanced support too.

## Mission / heist engines (the auto-generator's reference implementations)
- ⭐ **HKH's MissionHeist Creator 1.1.1** — DATA-DRIVEN mission builder, 105 objective types, INI schema, runtime interpreter. THE template for our generator. (PATTERNS/16)
- ⭐ **Single Player Heists 1.3** (+ SmartGuards) — the GTAO heists ported to SP (Cayo overhaul); 306k-line framework.
- ⭐ **Cayo Perico Heist in SP 11.0** — gold-standard player-driven heist: marker→Press E→synced-scene beats. (PATTERNS/15)
- **The Contract in SP 1.3**, **The ECU Job**, **Project Overthrow In SP 2.0**, **Cluckin Bell Farm Raid 1.4**, **Oscar Guzman Flies Again 1.4** — more SP heist/mission ports (SmartGuards-based); same engine, different content/coords.
- **Cayo Perico Wave Survival 2.0**, **TP3.6 (The Payday)**, **Gerald's Stash Houses 1.2**, **Gerald's Caches 2.0** — survival/payday/stash loops; reusable spawn+objective patterns.

## Heist interactions & minigames (player-driven beats)
- ⭐ **Hacking Minigames in SP** — 13 real GTAO hacking minigames (circuit/voltage/fingerprint/beam/hotwire/PCB/bruteforce…) with the HackFinishedID completion contract. (PATTERNS/17)
- **Placeable Casino Games 3.0** — poker/blackjack/roulette (full card/table minigame logic + UI).
- **BeaterDukesMiniGame** — a vehicle-repair timing minigame (QTE pattern).

## Guard / enemy AI (stealth + combat)
- ⭐ **SmartGuards** (bundled in the heist mods) — vision cone + LOS raycast + investigate-before-combat. (PATTERNS/15)
- **Advanced Bodyguards & Enemy Menu 1.0** — spawn/configure friendly+enemy squads via menu (relationship/combat setup).
- **Roaming Bosses**, **Akula Stealth Systems in SP** — ambient boss encounters; stealth detection systems.

## Interiors / world / map (walk-in + scenery)
- ⭐ **Enable All Interiors 43.2** — IPL load + entity sets + DOOR_SYSTEM auto-unlock (walk-in). (PATTERNS/15)
- **GTAO Mansions In SP 2.1**, **GTAO Diamond Casino In SP 3.2**, **Facilities in SP**, **Bunkers in SP**, **Safehouse Reloaded 12.0** — loadable GTAO interiors + their entity-set configs (vault/security/decor).
- **MenyooMap_to_ObjectPlacementMap 2.0** — converter: Menyoo .xml maps → the ObjectPlacementMap format the mission engine spawns. Lets us import community map placements as mission sets.

## Tools directly useful for OUR toolkit
- ⭐ **HKHModHelper (HKH Mod Helper 12.0)** — the 271-helper shared library: scaleform win/lose screens, money, streaming, control, geometry, attach, audio, player-lock. (PATTERNS/17)
- **RecordMyRoute** + **3D Waypoint Routes 1.0.1** — record a player path → waypoint route (for patrols / drive objectives / chase routes). Harvest for route capture.
- **Cutscene Player 3.0** + **Free Look Cutscenes 1.0** — play R* cutscenes / scripted cams in SP (REQUEST_CUTSCENE flow).
- **Ini Changer**, **Where Am I** (coord readout), **Mark Your Death** (death-marker) — small utilities.

## Businesses (economy loops — data-driven sell/resupply missions)
Executive Business, MC Businesses 5.1, Biker Business, Gunrunning, Smuggler's Run, ArenaWar Business,
Doomsday Heist Business, Lamars Custom Classics, Meth Dealing, Drug Trafficking V2 6.1, Illegal Gambling
Dens, The Black Market, GTAO Businesses 1.6, Business Helper, The Autoshop in SP 1.4, Working GSY 9.0,
Vinewood Club Garage. — All share a resupply→produce→sell-mission loop worth modeling for "endless" economy
content; Business Helper is the shared base.

## Vehicles / garages / special vehicles
GTAO Garages 3.1, EvenMoreGarages, Open 60 Car Garage, Persistent Rides 5.2, AVF 2.0 (advanced vehicle
functions), Special Vehicle Abilities, Working MOC/Terabyte/Avenger/Bomber/Kosotka Submarine/Flatbed/
Skylift/Juggernaut Suit, Mors Mutual Insurance, Luxury Auto Dealership, LS Dealerships 4.0, Simeon's
Vehicles, Drift Swap, Functional JB700, Akula Hidden Missiles, Gunship Autopilot.

## Weapons / combat / police
MK2 Ammo Giver 17.1, San Andreas Weapon Pickups, Throwable Melee Weapons, Flamethrower, NavyRevolver,
Limited Vehicle Ammo, Biker Melee, KillConfirmed, Phone Detonation. — Police: WantedConsequences, Enhanced
Traffic Laws 7.0 (zone spawning/search/impound), LSPDFR Bait Car, WantedD_or_A.

## Player / companion / misc gameplay
Player Companion 13.0, PersonalDriver, Advanced Actions 2.0, Mobile Wardrobe, Inventory Script 1.0,
Playable Online Character 4.1, Personal Hooker/Stripper, Vinewood Celebrity. — Events/cosmetic: Halloween
Events, Christmas Decorations, Graffiti Tags 2.0. — Activities: Drag Meets V, GTAO/HSW Time Trails, Enhanced
Trains, Walk On Water, Auto Boat Anchor, Manual Pickups, The Gun Van, The Music Locker, Lester's Bribes,
Executive VIP Service, Yusuf Amir's Tow Service, SlamtruckCarryMyVehicle, YourInAStolenVehicle, Working
Wastelander/MMV, GTAO Nano Drone, Active Camo, SPGR, Load Hatches, ParkingSpotsV.

> Harvest rule (CLAUDE.md): decompile into `Examples/_decompiled/`, distill a PATTERNS card WITH attribution,
> regen recipes — never commit their source. _sources/_decompiled are git-ignored.
