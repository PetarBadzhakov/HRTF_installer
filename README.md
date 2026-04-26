# Restore the "True" Sound of Classic PC Games

If you are playing a game from the DirectSound era (late '90s to mid-2000s) on modern Windows, **it probably sounds flat and lifeless.** Ever since Windows Vista, Microsoft stripped out the hardware audio layer these games relied on. This means legendary titles are forced into a generic "flat" stereo mode, missing the precise 3D positioning and immersive environmental echoes (EAX) that made them sound amazing back in the day.

**This tool is a "one-and-done" fix that restores:**
* **Full EAX Support:** Re-enables all hardware-accelerated effects (reverb, occlusion, and echoes).
* **True 3D Spatial Audio:** Uses modern HRTF to let you hear exactly where a sound is coming from (optimized for headphones).
* **Original Sound Quality:** Bypasses modern Windows limitations to bring back the intended audio engine of the 2000s.

# deploy_hrtf

Sets up DSOAL + OpenAL Soft + SADIE KU100 HRTF for legacy DirectSound games on Windows - DS3D, EAX 1 through 4, anything that goes through `dsound.dll`. NFS Undercover is what I wrote it for, but it works on the rest of that era.

Headphones only. The `alsoft.ini` it writes configures OpenAL Soft for stereo HRTF output; on speakers it would sound wrong.

Default HRTF profile is SADIE KU100 at 48 kHz - generally rated the best general-purpose dataset. To swap, drop a different `.mhr` into `%APPDATA%\openal\hrtf\` and edit `default-hrtf =` in the deployed `alsoft.ini`.

## Disable any OS-level spatial audio first

Windows Sonic, Dolby Atmos for Headphones, DTS Headphone:X, Razer Surround - turn them all off. OpenAL Soft is already doing the HRTF rendering; stacking another HRTF pass on top of it smears the output and wrecks positional cues.

Right-click the speaker tray icon → Spatial sound → Off.

## Usage

```
python deploy_hrtf.py "C:\Games\NFS Undercover"
python deploy_hrtf.py "C:\Games\NFS Undercover" --exe-hint nfs
```

If there's a single `.exe` in the folder it's used directly. If there are several (game + launcher + crash reporter), the largest one wins by default - pass `--exe-hint <substring>` to override.

## What it does

1. Downloads the SADIE HRTF pack and installs `sadie_ku100_stereo_48000_dataset.mhr` to `%APPDATA%\openal\hrtf\`.
2. Downloads the newest OpenAL Soft Windows binary from `kcat/openal-soft`.
3. Downloads the newest DSOAL build from `kcat/dsoal`.
4. Reads the game `.exe`'s PE header to determine 32-bit vs 64-bit.
5. Deploys the matching set of DLLs (`dsound.dll`, `dsoal-aldrv.dll`, etc.) plus a patched `alsoft.ini` next to the `.exe`.

Stdlib only, no `pip install` needed.

## Caveat - upstream packaging

The release-asset filenames on `kcat/openal-soft` and `kcat/dsoal` aren't stable. They've already shifted once each during the time I've been using this: `openal-soft-1.25.1-bin.zip` → `OpenALSoft+HRTF.zip`, and a flat `DSOAL.zip` → an outer wrapper around `DSOAL_rNNN.zip` where the build number changes every CI run. The script handles both layouts, but if either project changes packaging again the lambdas in `main()` will need tweaking.
