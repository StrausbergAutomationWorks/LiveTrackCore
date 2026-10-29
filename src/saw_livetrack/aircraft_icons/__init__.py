"""Aircraft marker icons for the Live Track integrations (saw-livetrack 0.2.11, backlog 298).

The type table, the artwork, render(), the tones, the lights, the police flash and
ART_REV, moved verbatim from Live Track Aircraft's icons.py at ac45b2e (2026-10-24)
so Live Track Enforcement Aircraft draws a listed aircraft exactly as Live Track
Aircraft does. No picture changed: every icon in every tone, lights state, weight,
badge and angle was rendered before and after and compared byte for byte.

A consumer keeps its own HTTP view and URL (picture_query() gives the query string)
and any picture of ONE airframe by Mode S hex. No Home Assistant import, so it tests
without it. ART_REV bumps with ANY change to what a picture shows; the test's golden
digests (tests/aircraft_icons_golden.json) pin every picture.

The notes below are Live Track Aircraft's, kept as written: "the integration" is Live
Track Aircraft and "the card" LiveTrackMapCard. Licence of the artwork: ATTRIBUTION.md
beside this file.

Aircraft marker icons, rotated to the direction of travel.

Icons by ADS-B Radar for macOS - https://adsb-radar.com
! THE LICENCE REQUIRES A BACKLINK. "Free to use for personal and commercial
projects. The only requirement: please provide a backlink to ADS-B Radar
somewhere in your project, website, or documentation." That backlink lives in
the README and in ATTRIBUTION.md, and must survive any rewrite of either.

! ONE ICON IS NOT HIS: kingair.svg was drawn for this project on 2026-09-23,
in his style, and given to him for his set (his app files the King Air under
the Cessna). ATTRIBUTION.md says so. It points north like the rest, checked by
rendering it beside his Cessna and Dash 8 at 512, 64, 40 and 28 px.

! EVERY ICON POINTS NORTH. Verified by rendering all 37 and looking at them,
not by reading the XML - the SVGs carry baked-in transforms that make the
source misleading. So rotation is the bearing applied directly, with no
per-icon offset table.

! THE CARD TURNS THE MARKER, since 2026-09-25 (Lee), with no option: every
picture is served UNROTATED and without a disc, and LiveTrackMapCard turns it
from icon_rotation_deg. render() can still turn an icon, for the view's
angle URLs, but the integration never issues one. Before this, rotation and
the disc were per-entry options (card rotation, silhouette without its
disc), default OFF so any card would draw correctly; Live Track Ships dropped the same
options the same day, on the ground that strangers get our map card.

! THE HELICOPTER'S ROTOR SPINS (Lee, 2026-09-25), ported from Live Track
Ships' SAR helicopter: a7.svg's two blades sit in their own group with an
animateTransform about the hub. It animates whatever the aircraft does,
hovering included, as Ships' does.
"""

from __future__ import annotations

import re

# --- emitter category -> icon -------------------------------------------
# a0-a7 and b1-b4 map onto the ADS-B categories exactly.
#
# ! TWO DELIBERATE EXCEPTIONS, because the file names do not match the
# standard: b0.svg is a QUADCOPTER, but category B0 means "no category
# information" while B6 means UAV and has no file of its own. A drone drawn
# on every aircraft whose category is merely unreported would be actively
# misleading, so B6 takes the drone and B0 takes the generic airliner.
_BY_CATEGORY = {
    "A0": "a0", "A1": "a1", "A2": "a2", "A3": "a3",
    "A4": "a4", "A5": "a5", "A6": "a6", "A7": "a7",
    "B0": "a0",     # no information - NOT the drone
    "B1": "b1",     # glider
    "B2": "b2",     # lighter-than-air: airship or balloon, seen from above
    "B3": "b3",     # parachutist - a canopy from above
    "B4": "b4",     # ultralight, hang-glider, paraglider
    "B6": "b0",     # UAV - the quadcopter
    "C0": "c0", "C1": "c0", "C2": "c0",   # surface, filtered at the source
}

# --- ICAO type designator -> a specific silhouette ------------------------
# Checked before the category, because "a Boeing 737" beats "a large aircraft".
#
# ! THE BASE IS THE ICON AUTHOR'S OWN TABLE, AS HE SENT IT ON 2026-09-25 (Lee:
# "adopt his new list with our overrides"). 909 codes against the 539 before,
# with his new `beechcraft` icon (a low-wing light twin, his file
# beachcraft.svg, installed byte-exact as icons/beechcraft.svg) for the King
# Airs, Barons, Senecas and their kind; our kingair.svg is no longer used by his
# list and stays shipped. Verbatim below, lower case, his order. His earlier
# lists, and the corrections we sent him:
# ! (history) THE BASE WAS THE ICON AUTHOR'S OWN TABLE. Jos Nutbroek (ADS-B Radar) sent the
# app's full default type -> icon lists on 2026-09-22; we corrected them and sent
# the corrections back on 2026-09-23 (20 codes filed under two icons, 737-600/800
# on the 767, A350 on the A340, the Falcon 2000 on the Dash 8, CRJs on the F100,
# and 30 designators the feeds actually carry). This is that corrected version,
# verbatim, lower case as he writes it. Record:
# 30_Integrations/Live_Track_Aircraft/30_Correspondence/20260923_adsb-radar_fixes/
# ! Many entries are model NAMES (crj200, phenom100) that never appear in a
# feed, which sends ICAO Doc 8643 designators. They are harmless and kept so
# this stays his list; the designators sit beside them.
# ! It includes his non-ICAO keys (FLARM f-codes, emitter categories as codes).
# The emitter category itself is still resolved by _BY_CATEGORY, below the type.
_AUTHOR_LISTS: dict[str, str] = {
    "a320": "a320,a20n,a318,a21n,a319,a321,bcs1,bcs3,c919,a19n,e390",
    "a330": ("a330,a300,a310,a332,a333,a338,a306,a339,a350,a359,a35k,a30b,a337,"
             "su95,a3st"),
    "a340": "a340,a343,a346,a342,a345,vc10,il76,il96,a50,a124,a225",
    "a380": "a380,a388",
    "b737": ("b737,b38m,b733,b734,b731,b732,b735,b739,b39m,b738,b736,b37m,b3xm,"
             "b721,b722,b73q"),
    "b747": "b741,b742,b743,b747,b744,b748,b74d,b74s,b74r,blcf,bsca",
    "b767": "b757,b753,b752,b767,b707,b763,b762,b764,b703,e3cf,e3tf",
    "b777": "b777,b772,b77l,b773,b77w,b779,b778",
    "b787": "b787,b788,b789,b78x",
    "c130": ("c130,c130h,c130j,c130j30,c135,c135b,c135c,c30j,c27j,c17,c160,a400,"
             "k35r,an12,c5m,an26,an24,an32,r135,c295"),
    "cessna": ("b209,c172,c172r,c172s,c152,c182,cruz,m20,m20j,m20k,m20p,m20t,c208,"
             "pc12,pc24,be33,c206,c340,sira,jfox,p46t,fk9,f260,pc6t,dr40,slg2,"
             "slg4,slgp,slg5,vl3,br23,br24,ct2k,ctsw,ctls,fk14,icp2,icp3,ev97,"
             "evss,wt9,wt91,rans,tl20,tl22,tl200,tl300,p96g,tec2,tec4,stin,skyr,"
             "ch60,ch70,ch80,ch200,ch650,p68,p68t,vtor,fdct,pa11,pa12,pa14,pa15,"
             "pa16,pa17,pa18,pa20,pa22,pa24,pa25,p28a,p28b,p28r,p28s,p28t,p28u,"
             "pa32,pa38,twen,pc21,da40,dv20,pc9,tb20,da50,g36,c42,sr22,tbm8,"
             "c421,c82s,l5,tbm9,dr10,s22t,pivi,suba,hr20,p208,toba,rv6,rv7,pc7,"
             "lnc2,t206,echo,cres,tb30,ng5,bt36,wa40,col4,shrk,veze,g115,g120,"
             "m700,p210,aa5,st75,bx2,rv12,alsl,tipb,bu31,e300,r90r,z42,p32r,"
             "as02,efox,v252,exng,dv1,tamp,dr30,svnh,z37p,junr,mcr4,aa1,glst,"
             "d140,kl07,gy80,dal4,c77r,ap32,eupa,dr22,velo,brez,coro,rall,k100,"
             "ap22,pnr2,sako,c72r,c150,c82t,be36,be35,wilt,tbm7,ac11,c425,c162,"
             "c177,c185,c210,sr20,pa28,da20,pnr3,m600,tbm7,a210,c425,bw60,sd4,"
             "bl8,rv4,rv10,lxr,c82r,rv8,tl30,c10t,d250,pnr4,leg2,d11,nnja,pa46,"
             "husk,coy2,bl17,rv9,rf6,j3,sah1,g12t,crer,c195,y18t,cp10,aat3,mp02,"
             "sa75,mm24,spit,huri,t6,l8,pup,ch7a,yk52,eagl,fk12,c180,waix,tour,"
             "c175,mcr1,at8t,sonx,at5t,p51,vr7,lgez,lnp4,s108,c207,epic,be24,"
             "prim,c240,be23,pay4,rs21,en28,ta15,be19,avid,tb21,ch7b,t34p,cozy,"
             "p32t,m7,cc11,fa24,cb1,gx,c170,gc1,t210,sa30,t38,ss2t,rv14,alig,"
             "erco,a660,wacf,ch65,pp3,la4,tuca,mc01"),
    "beechcraft": ("be20,b350,be30,be9l,be9t,be10,sw4,sf34,c408,b190,be58,e120,pa44,"
             "l410,p06t,pay1,be76,d28d,e121,pa30,c402,f406,pa34,piat,pa23,pa31,"
             "c310,be55,be95,c320,l90,e110,p212,pa27,p337,c414,c441,ac50,c337,"
             "expr,be18,aest,be50,mu2,v22,pat4,a748,ac68,ac90,be99,bn2p,c212,"
             "p68"),
    "crjx": ("crjx,crj,crj100,crj440,crj700,crj705,crj900,crj900er,crj1000,"
             "crj1000er,crj9,crj1,crj2,crj7"),
    "dh8a": ("dh8a,dh8b,dh8c,dh8d,at75,da42,da62,at76,at43,at45,at46,at72,340a,"
             "atr72,d328,j328,sb20,d228,m28,dhc6,p180,f50,f27,dhc7,sc7,dh89,"
             "cl2p,dc3,at73,atp,belf,cn35,cvlp,cvlt,dhc4,dhc5,g159,i114,il18,"
             "js31,js32,js41,l188,n262,noma,sh33,sh36,tris,ys11,ber2,cl2t,g21,"
             "g73t"),
    "e195": "e195,e190,e290,e295,e170,e75s,e175,e75l,e275",
    "erj": "erj,erj135,erj140,e135,e145,e45x,e35l,erd,er4",
    "f100": ("f100,md81,md82,md83,md87,md88,md90,md95,ba146,at146,avro,rj,"
             "avrorj70,avrorj100,f70,b712,b461,b462,b463,rj70,rj85,rj1h,aj27,"
             "f28,ba11,dc91,dc92,dc93,dc94,dc95,b720,il62,il86,t134,t154,t204,"
             "yk40,yk42"),
    "fa7x": ("fa7x,fa8x,fa50,fa2000,fa2000lx,fa2000s,fa2000ex,glf6,glf7,gl7t,"
             "f2th,f900,25m,fa6x"),
    "glf5": ("glf3,glf4,glf4e,glf5,g650,be40,e50p,e55p,e500,e550,g450,cl60,cl65,"
             "ga6c,gl5t,e545,g280,ea50,galx,ga5c,ms76,glex,ga7c,ga8c"),
    "learjet": ("c25a,c25b,c25c,c525,c525a,c525b,c525c,c510,c510a,c510b,c56x,c560,"
             "c560xl,c56,cl35,cl30,c680,c680a,c68a,c650,c650er,c750,c750er,lj35,"
             "lj35a,lj35a3,lj45,lj45ea,lj45xr,lj60,lj60xr,phenom100,phenom300,"
             "hawk100,hawk125,prm1,c700,hdjt,m345,sf50,ajet,l39,lj55,h25b,c55b,"
             "lj75,c500,c550,fa10,fa20,c25m,astr,lj31,ha4t,lj40"),
    "md11": "md11,b727,dc10",
    "a7": ("a7,f3,f03,ec20,ec25,ec30,ec35,ec35e,ec135,ec135t2,ec145,ec145t,"
             "ec145t2,ec145t2m,ec155,ec155b,ec155b1,ec175,as32,as350,as355,"
             "as365,as365n,as365n2,a129,a129d,a139,bk117,bk117a,bk117b,bk117c,"
             "bk117d,bk117c1,bk117c2,bk117c3,bk117c4,bk117c5,h60,h60m,h145,"
             "h145m,h47,h64,h65,s76,mi8,mi17,mi24,mi28,mi35,mi26,ka27,ka32,ka50,"
             "ka52,ec45,b429,a109,bk17,ec75,ec55,expl,as50,as55,as65,h160,a169,"
             "b06,r44,ac10,h500,g2ca,mm16,gyro,r66,h269,sh09,mt,gazl,kmax,as3b,"
             "b407,b505,r22,b206,puma,lynx,nh90,a119,b212,b412,b609,s58t,s92"),
    "b0": "b0,b5,b6,b7,f13,vfhc",
    "b1": ("a21c,arcp,arcus,arcusm,arcust,ask13,ask18,ask21,ask23,as15,as17,"
             "as19,as20,as22,as24,as27,as28,as29,as30,asti,twinasti,b1,califa21,"
             "dg1000,dg1000t,dg300,dg40,dg400,dg505,dg600,dg808,discus,discus2,"
             "disc,duod,f01,f1,f02,f2,fox,glid,hph304,hph304e,hph304s,janm,"
             "janusc,janc,jans,js1,js3,js5,js7,js8,ka6,ka6cr,k6,la12,la17,la19,"
             "l13,l23,libe,ls01,ls03,ls04,ls06,ls07,ls08,ls09,ls10,ls11,"
             "mini-nimbus,mistralc,perkoz,phoe,phoebus,pik20,pik20b,pik20d,"
             "pik20e,swift,s1,sz30,sz36,sz48,sz50,taurus,ultraligh,ven2,ventus2,"
             "ven3,ventus3,venb,ventusb,venc,ventusc,vent,dimo,sf25,rf3,clon,"
             "g109,ball,cdus,rf10,as16,glim,skra,cp30,f8l,pts2,sc01"),
    "b2": "b2,f12",
    "b3": "b3,f4,f04",
    "b4": ("b4,f6,f7,f06,f07,paraglide,ulac,b4208,foka4,foka5,foka6,foka7,"
             "szd56,diana2,szd59,szd54,szd48,szd42,szd41,ls4,ls6,ls8,ls9,"
             "discus3,asw20,asw22,asw24,asw27,asw28,asw29,asw30,ship,aura"),
    "c0": "c0,c1,c2,c3",
    "f5": "f5,f05",
    "f11": "f11",
    "f15": "f15,twr",
    # ! HIS LINES OF 2026-09-28, verbatim, with two new icons of his (his files
    # heavyfreighter.svg and amphibian.svg; the first ships as heavytransport.svg,
    # the name its line uses, unchanged). Placed LAST so they win over his older
    # lines for the codes they move: A124, IL76 off a340; C5M, C17, A400 off c130;
    # CL2P, CL2T off dh8a. ! A225 is no longer an ICAO designator, and CL4T, CL215,
    # CL415, CANSO and PBY are model names a feed never sends: harmless, kept as
    # his list; the designators sit in _ROUND4 below.
    "heavytransport": "a124,a225,il76,c5m,c17,a400,an22",
    "amphibian": "cl2p,cl2t,cl4t,cl215,cl415,dhc2,dhc3,canso,pby",
}

# ! OURS, where his lists are SILENT - never over an author entry. Designators
# seen overhead or chosen before his lists arrived. Where ours and his disagreed
# (2026-09-23, five codes) HIS won: PC12 -> cessna (a single turboprop, not a
# jet), E35L -> erj (built on the ERJ-135), CL35 -> learjet, E50P and E55P ->
# glf5. The Phenoms were on learjet here; glf5 is his call, kept as his.
_OURS = {
    "A19N": "a320",
    "SR20": "cessna", "SR22": "cessna", "C162": "cessna", "C177": "cessna",
    "C210": "cessna", "C72R": "cessna", "PA28": "cessna", "BE36": "cessna",
    "COL3": "cessna",
    "SF34": "dh8a", "B190": "dh8a", "SH36": "dh8a",
    "G280": "glf5", "GALX": "glf5", "GL5T": "glf5",
    "LJ31": "learjet", "LJ75": "learjet",
    "L100": "c130",
    # ! THE E-JETS ARE e195, NOT erj - erj draws REAR-mounted engines (the
    # ERJ-145); the E170/175 carry theirs under the wing. His list agrees.
    # ! THE MD-80/90 AND 717 ARE f100, NOT md11 - md11 draws a TRIJET. His list
    # agrees. DC10 is a trijet and is ours alone.
    "DC10": "md11",
}

# ! OVERRIDES of an author entry, where his is wrong for what we draw. Lee,
# 2026-09-25: "We can override Jos's table when necessary." Every entry here
# says why, and each is worth sending back to him for his set.
_OVERRIDES = {
    # The Robinson R22 is a two-seat HELICOPTER. His lists before 2026-09-25
    # filed it under the cessna; his list of 2026-09-25 moved it to a7
    # himself, so this now agrees with him and is kept as the pin.
    "R22": "a7",
    # ! THE KING AIR 350 HAS ITS OWN ICON (Lee 2026-10-24, draft D of
    # 20_Research/kingair_20261024/): ours, drawn to published dimensions,
    # data-paint="keep" for its black windscreen and window band, lit toward
    # the spine. His list files B350 under beechcraft; BE20/BE30 stay there.
    "B350": "kingair350",
    # ! HELICOPTERS AND A GYRO ON HIS `cessna` (2026-09-25 list), by his own
    # descriptions table: EN28 "Enstrom F-28 / 280 Light Piston Civilian
    # Helicopter", CH7B "CH7 Helisport Charlie Ultralight Sports Kit
    # Helicopter" (CH7A its sibling; a CH7B was overhead on 2026-09-25 drawn as
    # an aeroplane), MM24 "Magni M24 Orion Enclosed Gyrocopter".
    # MM24 was pinned to a7 here too; since round 3 (2026-09-26) it is a
    # gyroplane on the `gyro` icon, with every ICAO class-G type (_ROUND3).
    "EN28": "a7", "CH7A": "a7", "CH7B": "a7",
    # ! V22 (Osprey) is a TILTROTOR; his list puts it on `beechcraft`. It gets
    # its own tiltrotor icon later (Lee, 2026-09-25); until then the
    # helicopter, so it stays a rotorcraft in colour and on the air_low layer.
    "V22": "a7",
    # ! TILTROTORS get their own two icons (backlog 260, Lee 2026-09-25): V22
    # was a7 until they existed; B609 (AW609) is on his a7. `tiltrotor` is
    # hovering, `tiltrotorfwd` in cruise; tiltrotor_forward() below picks.
    "V22": "tiltrotor", "B609": "tiltrotor",
    # ! P68 (Partenavia, a light twin) is on TWO of his lines, cessna and
    # beechcraft. His app resolves it by his case order; we set it here so it
    # does not depend on dictionary order. The twin.
    "P68": "beechcraft",
}

_BY_TYPE: dict[str, str] = {
    code.strip().upper(): key
    for key, codes in _AUTHOR_LISTS.items()
    for code in codes.split(",") if code.strip()
}
for _code, _key in _OURS.items():
    _BY_TYPE.setdefault(_code, _key)
# ! LOW-WING SINGLES, taken off his `cessna` (a high wing) onto our `lowwing`
# (backlog 260, Lee 2026-09-25). By type knowledge, NOT measured: only types
# that are certainly low-wing singles are here; the unsure ones stay on his
# cessna and are listed in backlog 260 for Lee.
_LOWWING = (
    "A210", "AA1", "AA5", "AAT3", "AC11", "ALIG", "AT5T", "AT8T", "BE19",
    "BE23", "BE24", "BE33", "BE35", "BE36", "BL17", "BR23", "BR24", "BW60",
    "C240", "CH200", "CH60", "CH65", "CH650", "COL4", "CP10", "CRUZ", "D11",
    "D140", "DA20", "DA40", "DA50", "DR10", "DR22", "DR30", "DR40", "DV20",
    "E300", "EPIC", "ERCO", "EV97", "EVSS", "EXNG", "F260", "FK14", "G115",
    "G120", "G12T", "G36", "GC1", "GY80", "HR20", "HURI", "LEG2", "LNC2",
    "LNP4", "M20", "M20J", "M20K", "M20P", "M20T", "M600", "M700", "MCR1",
    "MCR4", "NG5", "P28A", "P28B", "P28R", "P28S", "P28T", "P28U", "P32R",
    "P32T", "P46T", "P51", "P96G", "PA24", "PA25", "PA28", "PA32", "PA38",
    "PA46", "PC12", "PC21", "PC7", "PC9", "PNR2", "PNR3", "PNR4", "PP3",
    "PUP", "R90R", "RALL", "RF6", "RV10", "RV12", "RV14", "RV4", "RV6",
    "RV7", "RV8", "RV9", "S22T", "SAH1", "SHRK", "SIRA", "SLG2", "SLG4",
    "SLG5", "SONX", "SPIT", "SR20", "SR22", "SS2T", "T34P", "T6", "TA15",
    "TAMP", "TB20", "TB21", "TB30", "TBM7", "TBM8", "TBM9", "TL20", "TL200",
    "TL22", "TOBA", "TUCA", "TWEN", "VL3", "WA40", "WT9", "WT91", "Y18T",
    "YK52", "Z37P", "Z42",
    # round 3 (2026-09-26), from the unsure list, types checked against ICAO:
    "AS02", "B209", "BREZ", "BT36", "BX2", "CORO", "CRES", "D250", "DAL4", "DV1",
    "EUPA", "KL07", "LXR", "PRIM", "SAKO", "SD4", "SUBA", "TIPB", "TOUR", "V252",
)
_BY_TYPE.update({c: "lowwing" for c in _LOWWING})
# ! BACKLOG 260 ROUND 3 (Lee, 2026-09-26). Every type from ICAO Doc 8643
# (doc8643.icao.int), which records the type but NOT the wing position; high,
# low or mid wing is from knowledge of each design. Placement document: Claude
# Docs "Live Track Aircraft - backlog 260 icon placements".
_ROUND3 = {
    # twins on his cessna (a single): the twin. C425 Conquest I, C421 Golden
    # Eagle, C340, VTOR the AP68TP Viator (as P68 above).
    "C421": "beechcraft", "C425": "beechcraft", "C340": "beechcraft", "VTOR": "beechcraft",
    # PAY4 is the PA-42 Cheyenne 400: low wing, TIP TANKS, T-TAIL (SKYbrary).
    # Lee: "Draw it correctly" - its own icon.
    "PAY4": "cheyenne",
    # PC24 (the Pilatus PC-24 jet) has its own icon: _PILATUS below.
    # canard pushers; Lee identified all four with three-views
    "VEZE": "canard", "LGEZ": "canard", "COZY": "canard", "VELO": "canard",
    # biplanes (SA75, the Acroduster Too, was on the unsure list)
    "SA75": "biplane", "ST75": "biplane", "BU31": "biplane", "EAGL": "biplane", "FK12": "biplane",
    "SA30": "biplane", "WACF": "biplane", "CB1": "biplane", "PTS2": "biplane",
    # ! FIGHTERS. F15 and F5 are ALSO the names of two of his FLARM icons, and
    # his list files them there: an F-15 drew his three-bladed propeller.
    "T38": "fighter", "F16": "fighter", "F18H": "fighter", "F18S": "fighter", "F22": "fighter", "F35": "fighter",
    "F15": "fighter", "F14": "fighter", "F5": "fighter", "F4": "fighter", "F86": "fighter", "EUFI": "fighter",
    "RFAL": "fighter", "MIR2": "fighter", "MIRA": "fighter", "MRF1": "fighter", "SU27": "fighter", "SU57": "fighter",
    "MG21": "fighter", "MG23": "fighter", "MG25": "fighter", "MG29": "fighter", "MG31": "fighter", "J10": "fighter",
    "J20": "fighter", "FC1": "fighter", "KF21": "fighter", "KFIR": "fighter", "LCA": "fighter", "SGCD": "fighter",
    "SGEF": "fighter", "SB37": "fighter", "TOR": "fighter", "HAR": "fighter",
    # rotorcraft of their own shape: the four keys are in ROTOR_KEYS below, so
    # rule 1 of airframe.is_rotorcraft() keeps them on the low layer.
    "H47": "tandem", "H46": "tandem",
    "KA26": "coaxial", "KA27": "coaxial", "KA32": "coaxial", "KA50": "coaxial", "KA52": "coaxial",
    "KMAX": "kmax",
    # every ICAO class-G designator (gyroplane) plus the generic GYRO. MM24
    # supersedes its a7 pin in _OVERRIDES.
    "A002": "gyro", "A205": "gyro", "AC10": "gyro", "ADEL": "gyro", "AG1": "gyro", "AR1": "gyro",
    "BR54": "gyro", "CDUS": "gyro", "CHSY": "gyro", "CLD2": "gyro", "CLON": "gyro", "CMD1": "gyro",
    "CMDE": "gyro", "CMDT": "gyro", "DEAG": "gyro", "EL10": "gyro", "ELA7": "gyro", "GOBU": "gyro",
    "GYRO": "gyro", "HA2": "gyro", "HAW3": "gyro", "HW4P": "gyro", "HW4T": "gyro", "J4B2": "gyro",
    "JE2": "gyro", "JRO": "gyro", "MM14": "gyro", "MM16": "gyro", "MM19": "gyro", "MM21": "gyro",
    "MM22": "gyro", "MM24": "gyro", "MMAX": "gyro", "MT": "gyro", "OKHO": "gyro", "PAV4": "gyro",
    "PCA2": "gyro", "PHIX": "gyro", "RAF2": "gyro", "REV6": "gyro", "RPUP": "gyro", "SCII": "gyro",
    "SNAD": "gyro", "SPGY": "gyro", "SPHA": "gyro", "SSC": "gyro", "TSTR": "gyro", "UFHT": "gyro",
    "UM18": "gyro", "XNON": "gyro",
    # the B-2: his list puts B2 on b2, his AIRSHIP ellipse
    "B2": "flyingwing",
    # the Avanti: his list puts it on dh8a
    "P180": "p180",
}
_BY_TYPE.update(_OVERRIDES)
_BY_TYPE.update(_ROUND3)

# ! PILATUS, each its own icon (Lee, 2026-09-26): outlines traced from the top
# views in Pilatus Aircraft Ltd's model building plans (1:25), scaled to the
# published span; drawn length within 2% of the published length. Codes from
# ICAO Doc 8643. PC-7 is drawn from the MkX plan; PC24 was on learjet.
_PILATUS = {
    "PC6P": "pc6", "PC6T": "pc6", "PC7": "pc7", "PC9": "pc9",
    "PC12": "pc12", "PC21": "pc21", "PC24": "pc24",
}
_BY_TYPE.update(_PILATUS)

# ! ROUND 4 (Lee, 2026-10-06). Codes from ICAO Doc 8643, where the class code
# starting "A" means amphibian. A new icon only where the type is NOT
# substantially like his amphibian (a CL-415 planform, span/length 1.455):
# be12, be200 and us2 are drawn to published dimensions (bin\new_icons\gen_amph.py).
_ROUND4 = {
    # the ICAO designators for the families his model names mean: CAT is the
    # Canso/Catalina (his CANSO, PBY); DH2T and DH3T the Turbo Beaver and Otter
    "CAT": "amphibian", "DH2T": "amphibian", "DH3T": "amphibian",
    # straight-wing twins with floats and a conventional tail, like his icon:
    # the Goose (G21 1.27; G21T, G21M conversions), the Mallard (G73T 1.38; G73)
    # and the HU-16 Albatross. G21 and G73T were on his dh8a.
    "G21": "amphibian", "G21T": "amphibian", "G21M": "amphibian",
    "G73": "amphibian", "G73T": "amphibian", "U16": "amphibian",
    # the Beriev A-50 is an Il-76 airframe; his list puts it on a340
    "A50": "heavytransport",
    # their own icons: Be-12 (as long as its span, H-tail), Be-200 (swept jet,
    # pods above the wing roots, T-tail; BER2 was on his dh8a) and the A-40,
    # the same layout larger; US-2 (four turboprops, T-tail)
    "BE12": "be12", "BER2": "be200", "BER4": "be200", "US2": "us2",
}
_BY_TYPE.update(_ROUND4)

# The helicopter silhouette. airframe.is_rotorcraft() treats "the table draws
# one of ROTOR_KEYS" as the answer for any type the table knows.
ROTOR_KEY = "a7"
ROTOR_KEYS = frozenset((ROTOR_KEY, "tiltrotor", "tiltrotorfwd",
                        "tandem", "coaxial", "kmax", "gyro"))

# ! A TILTROTOR HAS TWO PICTURES (Lee, 2026-09-25): nacelles up (hovering) and
# nacelles forward (cruise). ADS-B carries no nacelle angle, so GROUND SPEED
# decides, with a gap so it cannot flicker between them. The speeds are
# Lee's (200 / 170 km/h), from typical conversion speeds - an inference, not a
# measurement. Unknown speed keeps the last picture; first sight hovers.
TILT_FORWARD_KMH = 200.0
TILT_HOVER_KMH = 170.0


def tiltrotor_forward(was_forward: bool | None, speed_kmh: float | None) -> bool:
    """True for the cruise picture."""
    if speed_kmh is None:
        return bool(was_forward)
    if speed_kmh >= TILT_FORWARD_KMH:
        return True
    if speed_kmh <= TILT_HOVER_KMH:
        return False
    return bool(was_forward)

# Where a7's rotor turns, in its 200-unit viewBox: the CABIN's area centroid
# (bin\lta_rotor_cabin.py), with both blades moved there and the
# animateTransform centred on it; tests/test_icons.py checks the two agree.
# ! MOVED 2026-09-25 (Lee: "the rotors are spinning under the helicopter body
# and get shorter when they rotate to the front"). The first port turned the
# rotor where Jos's static X happened to cross, (100.798, 84.112), the WAIST of
# the fuselage: frozen at 8 angles in the card's own marker CSS
# (bin\lta_rotor_frames.png) the front blade showed only as a stub above the
# nose and the rear one vanished into the tail boom. Blade length never changed.
ROTOR_HUB = (100.798, 57.790)
# Blade reach from the hub, measured from the two blade paths (116.2-116.3).
ROTOR_RADIUS = 116.3
# ! A spinning rotor sweeps a full circle, so a big helicopter would push the
# front tip out of the SVG box and out of the card's round marker. Weight is
# capped for a picture with a rotor; the test proves every tip stays inside.
ROTOR_MAX_WEIGHT = 0.85
# The rotor's own dark edge, in marker pixels, so it reads ON TOP of a body
# the same colour. Chosen by eye 2026-09-25 from 0, 0.5, 0.75 and 1.0 at 48 and
# 96 px (bin\lta_rotor_edges.png): at 0.75 and up the edge chops the body into
# pieces at marker size; at 0 the rotor merges into it again.
ROTOR_EDGE_PX = 0.5


def type_icon(type_code: str | None) -> str | None:
    """The silhouette the table gives a type code, or None if it does not know it."""
    return _BY_TYPE.get((type_code or "").strip().upper())

# --- what must NOT be turned ---------------------------------------------
# ! An icon is unrotatable when turning it says nothing or breaks the
# picture. Decided by LOOKING at the rendered icons.
#
#   b3   a parachute canopy from above - radially symmetric, and a
#        parachutist has no heading
#   f11  a hot-air balloon in SIDE view - the only side-on icon in the set,
#        and rotating it tips the balloon over
#   f15  a three-bladed propeller - rotationally symmetric
#
# ! B2 IS NOT IN THIS SET. Lighter-than-air covers AIRSHIPS, which are
# powered and point where they are going, and b2 is a top-down ellipse that
# rotates correctly. An earlier draft put B2 here and swapped it to the f11
# balloon; both were wrong.
_NOT_ROTATABLE = frozenset(("b3", "f11", "f15", "c0"))

# Rotation is quantised so the browser can cache. 5 degrees is finer than
# the eye reads on a 28px marker and gives 72 cacheable variants per icon.
ROTATION_STEP = 5

_SAFE = re.compile(r"^[a-z0-9]+$")


# ! A FALLBACK ON THE AIRCRAFT NAME, behind the designator table and ahead of
# the category icon. The name comes from aircraft.py keyed on the type code, so
# this catches designators aircraft.py knows and _BY_TYPE does not - measured
# 2026-09-21, a C510 "Cessna Citation Mustang" and a C550 "Cessna Citation II"
# were both on the generic a1 icon. Idea taken from another project that uses
# these icons in production; no code came with it.
# ! ONLY FAMILIES WHOSE SILHOUETTE FITS UNAMBIGUOUSLY. No bare "cessna" - that
# name covers the 421 twin as well as the 172. Order matters: first match wins.
_BY_NAME: tuple[tuple["re.Pattern[str]", str], ...] = tuple(
    (re.compile(p, re.I), k) for p, k in (
        (r"\bcitation\b|\bphenom\b|\blearjet\b|\bhawker\b|\bhonda ?jet\b"
         r"|\bbeechjet\b|\bpremier\b", "learjet"),
        (r"\bgulfstream\b|\bchallenger\b|\bglobal (?:\d|express)", "glf5"),
        (r"\bfalcon\b|\blegacy\b|\bpraetor\b", "fa7x"),
        (r"\bembraer e1[79]\d\b|\bembraer e2\b", "e195"),
        (r"\bdash ?8\b|\bq400\b|\batr ?[47]2\b|\bsaab 340\b|\bshorts? 3[36]0\b", "dh8a"),
        (r"\bcirrus\b|\bskyhawk\b|\bskylane\b|\bcherokee\b|\barcher\b"
         r"|\bbonanza\b|\bmooney\b", "cessna"),
    ))


def pick(emitter_category: str | None, type_code: str | None,
         icon_category: str | None = None, name: str | None = None) -> str:
    """Which silhouette to draw. Type, then rotorcraft, then name, then category.

    ! THE HELICOPTER IS DECIDED BY airframe.is_rotorcraft(), the one function
    the tone, icon_category and map_layer also call, so the picture cannot
    disagree with them. Imported here, not at the top: airframe imports this
    module for the type table.
    """
    from .airframe import is_rotorcraft

    t = (type_code or "").strip().upper()
    if t in _BY_TYPE:
        return _BY_TYPE[t]
    if is_rotorcraft(emitter_category, t):
        return ROTOR_KEY
    if name:
        for rx, key in _BY_NAME:
            if rx.search(name):
                return key
    cat = (emitter_category or "").strip().upper()
    if cat in _BY_CATEGORY and _BY_CATEGORY[cat] != ROTOR_KEY:
        return _BY_CATEGORY[cat]
    if icon_category == "balloon":
        return "b2"
    if icon_category == "uav":
        return "b0"
    return "a0"


def is_rotatable(key: str) -> bool:
    return key not in _NOT_ROTATABLE


def quantise(bearing: float | None) -> int | None:
    if bearing is None:
        return None
    return int(round((bearing % 360) / ROTATION_STEP) * ROTATION_STEP) % 360


def valid_key(key: str) -> bool:
    """Guard for the HTTP view: a path segment must not escape the folder."""
    return bool(_SAFE.match(key or ""))


# --------------------------------------------------------------------------
# Rendering
#
# ! A SQUARE ROTATED 45 DEGREES DOES NOT FIT IN ITS OWN BOX. The corners need
# sqrt(2) more room, so wrapping the artwork in a plain rotate() clips the
# wingtips at every angle that is not a multiple of 90. The group is scaled
# to 1/sqrt(2) about the centre first, which costs a little size and keeps
# every angle whole.
# --------------------------------------------------------------------------

import math
import os

_SVG_OPEN = re.compile(r"<svg\b[^>]*>", re.I)
_VIEWBOX = re.compile(r'viewBox\s*=\s*"([^"]+)"', re.I)

# Fitting a rotated square inside the original box.
_FIT = 1.0 / math.sqrt(2.0)

# --------------------------------------------------------------------------
# ! 18 OF THE 37 SOURCE FILES ARE NOT WELL-FORMED XML. Measured 2026-09-12:
# every file exported from Boxy SVG carries a <bx:grid> element whose `bx:`
# prefix is never declared, which is a fatal well-formedness error. A browser
# parses an SVG fetched through background-image STRICTLY, so those icons
# rendered NOTHING AT ALL - a1, a3, a7 and the rest of the emitter-category
# fallbacks, which is to say every aircraft we could not match to a type.
#
# ! WHY THIS SURVIVED A VISUAL CHECK. The note at the top of this file says
# every icon was verified by rendering all 37 and looking. That was true and
# it proved orientation; whatever rendered them tolerated the undeclared
# prefix and a browser does not. A fixture that does not exercise the failing
# behaviour proves nothing about it - 05_SHARED_LESSONS D6a-0.
#
# ! STRIPPED, NOT DECLARED. Adding xmlns:bx would make the file parse while
# keeping an editor's grid guide in shipped artwork. The element draws
# nothing; it should not be there.
_FOREIGN_EMPTY = re.compile(r"<([a-zA-Z_][\w.-]*):([\w.-]+)\b[^>]*/\s*>")
_FOREIGN_PAIR = re.compile(
    r"<([a-zA-Z_][\w.-]*):([\w.-]+)\b[^>]*>.*?</\1:\2\s*>", re.S)
_FOREIGN_ATTR = re.compile(
    r'\s([a-zA-Z_][\w.-]*):([\w.-]+)\s*=\s*"[^"]*"')
_KNOWN_PREFIXES = frozenset(("xlink", "svg", "xml"))

# ! BLACK ON NEAR-BLACK. ha-map-card draws every picture marker on a disc
# filled with var(--card-background-color), and .marker.dark hardcodes
# #1c1c1c with no variable at all. The source artwork is #000000. Measured
# contrast: about 1.15:1 - the silhouettes were rendering and could not be
# seen.
#
# ! SO WE PAINT BOTH. The badge is ours and opaque, which is what makes this
# work from ONE URL: an integration cannot know whether the browser asking
# for a picture is in light or dark mode, so a fill tuned for one theme is
# wrong in the other. Owning the background removes the question.
BADGE = "#1F2327"

# ! COLOUR CARRIES MEANING, NOT DECORATION. Identity is the label's job -
# never encode it here, because a colour nobody can look up is noise.
_TONES = {
    "default": "#EDEAE2",
    "gov": "#EF9F27",      # government, law enforcement, air ambulance
    "rotor": "#9FE1CB",    # rotorcraft, which are not an edge case here
    # 2026-10-22, chosen by render on OSM Standard streets, water, a forest
    # preserve and the Loop at the live 60 px (20_Research/le_tones_20261022/
    # tones_60px_part*.png): olive drab #4B5320 went near black under the
    # shading; #859556 read khaki. Navy #1E3A8A read black; #5B8BDC sat on
    # OSM's own water and label blue.
    "mil": "#5C6B32",      # army green: the military, and the Coast Guard
    "law": "#2F5DB8",      # federal law enforcement
    # ! STATE AND LOCAL POLICE: WHITE, with flashing blue lights (Lee
    # 2026-10-22 02:48), as Live Track Ships draws a police boat.
    "police": "#FFFFFF",
}

# Owner categories (registrations.categorise) that decide the fill and the
# lights. tests/test_law.py holds these level with registrations.py.
MILITARY_CATEGORIES = frozenset(("Military", "Coast Guard"))
LAW_CATEGORIES = frozenset(("Law enforcement", "Federal law enforcement"))
POLICE_CATEGORIES = frozenset(("Law enforcement",))   # state and local
LIGHT_CATEGORIES = LAW_CATEGORIES | {"Coast Guard"}

# ! SIZE FROM emitter_category, NOT FROM THE ARTWORK. The ADS-B Radar site
# says their icons vary in size to separate light aircraft from heavy ones.
# That is their app's rendering, not something baked into the files:
# measured 2026-09-12, the 37 SVGs declare only three distinct viewBoxes and
# b737 and b747 are identical. So the ramp is ours, off the category the
# feed already gives us. A1 light -> A5 heavy.
_WEIGHT = {
    "A1": 0.62, "A2": 0.72, "A3": 0.85, "A4": 0.88, "A5": 1.00,
    "A6": 0.80, "A7": 0.78, "B1": 0.66, "B2": 0.78, "B4": 0.62,
}
DEFAULT_WEIGHT = 0.80


def tone_for(is_government: bool, icon_category: str | None,
             operator_category: str | None = None, military: bool = False) -> str:
    """Which colour the silhouette is drawn in.

    ! FILL = OWNER CLASS (Lee 2026-10-21): cream civil, orange government,
    army green military, blue law enforcement that is not military. The
    lights say law enforcement on top of whatever the fill is.
    """
    if military or operator_category in MILITARY_CATEGORIES:
        return "mil"
    if operator_category in POLICE_CATEGORIES:
        return "police"
    if operator_category in LAW_CATEGORIES:
        return "law"
    if is_government:
        return "gov"
    if icon_category == "rotorcraft":
        return "rotor"
    return "default"


def lights_for(operator_category: str | None) -> bool:
    """Whether the silhouette carries flashing police lights."""
    return operator_category in LIGHT_CATEGORIES


# ! LIGHTS MODES (backlog 299, Lee 2026-10-24 17:00). True is the tone-paired
# meaning it always had: the blue wig-wag on the police white, the red/blue
# glow on every other tone. "glow" and "wigwag" say which, whatever the tone:
# a state or local police aircraft on the EnforcementAircraft list is white
# with the red/blue glow (decision I(a), drawn and looked at in
# 20_Research/le_flash_299_20261024). The picture URL carries the mode
# explicitly (l=g, l=w); a view still reads the old l=1 as True.
LIGHTS_GLOW = "glow"
LIGHTS_WIGWAG = "wigwag"
# ! "charter" (0.2.16, backlog 313): the STEADY ICE Air charter glow, l=c. Never
# from True: only a consumer that knows the list's charter status asks for it.
LIGHTS_CHARTER = "charter"
_LIGHTS_QUERY = {LIGHTS_GLOW: "g", LIGHTS_WIGWAG: "w", LIGHTS_CHARTER: "c"}
_LIGHTS_FROM_QUERY = {"1": True, "g": LIGHTS_GLOW, "w": LIGHTS_WIGWAG, "c": LIGHTS_CHARTER}


def lights_mode(tone: str, lights) -> str | None:
    """None, LIGHTS_GLOW, LIGHTS_WIGWAG or LIGHTS_CHARTER: what a lights value draws on this tone.

    Anything that is not False/None, True or a mode draws nothing, so a
    mangled value fails safe.
    """
    if lights is True:
        return LIGHTS_WIGWAG if tone == "police" else LIGHTS_GLOW
    if isinstance(lights, str) and lights in _LIGHTS_QUERY:
        return lights
    return None


def lights_from_query(value: str | None):
    """A view's l= value as render()'s lights: "1" True, "g" glow, "w" wig-wag, "c" charter, else off."""
    return _LIGHTS_FROM_QUERY.get(value or "", False)


def weight_for(emitter_category: str | None) -> float:
    return _WEIGHT.get((emitter_category or "").strip().upper(), DEFAULT_WEIGHT)


def _inner(svg: str) -> tuple[str, float, float]:
    """The artwork inside the root <svg>, plus the box it is drawn in."""
    m = _SVG_OPEN.search(svg)
    if not m:
        raise ValueError("no <svg> element")
    body = svg[m.end():]
    end = body.rfind("</svg>")
    if end >= 0:
        body = body[:end]
    vb = _VIEWBOX.search(m.group(0))
    if vb:
        parts = [float(x) for x in vb.group(1).replace(",", " ").split()]
        if len(parts) == 4:
            return body, parts[2], parts[3]
    return body, 512.0, 512.0


def strip_foreign(body: str) -> str:
    """Remove elements AND attributes in an undeclared namespace.

    ! TWO SEPARATE CAUSES, and fixing only the first leaves 48 renders
    broken. <bx:grid/> is a foreign ELEMENT; bx:shape="ring 565 355 ..." on
    an ordinary <path> is a foreign ATTRIBUTE. Both are fatal, because the
    xmlns:bx declaration lives on the root <svg> tag that _inner() discards
    by design - we build our own root.
    """
    def drop(m: "re.Match[str]") -> str:
        return "" if m.group(1) not in _KNOWN_PREFIXES else m.group(0)
    body = _FOREIGN_PAIR.sub(drop, body)
    body = _FOREIGN_EMPTY.sub(drop, body)
    return _FOREIGN_ATTR.sub(
        lambda m: "" if m.group(1) not in _KNOWN_PREFIXES else m.group(0),
        body)


# ! fill="none" and stroke="none" are KEPT. They are deliberate - an element
# drawn with a stroke only, or a hit area that must not paint. Stripping them
# would fill shapes the artist left hollow.
_PAINT_ATTR = re.compile(r'\s(?:fill|stroke)\s*=\s*"(?!none\s*")[^"]*"', re.I)
_PAINT_STYLE = re.compile(r"(?:fill|stroke)\s*:\s*[^;\"']*;?", re.I)


def decolour(body: str) -> str:
    """Strip hardcoded paint so the wrapper's fill can be inherited.

    ! THE ARTWORK CARRIES fill="#000000" EXPLICITLY in several files, and an
    attribute on a child beats inheritance from a parent group. Setting fill
    on the wrapper alone changes nothing - which is why recolouring looked
    impossible before this.
    """
    body = _PAINT_ATTR.sub(" ", body)
    return _PAINT_STYLE.sub("", body)


# ! SILHOUETTE-ONLY NEEDS AN OUTLINE, OR IT VANISHES ON LIGHT TILES. The
# badge existed so one URL reads in light and dark mode alike. Without it the
# pale fill sits straight on the map, so a dark edge is drawn round it - the
# same answer as the card's label outline. This is the VISIBLE edge, in
# pixels of the rendered icon at `size`. 2.0 chosen by eye 2026-09-21 from
# 2, 3 and 4 on light, water and dark: at 3 and above a light aircraft turns
# to a blob, and a fixed edge inflates small icons most, fighting the ramp.
# ! 1.0 SINCE 2026-10-21 (Lee: "reduce the thickness so they look more
# delicate"), with the card's ground shadow under every aircraft. Chosen by
# eye from 2.0 / 1.25 / 1.0 / 0.75 at the live 60 px marker on OSM Standard
# streets, lakefront and forest preserve (20_Research/icons_depth_20261021/
# depth_sheet3_*): at 0.75 the small white ones dissolve on light tiles.
OUTLINE_PX = 1.0

# ! GENTLE SHADING (Lee 2026-10-21). Two gradients laid over the silhouette
# and masked to it: spanwise, darker at the tips with a soft highlight down
# the spine; fore and aft, a touch lighter at the nose. Both are in the
# icon's OWN frame (nose up), so they turn with the aircraft and the card
# needs no angle. SHADE scales every stop: 1.0 greyed the white airliners on
# light tiles, 0.6 kept them white (depth_sheet2_48px.png, depth_sheet3_*).
# A bevel (specular lighting) was tried and dropped: invisible at 48 px.
SHADE = 0.6
_SPINE = ((0, "#000", .42), (.32, "#000", .10), (.47, "#fff", .28), (.50, "#fff", .40),
          (.53, "#fff", .28), (.68, "#000", .10), (1, "#000", .42))
_FORE_AFT = ((0, "#fff", .10), (1, "#000", .18))


def _gradient(gid: str, x1: float, y1: float, x2: float, y2: float, stops) -> str:
    return ('<linearGradient id="%s" gradientUnits="userSpaceOnUse" '
            'x1="%.3f" y1="%.3f" x2="%.3f" y2="%.3f">' % (gid, x1, y1, x2, y2)
            + "".join('<stop offset="%g" stop-color="%s" stop-opacity="%.3f"/>'
                      % (o, c, a * SHADE) for o, c, a in stops)
            + "</linearGradient>")

# ! THE EDGE IS A FILTER, NOT A STROKE. Two stroke designs failed on the
# artwork, measured 2026-09-21:
#   * 15 of the 37 files carry stroke="none" on their only path (a330, a340,
#     a380, a6, b737, b747, b767, b777, b787, crjx, e195, erj, f100, learjet,
#     md11). A child attribute beats the stroke a parent group sets, so those
#     icons drew NO edge and vanished on light tiles.
#   * The 512-viewBox airliners carry their OWN transform on the path, a
#     second scale-down inside ours, so any stroke width came out thinner on
#     exactly those files. vector-effect="non-scaling-stroke" did not rescue
#     it: resvg measured it in viewBox units and browsers are free to read
#     "host coordinate space" differently. An edge whose width depends on the
#     renderer is the wrong design.
# Dilating the silhouette's own ALPHA works on what was actually drawn,
# after every transform in every file, so neither problem can reach it.
_OUTLINE_FILTER = (
    '<defs><filter id="e" filterUnits="userSpaceOnUse" x="0" y="0" '
    'width="%s" height="%s" color-interpolation-filters="sRGB">'
    '<feMorphology in="SourceAlpha" operator="dilate" radius="%.4f" result="d"/>'
    '<feFlood flood-color="%s" result="c"/>'
    '<feComposite in="c" in2="d" operator="in" result="edge"/>'
    '<feMerge><feMergeNode in="edge"/><feMergeNode in="SourceGraphic"/></feMerge>'
    '</filter></defs>')


_ROTOR_FILTER = (
    '<defs><filter id="r" x="-20%%" y="-20%%" width="140%%" height="140%%" '
    'color-interpolation-filters="sRGB">'
    '<feMorphology in="SourceAlpha" operator="dilate" radius="%.4f" result="d"/>'
    '<feFlood flood-color="%s" result="c"/>'
    '<feComposite in="c" in2="d" operator="in" result="edge"/>'
    '<feMerge><feMergeNode in="edge"/><feMergeNode in="SourceGraphic"/></feMerge>'
    '</filter></defs>')


# ! POLICE LIGHTS (Lee 2026-10-21; look chosen 2026-10-22 by render, 20_Research/
# le_tones_20261022/lights*_60px_part*.png). A red glow round the silhouette,
# then a blue one, alternating. Three looks were drawn on OSM tiles at 60 px:
# a thin halo (1.6 px) vanished into the ground shadow, and blue on the blue
# law fill; a split wig-wag halo was no better; a light bar of two dots on
# the fuselage was readable but small. A strong glow (3 px grown, 2.2 px
# blur, drawn twice) read as police lights on every background. 0.6 s each
# colour: under one change a second per colour, well below the 3 flashes a
# second that photosensitivity guidance warns about.
LIGHT_RED = "#FF2020"
LIGHT_BLUE = "#2060FF"
LIGHT_PERIOD_S = 1.2
_LIGHT_GROW_PX = 3.0
_LIGHT_BLUR_PX = 2.2


def _halo(fid: str, colour: str, w: float, h: float, px: float) -> str:
    return ('<filter id="%s" filterUnits="userSpaceOnUse" x="0" y="0" width="%s" height="%s">'
            '<feMorphology in="SourceAlpha" operator="dilate" radius="%.4f"/>'
            '<feGaussianBlur stdDeviation="%.4f" result="g"/>'
            '<feFlood flood-color="%s"/><feComposite in2="g" operator="in"/></filter>'
            % (fid, _fmt(w), _fmt(h), _LIGHT_GROW_PX * px, _LIGHT_BLUR_PX * px, colour))


def _flash(fid: str, values: str) -> str:
    return ('<g opacity="%s"><use href="#art" filter="url(#%s)"/><use href="#art" filter="url(#%s)"/>'
            '<animate attributeName="opacity" values="%s" dur="%gs" calcMode="discrete" '
            'repeatCount="indefinite"/></g>' % (values[0], fid, fid, values, LIGHT_PERIOD_S))


# ! THE ICE AIR CHARTER GLOW (backlog 313; Lee 2026-10-29 00:37 chose look C4 of
# 30_Integrations/Live_Track_SALT/20_Research/charter_look_20261029, drawn at
# 60 px on OSM streets, water and the Loop). STEADY, never flashing: the
# airframe flies ICE Air charters and other work too, so it does not take the
# sourced flash. Red grown wide outside, blue close inside, both UNDER the
# outlined art as the glow is. (id, colour, grow px, blur px, opacity)
CHARTER_HALOS = (("cr", LIGHT_RED, 3.0, 2.2, 0.85), ("cb", LIGHT_BLUE, 1.4, 1.0, 0.9))


def _steady_halos(w: float, h: float, px: float) -> tuple[str, str]:
    """(defs, drawn) for the charter glow: no animation."""
    defs, drawn = "", ""
    for fid, colour, grow, blur, op in CHARTER_HALOS:
        defs += ('<filter id="%s" filterUnits="userSpaceOnUse" x="0" y="0" width="%s" height="%s">'
                 '<feMorphology in="SourceAlpha" operator="dilate" radius="%.4f"/>'
                 '<feGaussianBlur stdDeviation="%.4f" result="g"/>'
                 '<feFlood flood-color="%s" flood-opacity="%.2f"/><feComposite in2="g" operator="in"/></filter>'
                 % (fid, _fmt(w), _fmt(h), grow * px, blur * px, colour, op))
        drawn += '<g opacity="1"><use href="#art" filter="url(#%s)"/></g>' % fid
    return "<defs>" + defs + "</defs>", drawn


# ! PAINTED ICONS (2026-10-22). An icon whose root <svg> carries
# data-paint="keep" is drawn in its OWN colours: the VC-25A in its livery
# (Lee 2026-10-21, draft A). decolour() would strip it to one tone. The tone
# still reaches the <use>, but every painted path's own fill wins.
_PAINT_KEEP = re.compile(r'<svg\b[^>]*\bdata-paint="keep"', re.I)

# ! A picture of ONE airframe by Mode S hex (the presidential aircraft) is the
# CONSUMER'S: Live Track Aircraft keeps HEX_KEYS and its two files (backlog 298).


# ! THE POLICE FLASH, PORTED FROM LIVE TRACK SHIPS (icons.py _police_flash,
# Lee 2026-09-28 for police boats; for police aircraft 2026-10-22). Drawn in
# Ships' 200-unit space, scaled by the art's k and fitted to this icon's box:
# two lamps either side of the centre in wig-wag, each a blue burst with a
# hot centre reaching past the silhouette on its side, radius capped so the
# burst never reaches past 94 of 100 from the centre at any heading. OUTSIDE
# the outline filter, so the glow stays soft.
POLICE_FLASH_R = 108.0
REACH_LIMIT = 94.0
# ! FORWARD (Lee 2026-10-23 16:59: at Ships' 101, the image centre, the
# lamps sat at the back of a helicopter's body and behind an aircraft's
# wings). 40 chosen by render at 90 px on OSM tiles against 80, 65 and 50
# (20_Research/le_tones_20261022/police_fwd_t*.png): forward cabin of the
# helicopter, cockpit and ahead of the wing on fixed-wing. The reach cap
# shrinks the burst a little this far out.
_LY = 40
POLICE_BLUE, POLICE_HOT = "#3D8BFF", "#EEF6FF"
_FLASH_CACHE: dict[float, float] = {}


def _flash_reach(r: float) -> float:
    cx = 85 - 0.25 * r
    return max(math.hypot(cx + r * math.cos(a) - 100, _LY + 0.72 * r * math.sin(a) - 100)
               for a in (i * math.pi / 360 for i in range(720)))


def police_flash_r(k: float) -> float:
    k = round(max(0.1, min(1.0, k)), 2)
    if k not in _FLASH_CACHE:
        if k * _flash_reach(POLICE_FLASH_R) <= REACH_LIMIT:
            _FLASH_CACHE[k] = POLICE_FLASH_R
        else:
            lo, hi = 0.0, POLICE_FLASH_R
            for _ in range(40):
                mid = (lo + hi) / 2
                lo, hi = (mid, hi) if k * _flash_reach(mid) <= REACH_LIMIT else (lo, mid)
            _FLASH_CACHE[k] = math.floor(lo * 10) / 10
    return _FLASH_CACHE[k]


def _police_flash(k: float, w: float, h: float) -> str:
    r = police_flash_r(k)
    kt = 'keyTimes="0;0.06;0.38;0.48;1"'
    out = []
    for x, begin, g in ((85, "0s", "pfa"), (115, "-0.4s", "pfb")):
        cx = x + (-1 if x < 100 else 1) * r * 0.25
        out.append(
            '<defs><radialGradient id="%s"><stop offset="0" stop-color="%s"/>'
            '<stop offset="0.22" stop-color="%s" stop-opacity="0.95"/>'
            '<stop offset="0.6" stop-color="%s" stop-opacity="0.55"/>'
            '<stop offset="1" stop-color="%s" stop-opacity="0"/></radialGradient></defs>'
            '<ellipse cx="%.1f" cy="%d" rx="%.1f" ry="%.1f" fill="url(#%s)" opacity="0">'
            '<animate attributeName="opacity" values="0;1;1;0;0" %s dur="0.8s" '
            'begin="%s" repeatCount="indefinite"/></ellipse>'
            % (g, POLICE_HOT, POLICE_BLUE, POLICE_BLUE, POLICE_BLUE,
               cx, _LY, r, 0.72 * r, g, kt, begin))
    s = min(w, h) / 200.0 * k
    return ('<g transform="translate(%.4f %.4f) scale(%.6f)">%s</g>'
            % (w / 2 - 100 * s, h / 2 - 100 * s, s, "".join(out)))


def render(path: str, degrees: int | None, size: int = 64,
           tone: str = "default", weight: float = DEFAULT_WEIGHT,
           badge: bool = True, lights=False) -> str:
    """One icon: coloured, sized and rotated, on our badge or outlined.

    lights: False, True (paired with the tone), LIGHTS_GLOW, LIGHTS_WIGWAG or LIGHTS_CHARTER.
    """
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        src = fh.read()
    painted = _PAINT_KEEP.search(src) is not None
    body, w, h = _inner(src)
    body = strip_foreign(body)
    if not painted:
        body = decolour(body)
    cx, cy = w / 2.0, h / 2.0
    rotor = re.search(r'<g id="rotor\d*"', body) is not None
    if rotor:
        weight = min(weight, ROTOR_MAX_WEIGHT)

    # ! The rotation fit and the weight ramp COMPOSE. 1/sqrt(2) is what keeps
    # a square inside its own box at 45 degrees; the weight is how big this
    # class of aircraft should read. Applying both keeps every angle whole at
    # every size.
    k = _FIT * max(0.1, min(1.0, weight))

    parts = ["translate(%.4f %.4f)" % (cx - cx * k, cy - cy * k),
             "scale(%.6f)" % k]
    if degrees:
        parts.append("rotate(%d %.4f %.4f)" % (degrees, cx, cy))

    fill = _TONES.get(tone, _TONES["default"])
    transform = " ".join(parts)
    head = ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
            'viewBox="0 0 %s %s">' % (size, size, _fmt(w), _fmt(h)))
    art = '<g transform="%s" fill="%s">%s</g>' % (transform, fill, body)

    if badge:
        # ! THE BADGE IS DRAWN BY US, not by the card. It is opaque on
        # purpose - see the note beside BADGE. It also means the 50% crop
        # ha-map-card applies lands on our own circle instead of shaving a
        # wingtip.
        return (head + '<circle cx="%.4f" cy="%.4f" r="%.4f" fill="%s"/>'
                % (cx, cy, min(cx, cy), BADGE) + art + '</svg>')

    # ! The filter sits on an UNTRANSFORMED wrapper, so its radius is in root
    # viewBox units: the visible edge converted by the one scale that maps
    # the viewBox onto `size` pixels (by the longer side, as SVG fits it).
    radius = OUTLINE_PX * max(w, h) / float(size)
    defs = _OUTLINE_FILTER % (_fmt(w), _fmt(h), radius, BADGE)
    if rotor:
        # ! The rotor group sits INSIDE the scaled art, so its edge radius is
        # divided by that scale. Default (bounding-box) filter region: the
        # group's own box turns with it, and a root-sized region would clip
        # a blade that swings above y=0.
        rr = ROTOR_EDGE_PX * max(w, h) / float(size) / k
        defs += _ROTOR_FILTER % (rr, BADGE)
        # EVERY rotor group (a tiltrotor has two: rotor, rotor2).
        art = re.sub(r'<g id="(rotor\d*)">', r'<g id="\1" filter="url(#r)">', art)
    if degrees:
        # A turned picture (the view still answers the old per-angle URLs)
        # gets no shading: its gradients are laid out nose up.
        return (head + defs + '<g filter="url(#e)">' + art + '</g></svg>')
    # ! The art is DEFINED once and drawn twice through <use>: once in the
    # tone, once white inside the mask that keeps the gradients on the
    # silhouette. Its fill comes from the <use>, which is why the defined
    # group carries none. A <use> clone runs its SMIL animations (rotor,
    # props) as the original does - looked at in the card's marker CSS.
    art_def = art.replace(' fill="%s">' % fill, ' id="art">', 1)
    x0, x1 = cx - w * k / 2.0, cx + w * k / 2.0
    y0, y1 = cy - h * k / 2.0, cy + h * k / 2.0
    W, H = _fmt(w), _fmt(h)
    defs += ('<defs>' + art_def
             + '<mask id="m" maskUnits="userSpaceOnUse" x="0" y="0" width="%s" height="%s"%s>'
               '<use href="#art" fill="#fff"/></mask>'
             # A painted icon's mask would read its colours as luminance and
             # shade the dark parts less; alpha is the silhouette.
             % (W, H, ' style="mask-type:alpha"' if painted else '')
             + _gradient("sp", x0, 0, x1, 0, _SPINE)
             + _gradient("fa", 0, y0, 0, y1, _FORE_AFT) + '</defs>')
    lit = ('<use href="#art" fill="%s"/>' % fill
           + '<rect x="0" y="0" width="%s" height="%s" fill="url(#sp)" mask="url(#m)"/>' % (W, H)
           + '<rect x="0" y="0" width="%s" height="%s" fill="url(#fa)" mask="url(#m)"/>' % (W, H))
    glow = ""
    mode = lights_mode(tone, lights)
    if mode == LIGHTS_WIGWAG:
        # State and local police: Ships' blue wig-wag, on top, unfiltered.
        return (head + defs + '<g filter="url(#e)">' + lit + '</g>'
                + _police_flash(k, w, h) + '</svg>')
    if mode == LIGHTS_GLOW:
        # UNDER the outlined art, so the black edge still separates the
        # fill from the glow. Only on this path: the live, unrotated picture.
        px = max(w, h) / float(size)
        defs += ('<defs>' + _halo("lr", LIGHT_RED, w, h, px)
                 + _halo("lb", LIGHT_BLUE, w, h, px) + '</defs>')
        glow = _flash("lr", "1;0") + _flash("lb", "0;1")
    if mode == LIGHTS_CHARTER:
        # Steady, UNDER the outlined art, as the glow. Only on this path.
        cdefs, glow = _steady_halos(w, h, float(max(w, h)) / float(size))
        defs += cdefs
    return (head + defs + glow + '<g filter="url(#e)">' + lit + '</g></svg>')


def _fmt(v: float) -> str:
    return ("%f" % v).rstrip("0").rstrip(".") if v % 1 else "%d" % int(v)


def icon_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "icons")


def icon_path(key: str) -> str | None:
    if not valid_key(key):
        return None
    p = os.path.join(icon_dir(), key + ".svg")
    return p if os.path.exists(p) else None


# --------------------------------------------------------------------------
# The entity_picture QUERY. Each consumer serves the pictures from its OWN
# view and builds the URL: "<its base>/<key>/fixed.svg?" + picture_query().
# Live Track Aircraft's base is /api/live_track_aircraft/icon (its icons.py).
# --------------------------------------------------------------------------

# ! BUMPED WHEN THE ARTWORK CHANGES. The view answers with a day-long
# immutable cache header, so a browser that fetched the static a7 keeps it
# for a day under the same URL. 2: the rotor spins; 3: the rotor turns over
# the cabin with its own edge; 4: the King Air's props spin (all 2026-09-25);
# 5: backlog 260 round 3 - ten new icons, and his cessna and beechcraft
# props cut out and spinning (2026-09-26); 6: the six Pilatus icons
# (2026-09-26); 7: round 4 - Jos's heavytransport and amphibian, our be12,
# be200 and us2 (2026-10-06); 8: the 1 px edge and shading (2026-10-21);
# 9: the police lamps moved forward (2026-10-23) - 1440d16 shipped without
# this bump and browsers kept the old picture; 10: the King Air 350's own
# icon, kingair350.svg (2026-10-24); 11: the lights mode in the URL (l=g,
# l=w) and the red/blue glow on the police white for a listed aircraft
# (backlog 299, 2026-10-24). NOT bumped for 0.2.16's charter glow: it is a
# new picture under a new query (l=c) and changes none, which the golden
# digests in tests/test_aircraft_icons.py hold.
ART_REV = 11


def picture_query(tone: str = "default", weight: float | None = None,
                  lights=False, rev: int | None = None) -> str:
    """The entity_picture's query string: one silhouette, UNROTATED, with no disc.

    ! rev is the a= value, ART_REV when not given: a consumer with pictures of
    its own passes its own revision so it can bump them without a release here.

    ! NO ANGLE AND NO BADGE ARGUMENT, on purpose (Lee, 2026-09-25). The card
    turns the marker from icon_rotation_deg; a rotated picture as well would
    double the turn. b=0 keeps the view from drawing its disc.
    ! TONE AND WEIGHT RIDE AS QUERY PARAMETERS, and the query is part of the
    cache key - a government aircraft and an airliner on the same silhouette
    are two different cached images, as they should be.
    """
    extra = []
    if tone and tone != "default":
        extra.append("t=%s" % tone)
    if weight is not None:
        extra.append("w=%.2f" % weight)
    mode = lights_mode(tone, lights)
    if mode:
        extra.append("l=%s" % _LIGHTS_QUERY[mode])
    extra.append("b=0")
    extra.append("a=%d" % (ART_REV if rev is None else rev))
    return "&".join(extra)
