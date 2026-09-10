"""What this process and this container are actually using, read from the kernel.

⚠️ **Three numbers, never one, and that is the whole point of this module.**
   2026-08-28 cost four rounds of investigation because the only signal was a
   single figure on a dashboard, and the same system was reporting three
   different ones that mean three different things (revisions.md 五十一):

     · the platform's chart      one line **per instance**, and the retired
                                 instance's line stays on the graph looking
                                 like the current value;
     · a sum of process RSS      **counts shared pages more than once** —
                                 gunicorn's workers are forked, so the master's
                                 pages are resident in all three;
     · the cgroup's own total    honest, but includes reclaimable page cache,
                                 and is meaningless taken while the instance is
                                 still cold.

   A probe that answered "memory: 477MB" would reproduce that whole episode.
   This one answers with `anon`, `file` and this process's RSS separately, so
   the three questions that matter — is it ours, is it reclaimable, is it
   growing — can each be asked.

⚠️ Nothing is imported that is not in the standard library, and no Django. The
   kernel already knows all of this; a measurement dependency would be a new
   package on the deploy for numbers that are free.

⚠️ Every reader returns None rather than raising when the file is not there.
   None of these paths exist on macOS, so a probe that threw would fail on
   every development machine — and a probe that fails on a laptop gets deleted
   rather than fixed.

⚠️ cgroup **v2 only** (`/sys/fs/cgroup/memory.stat` with `anon`/`file` keys),
   which is what Render runs — verified against a live instance rather than
   assumed. On v1 the files are named differently and this reports nothing,
   which is the honest answer for a reader that has not been tested there.
"""

import os

PROC_STATUS = "/proc/self/status"
CGROUP_STAT = "/sys/fs/cgroup/memory.stat"
CGROUP_CURRENT = "/sys/fs/cgroup/memory.current"
CGROUP_MAX = "/sys/fs/cgroup/memory.max"

#: Which of memory.stat's several dozen keys are worth a log line. `anon` is
#: the one that matters — it is what cannot be reclaimed and therefore what
#: actually runs the container out of memory; `file` sits beside it precisely so
#: that a high total can be recognised as cache rather than mistaken for anon.
STAT_KEYS = ("anon", "file", "slab")


def _read(path):
    """The file's text, or None if it is not there or cannot be read."""
    try:
        with open(path, encoding="ascii") as handle:
            return handle.read()
    except OSError:
        return None


def process_rss_bytes(status_text=None):
    """This process's resident set size.

    ⚠️ Useful for "is *this* worker growing", and for nothing else. Summing it
       across processes is the mistake described at the top of this module.
    """
    text = status_text if status_text is not None else _read(PROC_STATUS)
    if text is None:
        return None
    for line in text.splitlines():
        if line.startswith("VmRSS:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                # /proc reports kB, and says so in the third field. Converted
                # here so that every number this module hands out is bytes.
                return int(parts[1]) * 1024
    return None


def cgroup_stat(stat_text=None):
    """The `anon` / `file` / `slab` breakdown, as bytes. Empty when unavailable."""
    text = stat_text if stat_text is not None else _read(CGROUP_STAT)
    if text is None:
        return {}
    wanted = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] in STAT_KEYS and parts[1].isdigit():
            wanted[parts[0]] = int(parts[1])
    return wanted


def _cgroup_number(path):
    text = _read(path)
    if text is None:
        return None
    value = text.strip()
    # ⚠️ memory.max reads "max" when no limit is set. Not an error, and not
    #    zero either — returning 0 would make every percentage infinite.
    return int(value) if value.isdigit() else None


def snapshot():
    """One reading: {name: bytes}, leaving out whatever this platform lacks."""
    reading = {}
    rss = process_rss_bytes()
    if rss is not None:
        reading["rss"] = rss
    reading.update(cgroup_stat())
    for name, path in (("total", CGROUP_CURRENT), ("limit", CGROUP_MAX)):
        value = _cgroup_number(path)
        if value is not None:
            reading[name] = value
    return reading


def format_snapshot(reading=None, *, pid=None):
    """One log line: MB apiece, plus the percentage of the limit if there is one.

    ⚠️ The pid is on the line because the answer to "which worker is growing"
       is not derivable from anything else in it, and that question is the one
       that identified the per-thread ratchet: the master's figure did not move
       at all while the two workers moved by different amounts.
    """
    reading = snapshot() if reading is None else reading
    if not reading:
        return "[memory] unavailable on this platform"
    mb = {name: value / (1024 * 1024) for name, value in reading.items()}
    parts = [f"pid={pid if pid is not None else os.getpid()}"]
    parts += [f"{name}={mb[name]:.1f}MB" for name in
              ("rss", "anon", "file", "slab", "total", "limit") if name in mb]
    if "total" in mb and mb.get("limit"):
        parts.append(f"pct={mb['total'] / mb['limit'] * 100:.0f}%")
    return "[memory] " + " ".join(parts)
