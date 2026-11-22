"""Placement-focused strategy for the Mech Royale simulator."""
from array import array
from collections import defaultdict
import json
import math
from pathlib import Path
import sys

import mechfight as mf
from navigation import Navigator, walk_segment


DIRECTIONS = [(math.cos(i * math.tau / 16), math.sin(i * math.tau / 16))
              for i in range(16)] + [(0.0, 0.0)]
UPGRADES = {"nanites": 12, "servos": 11, "armor": 10, "plating": 9,
            "jets": 8, "laser_charge": 7, "laser_damage": 7,
            "reactor": 6, "gun_damage": 5, "gun_rate": 5, "capacitor": 4,
            "lightning_range": 4, "mortar_reload": 4, "gyros": 3}
PREFERENCE = {"gun": 0, "mortar": 1, "lightning": 2, "laser": 3}


def clamp(value, minimum, maximum):
    return min(maximum, max(minimum, value))


def inside(x, y, ring):
    cross = False
    last_x, last_y = ring[-1]
    for next_x, next_y in ring:
        if (last_y > y) != (next_y > y):
            if x < (next_x - last_x) * (y - last_y) / (next_y - last_y) + last_x:
                cross = not cross
        last_x, last_y = next_x, next_y
    return cross


class Arena:
    cell = 24

    def __init__(self, directory):
        root = Path(directory) / "v2"
        with (root / "world.json").open(encoding="utf-8") as stream:
            world = json.load(stream)
        self.bounds = world["bounds"]
        self.meta = world["terrain"]
        self.heightmap = array("h", (root / "terrain.i16").read_bytes())
        if sys.byteorder != "little":
            self.heightmap.byteswap()
        self.index = defaultdict(list)
        for shape in world["prisms"]:
            ring = list(zip(shape["ring"][::2], shape["ring"][1::2]))
            holes = [list(zip(hole[::2], hole[1::2])) for hole in shape["holes"] if len(hole) >= 6]
            if len(ring) < 3:
                continue
            xs, ys = zip(*ring)
            west, east, south, north = min(xs), max(xs), min(ys), max(ys)
            item = shape["base"], shape["top"], west, east, south, north, ring, holes
            for i in range(math.floor(west / self.cell), math.floor(east / self.cell) + 1):
                for j in range(math.floor(south / self.cell), math.floor(north / self.cell) + 1):
                    self.index[i, j].append(item)

    def ground(self, x, y):
        meta = self.meta
        nx, ny = meta["nx"], meta["ny"]
        fx = clamp((x - meta["minX"]) / meta["step"], 0, nx - 1.000001)
        fy = clamp((y - meta["minY"]) / meta["step"], 0, ny - 1.000001)
        i, j = int(fx), int(fy)
        u, v = fx - i, fy - j
        at = j * nx + i
        h = self.heightmap
        return meta["scale"] * ((h[at] * (1 - u) + h[at + 1] * u) * (1 - v)
                                + (h[at + nx] * (1 - u) + h[at + nx + 1] * u) * v)

    def solid(self, x, y, bottom, top):
        for base, roof, west, east, south, north, ring, holes in self.index.get(
            (math.floor(x / self.cell), math.floor(y / self.cell)), ()
        ):
            if (base < top and bottom < roof and west <= x <= east and south <= y <= north
                    and inside(x, y, ring) and not any(inside(x, y, hole) for hole in holes)):
                return True
        return False

    def walk(self, x, y, feet, height, dx, dy, length, step, radius):
        b = self.bounds
        last_ground = self.ground(x, y)
        for fraction in (0.30, 0.65, 1.0):
            nx, ny = x + dx * length * fraction, y + dy * length * fraction
            if not (b["minX"] + radius < nx < b["maxX"] - radius
                    and b["minY"] + radius < ny < b["maxY"] - radius):
                return fraction - 0.35
            terrain = self.ground(nx, ny)
            level = max(terrain, feet - 0.7 if feet > last_ground + 2 else terrain)
            if terrain > last_ground + max(step, length * 0.30 * 0.92) and terrain > feet + step:
                return fraction - 0.35
            if any(self.solid(nx - dy * offset, ny + dx * offset, level + 0.18, level + height)
                   for offset in (0, -radius * 0.77, radius * 0.77)):
                return fraction - 0.35
            last_ground = terrain
        return 1.0

    def clear(self, start, end, spacing=5.0, gravity=0.0, travel=0.0):
        x, y, z = start
        dx, dy, dz = (end[i] - start[i] for i in range(3))
        count = max(1, math.ceil(math.hypot(dx, dy) / spacing))
        for i in range(1, count):
            frac = i / count
            nx, ny = x + dx * frac, y + dy * frac
            nz = z + dz * frac + 0.5 * gravity * travel ** 2 * frac * (1 - frac)
            if self.solid(nx, ny, nz - 0.08, nz + 0.08) or self.ground(nx, ny) > nz:
                return False
        return True


class Bot:
    name = "Oval Relay"

    def __init__(self, hello):
        self.config = hello["config"]
        self.arena = Arena(hello["map"]["dir"])
        self.navigator = Navigator(self.arena, hello["map"]["dir"])
        self.loot_memory = {}
        self.phase = (hello["seed"] % 97) / 97 * math.tau
        self.pickup_radius = self.config["loot"]["pickup_radius"]
        self.direction = (0.0, 0.0)
        self.last_position = None
        self.stalled = 0
        self.attacked = -100.0
        self.attack_bearing = 0.0

    def destination(self, obs, nearby, pressure):
        me = obs["self"]
        zone = obs["zone"]
        x, y, z = me["pos"]
        speed = me["stats"]["walk_speed"]
        now = obs["t"]
        cx, cy = zone["next_center"]
        radius = zone["next_radius"]
        distance = math.hypot(x - cx, y - cy)
        future_gap = distance - max(0, radius - min(22, radius * 0.24))
        urgent = (not me["in_zone"] or radius < 10 or
                  future_gap > 0 and future_gap / max(1, speed * 0.8) + 8 > zone["shrink_end"] - now)
        if urgent:
            return (cx, cy), True

        held = {weapon["kind"] for weapon in me["weapons"] if weapon}
        # Keep short-lived observations so turning the torso cannot erase a goal.
        for group in ("crates", "cubes", "pickups", "drops"):
            for item in obs[group]:
                self.loot_memory[item["id"]] = (group, item, now)
        visible_ids = {item["id"] for group in ("crates", "cubes", "pickups", "drops") for item in obs[group]}
        best, target = -math.inf, None
        for key, (group, item, seen) in list(self.loot_memory.items()):
            lx, ly, lz = item["pos"]
            dist = math.hypot(lx - x, ly - y)
            if now - seen > 6 or (key not in visible_ids and dist < 8):
                del self.loot_memory[key]
                continue
            if pressure > 1.15 or abs(lz - z) > 7 or dist > 140:
                continue
            if group == "cubes":
                value = 100
            elif group == "pickups":
                if PREFERENCE.get(item["kind"], 0) <= max(PREFERENCE.get(k, 0) for k in held | {"gun"}):
                    continue
                value = 83 if item["kind"] == "laser" else 62
            elif group == "crates":
                value = 55 if me["cubes"] < 10 else 24
            else:
                if item["lands_in"] > 6 or item["holder"] not in (None, me["id"]):
                    continue
                value = 100
            return_trip = max(0, math.hypot(lx - cx, ly - cy) - radius + 8)
            if (dist + return_trip) / max(1, speed * 0.7) + 8 > zone["shrink_end"] - now:
                continue
            if math.hypot(lx - zone["center"][0], ly - zone["center"][1]) > zone["radius"] - 4:
                continue
            score = value - dist * 0.52 - (now - seen) * 2 - abs(lz - z) * 1.2
            if score > best and score > 0:
                best, target = score, (lx, ly)
        if target is not None:
            return target, False

        angle = math.atan2(y - cy, x - cx) if distance > 1 else self.phase
        staging = radius * (0.63 if radius > 100 else 0.44)
        staging = min(staging, max(0, zone["radius"] - 15))
        return (cx + math.cos(angle) * staging, cy + math.sin(angle) * staging), False

    def move(self, obs, enemies, goal, urgent, pressure):
        me, zone = obs["self"], obs["zone"]
        x, y, z = me["pos"]
        radius, height = me["stats"]["radius"], me["stats"]["height"]
        gx, gy = goal
        remaining = math.hypot(gx - x, gy - y)

        if self.last_position is not None:
            if math.hypot(x - self.last_position[0], y - self.last_position[1]) < 0.14 and math.hypot(*self.direction) > 0.4:
                self.stalled += 1
            else:
                self.stalled = 0
        self.last_position = (x, y)
        fleeing = pressure > 0.8
        desired_speed = me["stats"]["walk_speed"]
        sprint = me["grounded"] and me["energy"] > (10 if urgent else 34) and (urgent or fleeing)
        if sprint:
            desired_speed = me["stats"]["boost_speed"]
        horizon = min(17, max(6.0, desired_speed * 0.9))
        if not fleeing:
            horizon = min(horizon, max(0.5, remaining))
        threat = enemies[:3]
        incoming = self.incoming(obs, me)
        if not threat and not incoming and remaining > 0.1:
            length = min(horizon, remaining)
            end = (x + (gx-x) * length/remaining, y + (gy-y) * length/remaining)
            if walk_segment(self.arena,x,y,z,end,radius,height,me["stats"]["step_up"]):
                scale = min(1, remaining / 3.5)
                self.direction = (scale*(gx-x)/remaining, scale*(gy-y)/remaining)
                return list(self.direction), sprint and remaining > 4, False
        best = -math.inf
        direction = (0.0, 0.0)
        open_distance = 0.0
        cover_weight = 1.0 if urgent else 1.7
        choices = DIRECTIONS
        if remaining > 0.1:
            choices = [((gx-x)/remaining, (gy-y)/remaining), *DIRECTIONS]
        for dx, dy in choices:
            if dx or dy:
                free = max(0, self.arena.walk(x, y, z, height, dx, dy, horizon,
                                              me["stats"]["step_up"], radius))
            else:
                free = 1
            length = horizon * free
            nx, ny = x + dx * length, y + dy * length
            distance_after = math.hypot(gx - nx, gy - ny)
            progress = remaining - distance_after
            safety = zone["radius"] - math.hypot(nx - zone["center"][0], ny - zone["center"][1])
            score = progress * (2.5 if urgent else 0.85)
            score -= max(0.0, 4.0 - safety) * (1.5 + zone["dps"] / 80)
            score -= 14.0 * (1 - free)
            score += (dx * self.direction[0] + dy * self.direction[1]) * 1.7
            if fleeing and (dx or dy):
                score += 2.0
            eye = (nx, ny, max(z, self.arena.ground(nx, ny)) + me["stats"]["eye_height"])
            for actor, distance in threat:
                tx, ty, tz = actor["pos"]
                new_distance = math.hypot(nx - tx, ny - ty)
                proximity = max(0.15, 1.0 - distance / 190)
                score += (new_distance - distance) * proximity * (1.35 if fleeing else 0.62)
                if distance < 135 and not self.arena.clear(eye, (tx, ty, tz + 7), spacing=7):
                    score += 9 * cover_weight * proximity
                if actor.get("charging") and distance < 170:
                    angle = math.atan2(ny - ty, nx - tx) - actor["aim"][0]
                    score -= 15 * max(0.0, math.cos(angle) - 0.94) / 0.06
            if incoming:
                score -= self.exposure(me, dx, dy, desired_speed * free, incoming)
            if fleeing and threat:
                angle = math.atan2(threat[0][0]["pos"][1] - y, threat[0][0]["pos"][0] - x)
                score += abs(dx * math.sin(angle) - dy * math.cos(angle)) * 3
            if score > best:
                best, direction, open_distance = score, (dx, dy), length

        jet = not me["grounded"] and urgent and me["energy"] > 20 and self.stalled > 2
        jump = (me["grounded"] and me["energy"] > 35 and self.stalled > 4
                and (urgent or remaining > 22) and open_distance < horizon * 0.8)
        if jump or jet:
            yaw = math.atan2(gy - y, gx - x)
            direction = (math.cos(yaw), math.sin(yaw))
        self.direction = direction
        if remaining < 5 and not fleeing and direction != (0.0, 0.0):
            return [clamp((gx - x) / 5, -1, 1), clamp((gy - y) / 5, -1, 1)], False, False
        return list(direction), (sprint and direction != (0.0, 0.0)) or jet, jump

    def incoming(self, obs, me):
        x, y, z = me["pos"]
        threats = []
        for item in obs["projectiles"]:
            px, py, pz = item["pos"]
            vx, vy, vz = item["vel"]
            offset_x, offset_y = px - x, py - y
            eta = -(offset_x * vx + offset_y * vy) / max(1, vx * vx + vy * vy)
            if not 0.04 < eta < 0.75:
                continue
            miss = math.hypot(offset_x + vx * eta, offset_y + vy * eta)
            gravity = self.config["weapons"]["mortar"]["gravity"] if item["kind"] == "mortar" else 0
            predicted_z = pz + vz * eta - 0.5 * gravity * eta * eta
            if miss < 14 and z - 6 < predicted_z < z + me["stats"]["height"] + 6:
                threats.append((eta, px + vx * eta, py + vy * eta, item["kind"]))
        return sorted(threats)[:6]

    def exposure(self, me, dx, dy, speed, incoming):
        x, y, _ = me["pos"]
        vx, vy, _ = me["vel"]
        score = 0.0
        for eta, px, py, kind in incoming:
            steering = clamp(eta * me["stats"]["ground_accel"] / max(speed, 8), 0, 1)
            if not me["grounded"]:
                steering *= 0.25
            nx = x + eta * (vx + (dx * speed - vx) * steering * 0.67)
            ny = y + eta * (vy + (dy * speed - vy) * steering * 0.67)
            danger = me["stats"]["radius"] + (7.0 if kind == "mortar" else 1.5)
            miss = math.hypot(px - nx, py - ny)
            score += max(0, 1.0 - miss / danger) * (22 if kind == "mortar" else 12)
        return score

    def shot(self, me, actor, weapon):
        x, y, z = me["pos"]
        tx, ty, tz = actor["pos"]
        vx, vy, vz = actor.get("vel", (0, 0, 0))
        kind, stats = weapon["kind"], weapon["stats"]
        size = 1.0 + self.config["cubes"]["size_per_cube"] * actor.get("cubes", 0)
        center = tz + (1.8 if actor.get("kind") == "npc" else 4.5 * size if actor.get("kind") == "mech" else 2.0)
        origin = x, y, z + me["stats"]["muzzle_height"] + (2.5 if kind == "mortar" else 0)
        dist = math.hypot(tx - x, ty - y)
        speed = stats.get("speed", 1e6)
        gravity = stats.get("gravity", 0) if kind == "mortar" else 0
        limit = stats.get("range", speed ** 2 / max(gravity, 1))
        if dist > limit * 0.98 or (kind == "mortar" and dist < 15):
            return None
        delay = max(0, dist - 3) / speed
        for _ in range(3):
            nx, ny = tx + vx * delay, ty + vy * delay
            nz = center + clamp(vz * delay, -5, 9)
            flat = math.hypot(nx - x, ny - y)
            lift = nz - origin[2]
            if gravity and flat > 0.1:
                discriminant = speed ** 4 - gravity * (gravity * flat ** 2 + 2 * lift * speed ** 2)
                if discriminant < 0:
                    return None
                pitch = math.atan((speed ** 2 - math.sqrt(discriminant)) / (gravity * flat))
            else:
                pitch = math.atan2(lift, flat)
            delay = max(0, flat / max(0.1, math.cos(pitch)) - 3) / speed
        if not me["stats"]["min_pitch"] <= pitch <= me["stats"]["max_pitch"]:
            return None
        if not self.arena.clear(origin, (nx, ny, nz), spacing=3.0, gravity=gravity, travel=delay):
            return None
        yaw = math.atan2(ny - y, nx - x)
        tolerance = clamp(math.atan2(2.4 * size, max(dist, 1)) * 0.90, 0.012, 0.24)
        if kind == "lightning":
            tolerance = math.radians(stats.get("cone_deg", 30)) * 0.40
        return (yaw, pitch), tolerance

    def act(self, obs):
        me = obs["self"]
        x, y, z = me["pos"]
        now = obs["t"]
        enemies = sorted(
            ((actor, math.hypot(actor["pos"][0] - x, actor["pos"][1] - y))
             for actor in obs["mechs"] if actor["kind"] == "mech"),
            key=lambda pair: pair[1],
        )
        for event in obs["events"]:
            if event["type"] == "damaged" and event.get("bearing") is not None:
                self.attacked, self.attack_bearing = now, event["bearing"]
        nearby = enemies[0][1] if enemies else 500
        hp = me["hp"] / max(1, me["max_hp"])
        pressure = sum(max(0, 1 - dist / 100) for _, dist in enemies[:5])
        pressure += 0.55 * (now - self.attacked < 2) + 0.7 * (hp < 0.45)
        goal, urgent = self.destination(obs, nearby, pressure)
        waypoint = self.navigator.waypoint(me, goal, now)
        move, boost, jump = self.move(obs, enemies, waypoint, urgent, pressure)
        action = {"tick": obs["tick"], "move": move, "boost": boost, "jump": jump,
                  "fire": [False, False]}
        near = [item for item in obs["pickups"] if
                math.hypot(item["pos"][0] - x, item["pos"][1] - y) < self.pickup_radius
                and abs(item["pos"][2] - z) < 3.9]
        held = [weapon["kind"] if weapon else None for weapon in me["weapons"]]
        if near:
            item = max(near, key=lambda pickup: PREFERENCE.get(pickup["kind"], -1))
            kind = item["kind"]
            if kind not in held and kind in PREFERENCE:
                if None in held and kind != "gun":
                    action["take"] = item["id"]
                elif None not in held and held[1] != "gun" and PREFERENCE[kind] > PREFERENCE.get(held[1], -1):
                    if not me["weapons"][1].get("charge"):
                        action.update(take=item["id"], drop=1)
        if obs["offer"]:
            choices = obs["offer"]["choices"]
            action["pick"] = max(range(len(choices)), key=lambda i: UPGRADES.get(choices[i]["id"], 0))

        targets = []
        for actor, dist in enemies[:5]:
            if dist < 140 or (hp > 0.50 and pressure < 0.85 and dist < 220):
                points = 48 - 0.20 * dist + 24 * (1 - actor.get("hp", 1400) / max(1, actor.get("max_hp", 1400)))
                targets.append((points, actor))
        if pressure < 0.6 and hp > 0.72 and not urgent:
            for actor in sorted(obs["crates"], key=lambda item: math.dist(item["pos"], me["pos"]))[:2]:
                distance = math.dist(actor["pos"], me["pos"])
                if distance < 72 and me["cubes"] < 10:
                    targets.append((14 - 0.1 * distance, actor))

        best = None
        for score, actor in targets:
            if hp < 0.32 and nearby > 32 and obs["alive"] > 2:
                continue
            for slot, weapon in enumerate(me["weapons"]):
                if not weapon or weapon.get("reload", 0) > 0.18:
                    continue
                if weapon["kind"] == "lightning" and me["energy"] < 19:
                    continue
                if firing := self.shot(me, actor, weapon):
                    value = score + {"gun": 0, "mortar": 5, "laser": 11, "lightning": 9}[weapon["kind"]]
                    value -= min(weapon["cooldown"], 1.5) * 12
                    if weapon.get("charge", 0):
                        value += 16
                    if best is None or value > best[0]:
                        best = value, actor, slot, firing
        if best:
            _, actor, slot, (aim, tolerance) = best
            action["aim"] = list(aim)
            for index, weapon in enumerate(me["weapons"]):
                if not weapon:
                    continue
                firing = (aim, tolerance) if index == slot else self.shot(me, actor, weapon)
                if firing is None:
                    continue
                (yaw, pitch), bound = firing
                aligned = (abs(math.remainder(yaw - me["aim"][0], math.tau)) <= bound
                           and abs(pitch - me["aim"][1]) <= max(0.02, bound))
                kind = weapon["kind"]
                if kind == "laser":
                    charge = weapon.get("charge", 0)
                    if index == slot and not urgent and weapon["cooldown"] <= 0.1:
                        required = weapon["stats"].get("charge", 0.75) * 0.91
                        action["fire"][index] = (charge < required or (not aligned and charge < required + 0.6))
                elif aligned and (kind != "lightning" or me["energy"] >= 19):
                    action["fire"][index] = True
        else:
            if nearby < 130:
                tx, ty, _ = enemies[0][0]["pos"]
                yaw = math.atan2(ty - y, tx - x)
            elif now - self.attacked < 3:
                yaw = self.attack_bearing
            else:
                yaw = math.atan2(goal[1] - y, goal[0] - x) + 0.8 * math.sin(now * 0.37 + self.phase)
            action["aim"] = [yaw, 0.0]
            action["reload"] = any(weapon and weapon["kind"] == "gun"
                                   and weapon.get("ammo", 12) < 8 for weapon in me["weapons"])
        return action


if __name__ == "__main__":
    mf.run(Bot)
