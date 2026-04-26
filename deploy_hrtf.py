#!/usr/bin/env python3
# Sets up DSOAL + OpenAL Soft + a SADIE HRTF preset for a legacy DirectSound
# game on Windows. Originally written for NFS Undercover but works for anything
# that goes through dsound.dll.
#
# Usage: python deploy_hrtf.py "C:\Games\NFS Undercover"

import argparse
import json
import os
import shutil
import struct
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path


ALSOFT_INI = """[general]
channels = stereo
stereo-mode = headphones
stereo-encoding = hrtf
hrtf-mode = full
frequency = 48000
resampler = bsinc24
default-hrtf = sadie_ku100_stereo_48000_dataset
hrtf-paths =
[reverb]
boost = 0
"""

# The SADIE HRTF pack lives as a stable file attachment under PenguinDOOM's
# 1.21.1 release tag. URL doesn't change across newer tags of that repo.
SADIE_URL = ("https://github.com/PenguinDOOM/Compiled-HRTF-for-OpenAL-Soft/"
             "files/6212099/Compiled_SADIE_HRTF.zip")
SADIE_FILE = "sadie_ku100_stereo_48000_dataset.mhr"

OAS_REPO = "kcat/openal-soft"
DSOAL_REPO = "kcat/dsoal"

UA = "deploy-hrtf/1.0"

# Machine values from the PE/COFF spec.
PE_BITS = {
    0x014C: 32,  # i386
    0x8664: 64,  # amd64
    0xAA64: 64,  # arm64 - same DLL slot in Windows so treat as 64
    0x01C4: 32,  # armnt
}


def gh_json(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def download(url, dest):
    print(f"  > {url}")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    print(f"  + {dest.name} ({dest.stat().st_size:,} bytes)")


def pick_asset(repo, predicates, label):
    """
    Walk releases newest-first (GH returns them sorted by published_at desc)
    and within each release try predicates in priority order. First hit wins.
    Returns (tag, name, url).
    """
    api = f"https://api.github.com/repos/{repo}/releases?per_page=30"
    try:
        releases = gh_json(api)
    except urllib.error.HTTPError as e:
        sys.exit(f"GitHub API error fetching {repo} releases: {e}")
    except urllib.error.URLError as e:
        sys.exit(f"Network error fetching {repo} releases: {e}")
    if not isinstance(releases, list) or not releases:
        sys.exit(f"No releases found for {repo}")

    for rel in releases:
        assets = rel.get("assets") or []
        for pred in predicates:
            for a in assets:
                name = a.get("name", "")
                if pred(name):
                    return rel.get("tag_name", "?"), name, a["browser_download_url"]
    sys.exit(f"No matching asset ({label}) in any release of {repo}")


def unzip(zip_path, dest):
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)
    print(f"  + extracted {zip_path.name}")


def unzip_nested(zip_path, dest, max_depth=3):
    # Current DSOAL release is a zip-of-a-zip: outer DSOAL.zip wraps
    # DSOAL_rNNN.zip where NNN is the build number (changes every CI run, do
    # not hardcode it). Unwrap whatever .zip turns up at the destination root.
    unzip(zip_path, dest)
    for _ in range(max_depth):
        inners = [p for p in dest.glob("*.zip") if p.is_file()]
        if not inners:
            return
        for p in inners:
            print(f"  + unwrapping {p.name}")
            unzip(p, dest)
            p.unlink()


def find_oas_arch(root, arch):
    # OpenAL Soft binary layout has changed between releases:
    #   new (OpenALSoft+HRTF.zip): <root>/Win32/OpenAL32.dll
    #   old (openal-soft-X.Y.Z-bin.zip): two Win32 dirs - bin/Win32/soft_oal.dll
    #     (the actual implementation) and router/Win32/OpenAL32.dll (registry
    #     shim). We want the bin/ one in the legacy case.
    arch_lc = arch.lower()
    soft, any_dll = [], []
    for p in root.rglob("*"):
        if not p.is_dir() or p.name.lower() != arch_lc:
            continue
        files = [c for c in p.iterdir() if c.is_file()]
        if any(c.name.lower() == "soft_oal.dll" for c in files):
            soft.append(p)
        if any(c.suffix.lower() == ".dll" for c in files):
            any_dll.append(p)
    if soft:
        return soft[0]
    return any_dll[0] if any_dll else None


def find_dsoal_arch(root, arch):
    # After the inner zip unwraps, both DSOAL/Win32 and DSOAL+HRTF/Win32 are
    # there. Prefer plain DSOAL/ - we use SADIE so the bundled IRCAM presets
    # under +HRTF/ would just be dead weight in the game folder.
    arch_lc = arch.lower()
    matches = [p for p in root.rglob("*")
               if p.is_dir() and p.name.lower() == arch_lc and (p / "dsound.dll").exists()]
    if not matches:
        return None
    for m in matches:
        if m.parent.name.lower() == "dsoal":
            return m
    return matches[0]


def copy_into(src, dst):
    """Copy contents of src into dst, overwriting silently. Returns count."""
    n = 0
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            n += copy_into(item, target)
        else:
            shutil.copy2(item, target)
            n += 1
    return n


def exe_bits(path):
    """Read the PE header and return 32 or 64."""
    with open(path, "rb") as f:
        dos = f.read(0x40)
        if len(dos) < 0x40 or dos[:2] != b"MZ":
            raise ValueError(f"{path.name}: not a PE file (no MZ)")
        pe_off = struct.unpack_from("<I", dos, 0x3C)[0]
        f.seek(pe_off)
        if f.read(4) != b"PE\x00\x00":
            raise ValueError(f"{path.name}: not a PE file (no PE signature)")
        machine = struct.unpack("<H", f.read(2))[0]
    if machine in PE_BITS:
        return PE_BITS[machine]
    raise ValueError(f"{path.name}: unrecognised PE machine 0x{machine:04x}")


def pick_exe(game_dir, hint):
    exes = sorted(game_dir.glob("*.exe"))
    if not exes:
        raise FileNotFoundError(f"No .exe files in {game_dir}")
    if hint:
        h = hint.lower()
        for e in exes:
            if h in e.name.lower():
                return e
        raise FileNotFoundError(
            f"No .exe in {game_dir} matched hint '{hint}'. "
            f"Found: {', '.join(p.name for p in exes)}"
        )
    if len(exes) == 1:
        return exes[0]
    # Largest .exe is almost always the actual game (vs launchers, updaters,
    # crash reporters which tend to be tiny).
    return max(exes, key=lambda p: p.stat().st_size)


def main():
    ap = argparse.ArgumentParser(
        prog="deploy_hrtf",
        description="Deploy DSOAL + OpenAL Soft + SADIE KU100 HRTF to a "
                    "legacy DirectSound game for headphone HRTF audio.",
        epilog=('Example:\n'
                '  python deploy_hrtf.py "C:\\Games\\NFS Undercover"\n'
                '  python deploy_hrtf.py "C:\\Games\\NFS Undercover" --exe-hint nfs'),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("game_dir", type=Path, help="Game directory containing the main .exe")
    ap.add_argument("--exe-hint", default=None,
                    help="Substring to disambiguate the game .exe (case-insensitive)")
    args = ap.parse_args()

    game_dir = args.game_dir.resolve()
    if not game_dir.is_dir():
        sys.exit(f"Error: {game_dir} is not a directory")

    # Detect arch up front so we fail fast on a bad path before downloading.
    try:
        exe = pick_exe(game_dir, args.exe_hint)
        bits = exe_bits(exe)
    except (FileNotFoundError, ValueError) as e:
        sys.exit(f"Error: {e}")
    arch = "Win32" if bits == 32 else "Win64"
    print(f"Target: {exe.name}  ({bits}-bit, will use {arch})")

    appdata = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    hrtf_dir = appdata / "openal" / "hrtf"

    # Asset filename predicates. OpenAL Soft has three flavours we accept;
    # tried in priority order within each release.
    is_oas_hrtf = lambda n: n.lower() in ("openalsoft+hrtf.zip", "openal-soft+hrtf.zip")
    is_oas_plain = lambda n: n.lower() in ("openalsoft.zip", "openal-soft.zip")
    is_oas_legacy = lambda n: n.lower().endswith(".zip") and "openal-soft" in n.lower() and "bin" in n.lower()
    # DSOAL: case-insensitive DSOAL.zip, reject .7z (no 7zip dependency) and
    # the +HRTF variant (we ship our own).
    is_dsoal = lambda n: n.lower() == "dsoal.zip"

    with tempfile.TemporaryDirectory(prefix="hrtf_deploy_") as tdstr:
        td = Path(tdstr)
        print(f"Working in: {td}")

        print("\n[1/6] Fetching SADIE HRTF pack")
        sadie_zip = td / "Compiled_SADIE_HRTF.zip"
        download(SADIE_URL, sadie_zip)
        sadie_dir = td / "sadie"
        unzip(sadie_zip, sadie_dir)

        print(f"\n[2/6] Installing {SADIE_FILE} to %APPDATA%/openal/hrtf")
        match = next((p for p in sadie_dir.rglob("*")
                      if p.is_file() and p.name.lower() == SADIE_FILE.lower()), None)
        if match is None:
            sys.exit(f"Error: {SADIE_FILE} not found inside the SADIE archive")
        hrtf_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(match, hrtf_dir / SADIE_FILE)
        print(f"  + installed {hrtf_dir / SADIE_FILE}")

        print(f"\n[3/6] Resolving newest OpenAL Soft binary on {OAS_REPO}")
        tag, name, url = pick_asset(
            OAS_REPO, [is_oas_hrtf, is_oas_plain, is_oas_legacy],
            "OpenALSoft+HRTF.zip / OpenALSoft.zip / openal-soft-*-bin.zip",
        )
        print(f"  + {tag} -> {name}")
        oas_zip = td / name
        download(url, oas_zip)
        oas_dir = td / "openal-soft"
        unzip(oas_zip, oas_dir)
        oas32 = find_oas_arch(oas_dir, "Win32")
        oas64 = find_oas_arch(oas_dir, "Win64")
        if not oas32 or not oas64:
            sys.exit("Error: couldn't find Win32/Win64 inside OpenAL Soft archive")
        print(f"  + OpenAL Soft Win32: {oas32.relative_to(td)}")
        print(f"  + OpenAL Soft Win64: {oas64.relative_to(td)}")

        print(f"\n[4/6] Resolving newest DSOAL on {DSOAL_REPO}")
        tag, name, url = pick_asset(DSOAL_REPO, [is_dsoal], "DSOAL.zip")
        print(f"  + {tag} -> {name}")
        dsoal_zip = td / name
        download(url, dsoal_zip)
        dsoal_dir = td / "dsoal"
        unzip_nested(dsoal_zip, dsoal_dir)
        ds32 = find_dsoal_arch(dsoal_dir, "Win32")
        ds64 = find_dsoal_arch(dsoal_dir, "Win64")
        if not ds32 or not ds64:
            sys.exit("Error: couldn't find Win32/Win64 inside DSOAL archive")
        print(f"  + DSOAL Win32: {ds32.relative_to(td)}")
        print(f"  + DSOAL Win64: {ds64.relative_to(td)}")

        print("\n[5/6] Patching alsoft.ini and merging DSOAL into OpenAL Soft")
        for d in (ds32, ds64):
            (d / "alsoft.ini").write_text(ALSOFT_INI, encoding="utf-8")
            print(f"  + patched {d.relative_to(td)}/alsoft.ini")
        n32 = copy_into(ds32, oas32)
        n64 = copy_into(ds64, oas64)
        print(f"  + merged {n32} files into OpenAL Soft Win32, {n64} into Win64")

        print(f"\n[6/6] Deploying {bits}-bit DLLs next to {exe.name}")
        chosen = oas32 if bits == 32 else oas64
        copied = copy_into(chosen, game_dir)
        print(f"  + copied {copied} files to {game_dir}")

    print()
    print("Done.")
    print(f"  Game directory:   {game_dir}")
    print(f"  HRTF dataset:     {hrtf_dir / SADIE_FILE}")
    print(f"  Architecture:     {bits}-bit ({arch})")
    print()
    print("Launch the game. If audio is silent or wrong, check that the in-game")
    print("audio setting is on Surround (or whatever maps to DirectSound).")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
