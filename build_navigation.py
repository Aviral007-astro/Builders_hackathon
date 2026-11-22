"""Build ground navigation edges on the official game map."""
import hashlib
import json
from pathlib import Path
import sys
from bot import Arena
from navigation import Navigator, STEPS, walk_segment

root = Path(sys.argv[1])
arena = Arena(root)
nav = Navigator(arena)
edges = {}
for here in sorted(nav.open):
    x, y = (v * nav.step for v in here)
    z = arena.ground(x,y)
    mask = 0
    for index, (dx, dy) in enumerate(STEPS):
        nxt = here[0]+dx, here[1]+dy
        if nxt not in nav.open:
            continue
        if dx and dy and ((here[0]+dx,here[1]) not in nav.open or (here[0],here[1]+dy) not in nav.open):
            continue
        end = (nxt[0]*nav.step,nxt[1]*nav.step)
        if walk_segment(arena,x,y,z,end,2.65,9,1.2):
            mask |= 1 << index
    if mask:
        edges[f"{here[0]},{here[1]}"] = mask
signature = hashlib.sha256((root/"v2/world.json").read_bytes()+(root/"v2/terrain.i16").read_bytes()).hexdigest()
Path(sys.argv[2]).write_text(json.dumps({"signature":signature,"edges":edges},separators=(",",":")))
