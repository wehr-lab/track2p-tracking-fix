"""
machine_config.py

Single source of truth for GIT_CLONE_PATH -- the local filesystem path to
the patched track2p git clone (the fork with the diagnostics/gap-tolerant
patches: npy_to_s2p, save_match_diagnostics, etc.) -- instead of the same
literal path copy-pasted into every launcher/tool script
(run_gap_tolerant.py, run_exclude_session.py, debug_large_displacement.py,
inspect_registration_pair.py, registration_quality_scan.py all import
GIT_CLONE_PATH from here now).

Why this exists: this repo runs on multiple machines (Mike's Mac, the
'talapas' HPC cluster, possibly more later) that share the same git
history but have the patched track2p clone at DIFFERENT paths. A plain
tracked variable can't hold that -- `git pull` on one machine would
clobber the value another machine needs. So GIT_CLONE_PATH instead lives
in `local_machine.cfg`, a small INI file that is gitignored (see
.gitignore) -- each machine keeps its own copy forever, untouched by
git pull/push. `local_machine.cfg.example` (tracked) documents the format.

One-time setup on a NEW machine:
    cp local_machine.cfg.example local_machine.cfg
    # edit local_machine.cfg's git_clone_path to point at THIS machine's
    # patched track2p clone. Confirm with (after activating the track2p
    # conda env):
    #     python -c "import track2p; print(track2p.__file__)"
    # If that path is inside a conda_envs/.../site-packages folder rather
    # than a git clone, that's the stale pip-installed copy -- not what
    # belongs here.

Fails LOUDLY (not silently) if local_machine.cfg is missing, or its path
doesn't actually contain an importable track2p package. The previous
hardcoded-per-file design failed silently instead: a wrong/nonexistent
GIT_CLONE_PATH just made `sys.path.insert` a no-op, so Python quietly fell
back to whichever track2p happened to be on sys.path already (often a
stale pip/conda-installed copy missing this fork's patches) -- exactly the
ImportError this file exists to prevent happening again.
"""

import configparser
import os

_CFG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'local_machine.cfg')

if not os.path.exists(_CFG_PATH):
    raise FileNotFoundError(
        f"local_machine.cfg not found at {_CFG_PATH}.\n"
        f"This is a per-machine, gitignored settings file -- each machine (Mac, "
        f"talapas, any future one) needs its own copy, since the patched track2p "
        f"clone lives at a different path on each. One-time setup:\n"
        f"    cp local_machine.cfg.example local_machine.cfg\n"
        f"then edit git_clone_path in local_machine.cfg to point at THIS machine's "
        f"patched track2p clone."
    )

_config = configparser.ConfigParser()
_config.read(_CFG_PATH)

try:
    GIT_CLONE_PATH = _config['paths']['git_clone_path']
except KeyError:
    raise KeyError(
        f"local_machine.cfg at {_CFG_PATH} is missing a [paths] section with "
        f"git_clone_path -- see local_machine.cfg.example for the expected format."
    )

if not os.path.isdir(GIT_CLONE_PATH):
    raise NotADirectoryError(
        f"local_machine.cfg's git_clone_path ({GIT_CLONE_PATH!r}) doesn't exist on "
        f"this machine. Confirm the patched track2p clone's actual location and fix "
        f"local_machine.cfg -- do NOT let this fail silently: an outdated/wrong path "
        f"here previously caused Python to quietly fall back to a stale pip/conda-"
        f"installed track2p missing this fork's patches (npy_to_s2p, "
        f"save_match_diagnostics, etc.), which is exactly the bug this check exists "
        f"to catch early instead."
    )

if not os.path.isdir(os.path.join(GIT_CLONE_PATH, 'track2p')):
    raise NotADirectoryError(
        f"{GIT_CLONE_PATH!r} exists but has no track2p/ subfolder inside it -- "
        f"git_clone_path should be the CLONE ROOT (the directory `git clone` created), "
        f"which contains a track2p/ package folder, not the package folder itself. "
        f"Check local_machine.cfg's git_clone_path."
    )
