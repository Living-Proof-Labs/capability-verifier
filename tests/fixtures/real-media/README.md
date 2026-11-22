# Real media bundles (capability-verifier#212)

Nothing here is written by hand.

- `media-2.2.0-sim-v3.9.1/`: the output of the producer itself. It is what livingproof-sim-mujoco
  v3.9.1 (`media.py`, `FORMAT = "livingproof-hallway-media/2.2.0"`) writes for one trial, taken from
  `pytest tests/test_media_split.py -k new_minor_format` in that repository (the toy robot, one PASS
  trial). The video is left out: this repository's media step does not read it.
- `media-2.1.0-lpv-run-44948999708/`: `media.json` and the one binding file from the artifact
  `unitree-g1-stock-mujoco-corner-left-mcap-stock` (id 14725530559) of livingproof-verification
  Actions run 44948999708, produced through this pipeline. The 215 KB `trial-01.mcap` and the video
  are left out.

In both, every trial has a `binding` entry and the binding file's `producer.id` is
`livingproof-sim-mujoco`: the simulator writes the binding, and this pipeline only carries it.
