# Oval Circuit

Oval Circuit runs in the official Mech Royale Python wrapper. It builds a coarse walkable grid from the supplied game map, uses weighted A* to route to an objective, and checks nearby waypoints with the local movement model. The action controller scales its lookahead near a waypoint so it can reach a pickup or finish a turn.

Resource selection considers item value, distance, observation age, vertical separation, and time left to return to the shrinking circle. The bot remembers recently seen items for six seconds. It can approach crates before the first zone shrink.

The route benchmark uses paired ground-only journeys between map points with a blocked straight route. It measures arrivals under approximate movement and collision rules. The official recorded-stream checker measures protocol conformance and decision latency. Practice placement requires the competition grader.

## Design references

- [Amit Patel's A* implementation guide](https://www.redblobgames.com/pathfinding/a-star/implementation.html)
- [Craig Reynolds on path following and steering](https://www.red3d.com/cwr/papers/1999/gdc99steer.html)
- [id Software's item-goal selection in Quake III Arena](https://github.com/id-Software/Quake-III-Arena/blob/master/code/botlib/be_ai_goal.c)

To rebuild the navigation data, use Python 3.13 and run `python build_navigation.py PATH_TO_KIT/arena ground.json` from the bot directory. The runtime verifies the arena fingerprint before loading the precomputed edges.

With few cubes, the bot searches around the staging ring and scans for resources. It gives damaged opponents extra priority and keeps its target while an opportunity lasts.