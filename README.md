# Oval Drift

Oval Drift is a Python bot for the Builders Buildathon Mech Royale track. It reads the organizer's arena map at startup and uses an obstacle index, terrain heights, and line-of-sight checks to compare short movement options.

The bot prioritizes staying inside the closing zone, breaking contact under pressure, and collecting nearby upgrades. It uses visible projectile trajectories to help choose movement and uses the simulator's weapon stats when aiming.

The Dockerfile uses the official kit's JSON-lines wrapper. The kit supplies the arena, recorded observations, and conformance checker. The recorded observations verify protocol and latency. Practice matches on the official track measure placement.
