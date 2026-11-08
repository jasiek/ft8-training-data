# FT8 training recordings: KiwiSDR audio with jt9 labels

Real off-air FT8 recorded on 2026-11-04 from 03:02 to 03:33 UTC, from 10 KiwiSDR receivers picked at random from
receiverbook.de (one per site, a band suited to each site's local time). Used by https://github.com/jasiek/ft8-nn as
its real-data benchmark (`bench/run_real.py`) and for failure analysis. The network itself is trained on synthetic data.

| folder | band | receiver |
|---|---|---|
| `recordings/<NN>_<band>_<host>/` | see `meta.json` | url, dial kHz, location, probe result |

Each receiver folder: `slots/YYMMDD_HHMMSS.wav` (15 s, 12 kHz mono 16-bit, named by slot start),
`meta.json`, `decodes.txt` (WSJT-X `jt9 -8 -d 3` labels; 5 lines are jt9's placeholder `K1ABC …` decodes) and a
30-minute WAV (rebuilt on unpack). `selection.json` is the probe result (seed 372751130, plus the 160 m receiver
that was too busy and replaced by K1RA's; that replacement was recorded by hand), `record.log` the recording log.
`tools/ft8rec.py` (probe, record, decode), `tools/fill.py` (second probe pass) and `tools/kiwi.py` made it; no PSK
Reporter spots were captured for this run (see ../ft8-validation-set for runs with spots).

## Getting the files

The audio is stored in Git LFS as FLAC (lossless); unpack restores every WAV byte for byte and checks the SHA-256
manifest. Needs `git-lfs` and `flac`.

```sh
git clone https://github.com/jasiek/ft8-training-data   # next to ft8-nn, so ../ft8-training-data works there
cd ft8-training-data
python3 tools/datarepo.py unpack . --manifest MANIFEST.sha256
```

Source: audio from public KiwiSDR receivers; their operators are listed in each `meta.json`.
