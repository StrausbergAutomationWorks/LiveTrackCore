# Aircraft marker icons - attribution

The SVG files in `icons/` beside this file are the aircraft marker icons of the
Live Track integrations (Live Track Aircraft, Live Track Enforcement Aircraft).
They moved here from Live Track Aircraft on 24 October 2026 unchanged, so that
every integration draws an aircraft the same way.

## ADS-B Radar

40 of the 64 SVG files here are by **ADS-B Radar** — https://adsb-radar.com

Source: https://adsb-radar.com/help/icons.html

Terms, as stated on that page (read 2026-09-21): the icons are free to use
for personal and commercial projects, and the only requirement is a
backlink to ADS-B Radar in the project, website or documentation. This
file, the module's docstring and the Aircraft icons section of the
package's `README.md` are that backlink.

The icons belong to their author. They are not covered by the MIT licence
in `LICENSE`, which applies to the code in this package.

His files: `a0`, `a1`, `a2`, `a3`, `a4`, `a5`, `a6`, `a7`, `a320`, `a330`,
`a340`, `a380`, `amphibian`, `b0`, `b1`, `b2`, `b3`, `b4`, `b737`, `b747`,
`b767`, `b777`, `b787`, `beechcraft`, `c0`, `c130`, `cessna`, `crjx`, `dh8a`,
`e195`, `erj`, `f100`, `f11`, `f15`, `f5`, `fa7x`, `glf5`, `heavytransport`,
`learjet`, `md11` (each `.svg`).

### Which icon each aircraft type gets

The type-to-icon table in `__init__.py` is the default lists from the ADS-B
Radar app, shared by its author, Jos Nutbroek, in September 2026, in the
version he sent on 25 September 2026. `beechcraft.svg` (a light twin, used
for the King Airs, Barons and their kind) is his, sent the same week; the file
he sent is named `beachcraft.svg` and ships unchanged under the name its list
key uses. Since his list files the King Airs under it, `kingair.svg` is no
longer used by the table. On 28 September 2026 he sent two more icons and
their lists: `heavytransport.svg` (his file `heavyfreighter.svg`, shipped
unchanged under the name its list key uses) and `amphibian.svg`.

### Changes made to his files

The files ship as downloaded except three. `a7.svg` (the helicopter): its two
rotor blades are grouped and animated so the rotor spins; the shapes are
unchanged. `cessna.svg` and `beechcraft.svg`: his outline is kept byte for
byte, a clip hides the propeller band beside each spinner, and the propeller
is redrawn from his own outline in a group that animates it (26 September
2026). A note in each file says so. The type list is followed except where
`__init__.py` overrides it: EN28 (Enstrom 280), CH7A/CH7B (Helisport CH-7)
are drawn as helicopters rather than light aircraft; V22 and B609 use the
tiltrotor; the low-wing singles on the Cessna line use `lowwing.svg`; P68,
listed under two icons, uses the twin; B350 uses `kingair350.svg`; and the
types in `_ROUND3` (twins, canards, biplanes, fighters, gyroplanes, the
Chinook, the Kamovs, the K-MAX, the B-2, the Avanti and the Cheyenne 400), in
`_PILATUS` (the Pilatus types) and in `_ROUND4` (the amphibians and the Beriev
A-50) use the icons below. `render()` produces altered copies: editor
metadata is removed so browsers parse them strictly, the hardcoded fill is
replaced, a dark outline is drawn round the silhouette, and each icon is
scaled by aircraft category. Icons point north; the map card turns them to
the direction of travel.

## Drawn for StrausbergAutomationWorks

The other 24 files were drawn for this project in the style of the set and
donated to ADS-B Radar, so they are under its terms above too; the MIT licence
in `LICENSE` covers the code only. They are `kingair.svg` (offered
to the set's author), `lowwing.svg` (a low-wing single), and `tiltrotor.svg` /
`tiltrotorfwd.svg` (a tiltrotor hovering and in cruise, after an
AgustaWestland AW609 three-view), and, added 26 September 2026: `tandem.svg`
(CH-47 Chinook), `coaxial.svg` (Kamov), `kmax.svg` (Kaman K-MAX), `gyro.svg`
(autogyro), `flyingwing.svg` (B-2), `fighter.svg` (F-16 planform),
`biplane.svg` (Stearman), `canard.svg` (Rutan Long-EZ), `p180.svg` (Piaggio
P.180 Avanti) and `cheyenne.svg` (Piper PA-42 Cheyenne 400), each drawn to the
type's published dimensions; and `pc6.svg`, `pc7.svg`, `pc9.svg`, `pc12.svg`,
`pc21.svg` and `pc24.svg`, whose outlines are traced from the top views in
Pilatus Aircraft Ltd's model building plans (https://www.pilatus-aircraft.com)
and scaled to Pilatus's published spans. Added 6 October 2026, drawn to each
type's published dimensions: `be12.svg` (Beriev Be-12), `be200.svg` (Beriev
Be-200) and `us2.svg` (ShinMaywa US-2). Their propellers and rotors are
animated. Added 24 October 2026: `kingair350.svg` (Beechcraft King Air 350),
drawn to the type's published span, length, tailplane and propeller diameter,
with its shapes read from NASA photograph ED08-0141-21 (public domain, via
Wikimedia Commons). It takes the aircraft's tone and keeps its own black
windscreen and window band.

Pictures of single airframes (the presidential aircraft) are not here: they
belong to the integration that draws them.
