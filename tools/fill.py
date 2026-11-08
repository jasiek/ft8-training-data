"""Second probe pass: fill bands still missing from selection.json with targeted random picks."""
import concurrent.futures as cf, json, os, random, sys
sys.path.insert(0, os.path.dirname(__file__))
import ft8rec as F

sel_path = os.path.join(F.ROOT, "selection.json")
sel = json.load(open(sel_path))
have = {c["band"] for c in sel["chosen"]}
used_sites = {(round(c["lat"], 1), round(c["lon"], 1)) for c in sel["chosen"]}
missing = [b for b in F.BANDS if b not in have]
per_band = int(sys.argv[1]) if len(sys.argv) > 1 else 8
rng = random.Random()
rxs = [r for r in json.load(open(F.RECEIVERS)) if r["type"] == "KiwiSDR"
       and (round(r["lat"], 1), round(r["lon"], 1)) not in used_sites]
rng.shuffle(rxs)
with cf.ThreadPoolExecutor(48) as ex:
    st = list(ex.map(lambda r: F.kiwi_status(r["url"]), rxs))
pool = [r for r, s in zip(rxs, st) if F.candidate_ok(s)]
F.log(f"missing {missing}; {len(pool)} candidates")
jobs, taken = [], set()
for band in missing:
    day = band not in ("60m", "80m", "160m")
    n = 0
    for r in pool:
        lh = F.local_hour(r["lon"])
        ok = (9 <= lh <= 16) if day else (lh >= 19 or lh <= 5)
        if ok and r["url"] not in taken:
            taken.add(r["url"]); jobs.append((r, band)); n += 1
            if n >= per_band:
                break
F.log(f"probing {len(jobs)} pairs")
with cf.ThreadPoolExecutor(len(jobs)) as ex:
    res = list(ex.map(lambda j: F.probe_one(*j), jobs))
best = {}
for (r, band), (n, dec) in sorted(zip(jobs, res), key=lambda x: -x[1][0]):
    F.log(f"  {n:3d} {band:5s} {r['url']} ({r['station'][:40]}, local {F.local_hour(r['lon']):.1f}h)")
    if n >= 3 and band not in best:
        best[band] = dict(url=r["url"], band=band, freq_khz=F.BANDS[band], label=r["label"],
                          station=r["station"], lat=r["lat"], lon=r["lon"], probe_decodes=n, probe_sample=dec[:5])
sel["chosen"] += list(best.values())
json.dump(sel, open(sel_path, "w"), indent=1)
F.log(f"added {list(best)}; now {len(sel['chosen'])} chosen")
