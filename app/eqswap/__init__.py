"""eqswap: reversible model swaps for the EverQuest client.

A swap is two text edits (proven in-game, 2026-10-01):
  1. racedata.txt  - set the model-tag field of a race/gender row
  2. <zone>_chr.txt - make sure the zone loads the new model (`tag,tag` for EQG,
                      `tag,tag_chr` for WLD; nothing for globally loaded models)
Everything is recorded as a decision and replayed onto the CURRENT files, so
swaps survive official patches (the engine never ships or caches game files).
"""
__version__ = "0.1.0"
