"""Pixel art for Pixel Cat: parts drawn as text, put together into frames.

Letters:  O outline   B fur   S stripes/patches   L light fur (chest, belly)
          X ears/paws/tail tip (dark on a siamese)   M muzzle   P pink (nose, ears)
          E eyes   R mouth / heart red   W white shine   H headset band   C headset cup
          F fish   Y yarn   N light (notes, zzz)   G grey (poof)
"""

W, H = 26, 22          # frame size in pixels; paws stand on the last row

HEAD = """
.O......O.
OPO....OPO
OBBOOOOBBO
OBBBSSBBBO
OBEBBBBEBO
OBMMPPMMBO
OLMMMMMMLO
.OOOOOOOO.
"""

HEAD_CLOSED = """
.O......O.
OPO....OPO
OBBOOOOBBO
OBBBSSBBBO
OBOOBBOOBO
OBMMPPMMBO
OLMMMMMMLO
.OOOOOOOO.
"""

HEAD_HAPPY = """
.O......O.
OPO....OPO
OBBOOOOBBO
OBOBSSBOBO
OOBOBBOBOO
OPMMPPMMPO
OLMMMMMMLO
.OOOOOOOO.
"""

HEAD_SAD = """
..........
OO......OO
OPOOOOOOPO
OBOBSSBOBO
OBBEBBEBBO
OBMMPPMMBO
OLMMMMMMLO
.OOOOOOOO.
"""

HEAD_YAWN = """
.O......O.
OPO....OPO
OBBOOOOBBO
OBBBSSBBBO
OBOOBBOOBO
OBMORROMBO
OLMORROMLO
.OOOOOOOO.
"""

BODY = """
.OOOOOOOOOOOO.
OBBSBBSBBSBBBO
OBBSBBSBBSBBBO
OBBBBBBBBBBBBO
OBLLLLLLLLLLBO
.OOOOOOOOOOOO.
"""

LEG = """
OXO
OXO
OOO
"""

LEG_SHORT = """
OXO
OOO
"""

TAIL_UP = """
.OO..
OXXO.
OXO..
OBO..
OBO..
.OBO.
.OBO.
..OBO
..OBO
"""

TAIL_UP2 = """
..OO.
.OXXO
.OXO.
.OBO.
OBO..
OBO..
.OBO.
..OBO
..OBO
"""

TAIL_BACK = """
OO.....
OXOOOO.
OXBBBBO
.OOOOOO
"""

SIT_BODY = """
...OOOOOO...
..OBBBBBBO..
.OBSBBBBBLO.
.OBSBBBBLLO.
OBBSBBBBLLO.
OBBBBBBBLLO.
OBBBBOBBOXO.
OBBBOBBBOXO.
OBBBOBBBOXO.
.OOOOOOOOOO.
"""

TAIL_SIT = """
OO.....
OXOOOOO
OBBBBBB
.OOOOOO
"""

TAIL_SIT2 = """
.......
OOOOOOO
OXBBBBB
.OOOOOO
"""

LOAF = """
....OOOOOOOO....
..OOBSBBSBBBOO..
.OBBBSBBSBBBBBO.
OBBBBBBBBBBBBBBO
OBBBBBBBBBBBBBBO
OBOOOOOOOOOBBBBO
OXXBBBBBBBBOBBBO
.OOOOOOOOOOOOOO.
"""

HELD_BODY = """
.OOOOOO.
OLLBBBBO
OLLBSBBO
OLLBSBBO
OLBBSBBO
OBBBBBBO
OBBBBBBO
OBBBBBBO
.OOOOOO.
"""

TAIL_DOWN = """
..OBO
..OBO
.OBO.
.OBO.
OBO..
OXO..
OXO..
.O...
"""

PAW_UP = """
.OO
OXO
OXO
"""

HEADSET = """
...HHHH...
..H....H..
.H......H.
.H......H.
"""

CUP = """
OO
CO
CO
OO
"""

HEART = """
.OO.OO.
ORWORRO
ORRRRRO
.ORRRO.
..ORO..
...O...
"""

FISH = """
..OOOO.OO
.OFFFFOFO
OFOFFFFFO
.OFFFFOFO
..OOOO.OO
"""

YARN = """
..OOO..
.OYYYO.
OYWYYYO
OYYWYYO
OYYYWYO
.OYYYO.
..OOO.O
"""

NOTE = """
..OOOO
..ONNO
..ONOO
..OO..
OOOO..
ONNO..
OOOO..
"""

POOF = """
..GGG....
.GNNNG.G.
GNNNNNGNG
GNNNNNNNG
.GNNNNNG.
..GGGGG..
"""

NIGHTCAP = """
WW...........
WWQO.........
.OQQOOOO.....
..OQQQQQOO...
..OQQQQQQQO..
.OWWWWWWWWWO.
"""

PARACHUTE = """
.......OOOOOOOOOO.......
.....OORRRWWWWRRROO.....
...OORRRRWWWWWWRRRROO...
..ORRRRRWWWWWWWWRRRRRO..
.ORRRRRRWWWWWWWWRRRRRRO.
ORRRRRRRWWWWWWWWRRRRRRRO
OOOOOOOOOOOOOOOOOOOOOOOO
.G.........GG.........G.
..G........GG........G..
...G.......GG.......G...
....G......GG......G....
.....G.....GG.....G.....
......G....GG....G......
.......G...GG...G.......
........G..GG..G........
.........OOOOOO.........
"""

BED = """
..OOOOOOOOOOOOOOOOOOOOOOOOOO..
.OTTTTTTTTTTTTTTTTTTTTTTTTTTO.
OTTUUUUUUUUUUUUUUUUUUUUUUUUTTO
OTUUUUUUUUUUUUUUUUUUUUUUUUUUTO
OTTTTTTTTTTTTTTTTTTTTTTTTTTTTO
OTTTTTTTTTTTTTTTTTTTTTTTTTTTTO
.OOOOOOOOOOOOOOOOOOOOOOOOOOOO.
"""

BED_FRONT = """
OTTTTTTTTTTTTTTTTTTTTTTTTTTTTO
OTTTTTTTTTTTTTTTTTTTTTTTTTTTTO
.OOOOOOOOOOOOOOOOOOOOOOOOOOOO.
"""

TRAMPOLINE = """
.OOOOOOOOOOOOOOOOOOOOOOOO.
ORRRRRRRRRRRRRRRRRRRRRRRRO
OFFFFFFFFFFFFFFFFFFFFFFFFO
.OOOOOOOOOOOOOOOOOOOOOOOO.
..OO..................OO..
..OO..................OO..
.OOOO................OOOO.
"""

TRAMPOLINE_DOWN = """
..........................
.OOO..................OOO.
ORRROOOOOOOOOOOOOOOOOORRRO
.OOFFFFFFFFFFFFFFFFFFFFOO.
..OOOOOOOOOOOOOOOOOOOOOO..
..OO..................OO..
.OOOO................OOOO.
"""

HOOKGUN = """
O.O.O
OGGGO
.OGO.
.ORO.
.ORO.
.OKO.
.OOO.
"""

PORTALGUN = """
.OOOO..
OKKKGVV
.OOOO..
"""

HOOK = """
OGGGO
OG.GO
O...O
"""

COATS = {
    # key: (label, colors)
    "tabby": ("Orange tabby", dict(O="#3b2417", B="#f0a04b", S="#c4642a", L="#fbe3c0", X="#f0a04b",
                                   M="#fbe3c0", P="#f28b9a", E="#3f8a2c")),
    "black": ("Black", dict(O="#0d0d10", B="#2d2d35", S="#26262d", L="#3b3b45", X="#2d2d35",
                            M="#3b3b45", P="#d77a8a", E="#f2cf2c")),
    "grey": ("Grey & white", dict(O="#2b2b30", B="#8d929c", S="#727781", L="#f4f4f4", X="#f4f4f4",
                                  M="#f4f4f4", P="#f0a0ae", E="#e0a92a")),
    "white": ("White", dict(O="#555563", B="#f7f7f7", S="#e8e8ee", L="#ffffff", X="#f7f7f7",
                            M="#ffffff", P="#f3a3b3", E="#3d9be0")),
    "calico": ("Calico", dict(O="#2e2320", B="#f8f3ec", S="#e8883a", L="#ffffff", X="#f8f3ec",
                              M="#ffffff", P="#f29aa8", E="#6aaa3c")),
    "siamese": ("Siamese", dict(O="#2b1f18", B="#f1e6d2", S="#e9dcc4", L="#faf4e8", X="#4a3426",
                                M="#4a3426", P="#b87b7b", E="#3d8fe0")),
}

COMMON = dict(R="#d9344f", W="#ffffff", H="#2f2f3a", C="#e0455a", F="#6f9fd8", Y="#e86fa8",
              N="#f4f4f8", G="#9a9aa6", Q="#6f95d0", T="#b07a4a", U="#e7c6e8",
              K="#3a3a44", V="#34d399", Z="#a7f3d0", A="#0f766e")
CALICO_PATCHES = ("#e8883a", "#e8883a", "#5a4d47")


def grid(text):
    return [list(line) for line in text.strip("\n").split("\n")]


PARTS = {name: grid(value) for name, value in dict(globals()).items()
         if isinstance(value, str) and name.isupper() and "\n" in value}


def blank():
    return [["." for _ in range(W)] for _ in range(H)]


BODY_PARTS = {"BODY", "SIT_BODY", "LOAF", "HELD_BODY"}


def stamp(canvas, part, x, y):
    body = part in BODY_PARTS if isinstance(part, str) else False
    for r, row in enumerate(PARTS[part] if isinstance(part, str) else part):
        for c, ch in enumerate(row):
            if ch != "." and 0 <= y + r < len(canvas) and 0 <= x + c < len(canvas[0]):
                canvas[y + r][x + c] = ch.lower() if body and ch in "BSL" else ch
    return canvas


# ---------------------------------------------------------------- frames
# Every frame also says where the head is, so the headset can be put on it.

LEGS_STILL = [(6, 0, 0), (9, 0, 0), (13, 0, 0), (16, 0, 0)]
LEGS_WALK = [
    LEGS_STILL,
    [(6, -1, 0), (9, 1, 1), (13, 1, 1), (16, -1, 0)],
    LEGS_STILL,
    [(6, 1, 1), (9, -1, 0), (13, -1, 0), (16, 1, 1)],
]


def standing(legs, head="HEAD", tail="TAIL_UP", head_dy=0, body_dy=0, head_dx=0, leg="LEG"):
    c = blank()
    stamp(c, tail, 1, 7 + body_dy)
    stamp(c, "BODY", 5, 14 + body_dy)
    for x, dx, lift in legs:
        stamp(c, leg, x + dx, 19 - lift + body_dy + (1 if leg == "LEG_SHORT" else 0))
    hx, hy = 15 + head_dx, 7 + head_dy + body_dy
    stamp(c, head, hx, hy)
    return c, (hx, hy)


def sitting(head="HEAD", tail="TAIL_SIT", head_dy=0, paw=False):
    c = blank()
    stamp(c, tail, 2, 18)
    stamp(c, "SIT_BODY", 9, 12)
    hx, hy = 12, 5 + head_dy
    stamp(c, head, hx, hy)
    if paw:
        stamp(c, "PAW_UP", hx + 6, hy + 6)
    return c, (hx, hy)


def loaf(head="HEAD_CLOSED", head_dy=0):
    c = blank()
    stamp(c, "LOAF", 3, 14)
    hx, hy = 14, 10 + head_dy
    stamp(c, head, hx, hy)
    return c, (hx, hy)


def crouch(wiggle=0, head="HEAD"):
    c = blank()
    stamp(c, "TAIL_BACK", 0 + wiggle, 13)
    stamp(c, "BODY", 5 + wiggle, 16)
    for x, dx, _lift in LEGS_STILL:
        stamp(c, "LEG_SHORT", x + dx + wiggle, 20)
    hx, hy = 15, 10
    stamp(c, head, hx, hy)
    return c, (hx, hy)


def jumping():
    c = blank()
    stamp(c, "TAIL_BACK", 0, 11)
    stamp(c, "BODY", 5, 13)
    for x, dx in ((6, -2), (9, -1), (13, 1), (16, 2)):
        stamp(c, "LEG", x + dx, 17)
    hx, hy = 15, 6
    stamp(c, "HEAD", hx, hy)
    return c, (hx, hy)


def held(head="HEAD_SAD"):
    c = blank()
    stamp(c, "TAIL_DOWN", 6, 13)
    stamp(c, "HELD_BODY", 9, 8)
    stamp(c, "LEG", 9, 16)
    stamp(c, "LEG", 14, 16)
    stamp(c, "LEG_SHORT", 15, 11)
    hx, hy = 8, 1
    stamp(c, head, hx, hy)
    return c, (hx, hy)


def aiming(gun):
    c, (hx, hy) = sitting()
    if gun == "HOOKGUN":
        stamp(c, "PAW_UP", hx + 8, hy + 5)
        stamp(c, "HOOKGUN", hx + 8, hy - 2)
    else:
        stamp(c, "PAW_UP", hx + 7, hy + 6)
        stamp(c, "PORTALGUN", hx + 8, hy + 6)
    return c, (hx, hy)


def portal(frame):
    """A swirling oval, 12 x 22 pixels; frame 0-2 turns the swirl."""
    import math
    rows = []
    for y in range(22):
        row = []
        for x in range(12):
            dx, dy = (x - 5.5) / 6.0, (y - 10.5) / 11.0
            r = dx * dx + dy * dy
            if r > 1.0:
                row.append(".")
            elif r > 0.78:
                row.append("A")
            else:
                angle = math.atan2(dy, dx)
                band = int((angle / (2 * math.pi) * 3 + math.sqrt(r) * 4 + frame) * 2) % 3
                row.append("VZN"[band] if r > 0.08 else "N")
        rows.append(row)
    return rows


def build_frames():
    f = {}
    for i, legs in enumerate(LEGS_WALK):
        f[f"walk{i}"] = standing(legs, tail="TAIL_UP" if i < 2 else "TAIL_UP2")
    f["stand"] = standing(LEGS_STILL)
    f["stand2"] = standing(LEGS_STILL, tail="TAIL_UP2")
    f["stand_sad"] = standing(LEGS_STILL, head="HEAD_SAD", tail="TAIL_UP2")
    f["sit"] = sitting()
    f["sit_tail"] = sitting(tail="TAIL_SIT2")
    f["sit_blink"] = sitting(head="HEAD_CLOSED")
    f["sit_bob"] = sitting(head_dy=1, tail="TAIL_SIT2")
    f["sit_bob_closed"] = sitting(head="HEAD_CLOSED", head_dy=1)
    f["sit_happy"] = sitting(head="HEAD_HAPPY")
    f["sit_happy2"] = sitting(head="HEAD_HAPPY", head_dy=1, tail="TAIL_SIT2")
    f["sit_sad"] = sitting(head="HEAD_SAD")
    f["sit_sad2"] = sitting(head="HEAD_SAD", tail="TAIL_SIT2")
    f["yawn"] = sitting(head="HEAD_YAWN")
    f["groom0"] = sitting(head="HEAD_CLOSED", paw=True)
    f["groom1"] = sitting(head="HEAD_CLOSED", head_dy=1, paw=True)
    f["sleep0"] = loaf()
    f["sleep1"] = loaf(head_dy=1)
    f["crouch0"] = crouch()
    f["crouch1"] = crouch(wiggle=1)
    f["jump"] = jumping()
    f["held"] = held()
    f["dangle"] = held(head="HEAD")
    f["dangle_happy"] = held(head="HEAD_HAPPY")
    f["aim_hook"] = aiming("HOOKGUN")
    f["aim_portal"] = aiming("PORTALGUN")
    f["eat0"] = standing(LEGS_STILL, head="HEAD_CLOSED", head_dy=6, head_dx=1)
    f["eat1"] = standing(LEGS_STILL, head="HEAD_CLOSED", head_dy=7, head_dx=1)
    return f


FRAMES = build_frames()


PAJAMA = {"b": "#8db4e6", "s": "#5f86c8", "l": "#dce9f8"}


def color_of(ch, coat, x, y, pajamas=False):
    if ch in "bsl":
        if pajamas:
            return PAJAMA[ch]
        ch = ch.upper()
    colors = COATS[coat][1]
    if coat == "calico" and ch in "BS":
        # patches of orange and black on white
        pick = ((x // 4) * 3 + (y // 3) * 5 + (1 if ch == "S" else 0)) % 6
        return CALICO_PATCHES[pick] if pick < 3 else colors["B"]
    if ch in colors:
        return colors[ch]
    return COMMON.get(ch)


def hex_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))


CUP_RIGHT = [row[::-1] for row in PARTS["CUP"]]


def with_headset(frame):
    canvas, (hx, hy) = frame
    c = [row[:] for row in canvas]
    stamp(c, "HEADSET", hx, hy - 2)
    stamp(c, "CUP", hx - 1, hy + 2)
    stamp(c, CUP_RIGHT, hx + 9, hy + 2)
    return c, (hx, hy)


def with_nightcap(frame):
    canvas, (hx, hy) = frame
    c = [row[:] for row in canvas]
    stamp(c, "NIGHTCAP", hx - 3, hy - 5)
    return c, (hx, hy)


def render(canvas, coat, scale, flip=False, pajamas=False):
    """A cairo image of a frame (or any part grid)."""
    import cairo
    rows = len(canvas)
    cols = len(canvas[0])
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, cols * scale, rows * scale)
    cr = cairo.Context(surface)
    for y, row in enumerate(canvas):
        for x, ch in enumerate(row):
            if ch == ".":
                continue
            value = color_of(ch, coat, x, y, pajamas)
            if value is None:
                continue
            cr.set_source_rgb(*hex_rgb(value))
            px = (cols - 1 - x) if flip else x
            cr.rectangle(px * scale, y * scale, scale, scale)
            cr.fill()
    surface.flush()
    return surface


BUBBLE = grid("""
..OOOOOOOOOOO..
.ONNNNNNNNNNNO.
ONNNNNNNNNNNNNO
ONNNNNNNNNNNNNO
ONNNNNNNNNNNNNO
ONNNNNNNNNNNNNO
ONNNNNNNNNNNNNO
.ONNNNNNNNNNNO.
..OOOOOOOOOOO..
...OO..........
..ONO..........
...O...........
""")


def bubble_with(part, x, y):
    canvas = [row[:] for row in BUBBLE]
    return stamp(canvas, part, x, y)


def head_icon():
    return PARTS["HEAD"]
