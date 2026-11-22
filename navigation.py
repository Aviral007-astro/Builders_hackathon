"""Grid routing for ground-level game objectives."""
import hashlib
import json
from pathlib import Path
import heapq
import math


STEPS = ((1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1))


def walk_segment(arena, x, y, z, end, radius, height, step_up):
    dx, dy = end[0]-x, end[1]-y
    distance = math.hypot(dx,dy)
    if distance < 0.02:
        return True
    dx, dy = dx/distance, dy/distance
    count = max(1, math.ceil(distance / 1.5))
    length = distance / count
    for i in range(count):
        if arena.walk(x,y,z,height,dx,dy,length,step_up,radius) < 1:
            return False
        x, y = x+dx*length, y+dy*length
        z = arena.ground(x,y)
    return True


class Navigator:
    step = 8

    def __init__(self, arena, directory=None):
        self.arena = arena
        self.open = set()
        self.path = []
        self.destination = None
        self.refresh = 0
        self.edges = {}
        data_file = Path(__file__).with_name("ground.json")
        if directory and data_file.is_file():
            root = Path(directory) / "v2"
            signature = hashlib.sha256((root/"world.json").read_bytes() + (root/"terrain.i16").read_bytes()).hexdigest()
            data = json.loads(data_file.read_text())
            if signature == data["signature"]:
                self.edges = {tuple(map(int, k.split(","))): v for k,v in data["edges"].items()}
                self.open = set(self.edges)
                return
        b = arena.bounds
        for i in range(math.ceil((b["minX"] + 4) / self.step), math.floor((b["maxX"] - 4) / self.step) + 1):
            for j in range(math.ceil((b["minY"] + 4) / self.step), math.floor((b["maxY"] - 4) / self.step) + 1):
                x, y = i * self.step, j * self.step
                z = arena.ground(x, y)
                if all(not arena.solid(x + dx, y + dy, z + 0.9, z + 9)
                       for dx, dy in ((0, 0), (2.4, 0), (-2.4, 0), (0, 2.4), (0, -2.4))):
                    self.open.add((i, j))

    def closest(self, point):
        x, y = point
        i, j = round(x / self.step), round(y / self.step)
        for radius in range(7):
            available = [(i + dx, j + dy) for dx in range(-radius, radius + 1)
                         for dy in range(-radius, radius + 1)
                         if max(abs(dx), abs(dy)) == radius and (i + dx, j + dy) in self.open]
            if available:
                return min(available, key=lambda q: (q[0]*self.step-x)**2 + (q[1]*self.step-y)**2)
        return None

    def search(self, start, goal):
        frontier = [(math.dist(start, goal), 0, start)]
        parent, costs = {}, {start: 0}
        while frontier and len(costs) < 5500:
            _, cost, here = heapq.heappop(frontier)
            if here == goal:
                route = []
                while here != start:
                    route.append((here[0] * self.step, here[1] * self.step))
                    here = parent[here]
                return route[::-1]
            if cost != costs[here]:
                continue
            x, y = here
            for index, (dx, dy) in enumerate(STEPS):
                if self.edges and not self.edges[here] & (1 << index):
                    continue
                nxt = x + dx, y + dy
                if nxt not in self.open or (dx and dy and ((x+dx,y) not in self.open or (x,y+dy) not in self.open)):
                    continue
                candidate = cost + (1.414214 if dx and dy else 1)
                if candidate < costs.get(nxt, math.inf):
                    costs[nxt], parent[nxt] = candidate, here
                    heapq.heappush(frontier, (candidate + 1.12 * math.dist(nxt, goal), candidate, nxt))
        return []

    def waypoint(self, me, goal, now):
        x, y, z = me["pos"]
        if math.dist((x,y),goal) < 9 or z > self.arena.ground(x,y) + 3:
            return goal
        if self.destination is None or math.dist(self.destination,goal) > 7 or now >= self.refresh:
            self.destination = goal
            self.refresh = now + 2.2
            start, end = self.closest((x,y)), self.closest(goal)
            self.path = self.search(start,end) if start is not None and end is not None else []
        while self.path and math.dist(self.path[0], (x,y)) < 2.5:
            self.path.pop(0)
        if not self.path:
            return goal
        # Use the furthest nearby waypoint reachable by the local movement model.
        for point in reversed(self.path[:5]):
            dx, dy = point[0]-x, point[1]-y
            distance = math.hypot(dx,dy)
            if 0 < distance < 33 and walk_segment(self.arena,x,y,z,point,me["stats"]["radius"],
                                                        me["stats"]["height"],me["stats"]["step_up"]):
                return point
        return self.path[0]
