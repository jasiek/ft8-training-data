"""Pick random KiwiSDRs from receiverbook.de, probe FT8 bands, record 30 min each, decode.

  python3 tools/ft8rec.py probe   -> writes selection.json
  python3 tools/ft8rec.py record  -> records all selected receivers in parallel
  python3 tools/ft8rec.py decode  -> runs jt9 on every slot, writes labels + 30-min WAVs
"""
import concurrent.futures as cf
import json, os, random, re, subprocess, sys, tempfile, threading, time, urllib.request, wave
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
import kiwi

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "recordings")
RECEIVERS = os.environ.get("RECEIVERS_JSON")  # parsed from receiverbook.de/map
N_WANTED = 10
MINUTES = 30

# FT8 dial frequencies (kHz), USB
BANDS = {"160m": 1840.0, "80m": 3573.0, "60m": 5357.0, "40m": 7074.0, "30m": 10136.0,
         "20m": 14074.0, "17m": 18100.0, "15m": 21074.0, "12m": 24915.0, "10m": 28074.0}
DAY_BANDS = ["20m", "17m", "15m", "12m", "10m", "30m"]
NIGHT_BANDS = ["160m", "80m", "60m", "40m", "30m", "20m"]

lock = threading.Lock()


def log(*a):
    with lock:
        print(time.strftime("%H:%M:%S"), *a, flush=True)


def jt9_decode(wav_path):
    wav_path = os.path.abspath(wav_path)  # jt9 runs with cwd=tempdir
    with tempfile.TemporaryDirectory() as d:
        r = subprocess.run(["jt9", "-8", "-d", "3", "-a", d, "-t", d, wav_path],
                           capture_output=True, text=True, cwd=d, timeout=120)
    return [l.rstrip() for l in r.stdout.splitlines() if l.strip() and not l.startswith("<Decode")]


def kiwi_status(url):
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/status", timeout=8) as r:
            txt = r.read().decode("utf-8", "replace")
        return dict(l.split("=", 1) for l in txt.splitlines() if "=" in l)
    except Exception:
        return None


def local_hour(lon):
    h = datetime.now(timezone.utc).hour + datetime.now(timezone.utc).minute / 60
    return (h + lon / 15) % 24


def candidate_ok(st):
    if not st or st.get("offline") != "no" or st.get("status") != "active":
        return False
    try:
        users, umax = int(st.get("users", 99)), int(st.get("users_max", 0))
        lo, hi = map(int, st.get("bands", "0-0").split("-"))
    except ValueError:
        return False
    if users >= umax - 1:  # leave a channel free for humans
        return False
    if hi < 29_000_000 or lo > 1_800_000:
        return False
    return True


def probe_one(rx, band):
    """Record one FT8 slot on `band`, return decode count."""
    d = tempfile.mkdtemp(prefix="probe_")
    try:
        info = {}
        files = kiwi.record_slots(rx["url"], BANDS[band], d, time.time() + 35, max_slots=1,
                                  log=lambda *a: None, info=info)
        if not files:
            return 0, []
        for key in ("inactivity_timeout_mins", "ip_limit_mins"):
            v = info.get(key, 0) or 0
            if 0 < v < MINUTES + 10:
                log(f"  skip {rx['url']}: {key}={v}")
                return 0, []
        dec = jt9_decode(os.path.join(d, files[0]))
        return len(dec), dec
    except Exception as e:
        log(f"  probe {rx['url']} {band}: {e!r}")
        return 0, []


def cmd_probe(seed=None):
    seed = seed if seed is not None else random.randrange(1 << 30)
    rng = random.Random(seed)
    rxs = [r for r in json.load(open(RECEIVERS)) if r["type"] == "KiwiSDR"]
    rng.shuffle(rxs)
    # one receiver per station location, to keep sites distinct
    seen, pool = set(), []
    for r in rxs:
        key = (round(r["lat"], 1), round(r["lon"], 1))
        if key not in seen:
            seen.add(key)
            pool.append(r)
    log(f"seed={seed}, {len(pool)} distinct-site kiwis; checking status of all")
    with cf.ThreadPoolExecutor(32) as ex:
        stats = list(ex.map(lambda r: kiwi_status(r["url"]), pool))
    pool = [dict(r, status=s) for r, s in zip(pool, stats) if candidate_ok(s)]
    log(f"{len(pool)} candidates pass status checks")

    unfilled = list(BANDS)
    rng.shuffle(unfilled)
    chosen = []
    tried = set()
    rounds = 0
    while len(chosen) < N_WANTED and rounds < 8:
        rounds += 1
        # pair each unfilled band with a not-yet-tried receiver whose local time suits it
        jobs = []
        bands_this_round = unfilled if unfilled else rng.sample(list(BANDS), N_WANTED - len(chosen))
        for band in bands_this_round:
            for r in pool:
                if r["url"] in tried:
                    continue
                lh = local_hour(r["lon"])
                day = 8 <= lh <= 17
                if band in (DAY_BANDS if day else NIGHT_BANDS) or rounds >= 4:
                    tried.add(r["url"])
                    jobs.append((r, band))
                    break
        # also try 2 receivers per band to improve odds
        extra = []
        for band in bands_this_round:
            for r in pool:
                if r["url"] not in tried:
                    lh = local_hour(r["lon"])
                    if band in (DAY_BANDS if 8 <= lh <= 17 else NIGHT_BANDS) or rounds >= 4:
                        tried.add(r["url"])
                        extra.append((r, band))
                        break
        jobs += extra
        if not jobs:
            break
        log(f"round {rounds}: probing {len(jobs)} (receiver, band) pairs")
        with cf.ThreadPoolExecutor(len(jobs)) as ex:
            results = list(ex.map(lambda j: probe_one(*j), jobs))
        for (r, band), (n, dec) in sorted(zip(jobs, results), key=lambda x: -x[1][0]):
            log(f"  {n:3d} decodes  {band:5s} {r['url']}  ({r['station'][:40]}, local {local_hour(r['lon']):.1f}h)")
            if n >= 3 and len(chosen) < N_WANTED and (band in unfilled or not unfilled):
                chosen.append(dict(url=r["url"], band=band, freq_khz=BANDS[band], label=r["label"],
                                   station=r["station"], lat=r["lat"], lon=r["lon"],
                                   probe_decodes=n, probe_sample=dec[:5]))
                if band in unfilled:
                    unfilled.remove(band)
        log(f"chosen {len(chosen)}/{N_WANTED}; unfilled bands: {unfilled}")
    json.dump(dict(seed=seed, chosen=chosen), open(os.path.join(ROOT, "selection.json"), "w"), indent=1)
    log("wrote selection.json")


def slug(s):
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")[:40]


def cmd_record():
    sel = json.load(open(os.path.join(ROOT, "selection.json")))["chosen"]
    start = (int(time.time()) // 15 + 1) * 15  # next slot boundary
    until = start + MINUTES * 60 + 1
    log(f"recording {len(sel)} receivers from {time.strftime('%H:%M:%S', time.gmtime(start))}Z for {MINUTES} min")

    def run(i, rx):
        rx["dir"] = os.path.join(OUT, f"{i:02d}_{rx['band']}_{slug(rx['url'].split('//')[1])}")
        slots = os.path.join(rx["dir"], "slots")
        os.makedirs(slots, exist_ok=True)
        json.dump(rx, open(os.path.join(rx["dir"], "meta.json"), "w"), indent=1)
        files = []
        attempts = 0
        while time.time() < until and attempts < 5:
            attempts += 1
            try:
                files += kiwi.record_slots(rx["url"], rx["freq_khz"], slots, until,
                                           log=lambda *a: log(*a))
            except Exception as e:
                log(f"[{rx['url']}] {e!r}; retry in 20s")
                time.sleep(20)
        log(f"[{i:02d} {rx['band']}] done: {len(os.listdir(slots))} slots")

    with cf.ThreadPoolExecutor(len(sel)) as ex:
        list(ex.map(lambda a: run(*a), enumerate(sel)))


def cmd_decode():
    dirs = sorted(d for d in os.listdir(OUT) if os.path.isdir(os.path.join(OUT, d)))
    for d in dirs:
        base = os.path.join(OUT, d)
        slots = sorted(f for f in os.listdir(os.path.join(base, "slots")) if f.endswith(".wav"))
        paths = [os.path.join(base, "slots", f) for f in slots]
        with cf.ThreadPoolExecutor(8) as ex:
            decs = list(ex.map(jt9_decode, paths))
        with open(os.path.join(base, "decodes.txt"), "w") as fh:
            for f, dec in zip(slots, decs):
                for l in dec:
                    fh.write(f"{f[:-4]} {l}\n")
        # 30-minute continuous WAV, zero-filled where a slot is missing
        if slots:
            t = lambda f: datetime.strptime(f[:13], "%y%m%d_%H%M%S").replace(tzinfo=timezone.utc).timestamp()
            t0 = t(slots[0])
            nslots = int((t(slots[-1]) - t0) // 15) + 1
            have = {int((t(f) - t0) // 15): p for f, p in zip(slots, paths)}
            silence = b"\0" * (15 * 12000 * 2)
            with wave.open(os.path.join(base, f"{slots[0][:13]}_{d}_30min.wav"), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(12000)
                for k in range(nslots):
                    if k in have:
                        with wave.open(have[k]) as r:
                            w.writeframes(r.readframes(r.getnframes()))
                    else:
                        w.writeframes(silence)
        n = sum(map(len, decs))
        withdec = sum(1 for x in decs if x)
        print(f"{d:45s} slots={len(slots):3d}  slots_with_decodes={withdec:3d}  decodes={n}")


if __name__ == "__main__":
    {"probe": cmd_probe, "record": cmd_record, "decode": cmd_decode}[sys.argv[1]]()
