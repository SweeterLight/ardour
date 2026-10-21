# Native Windows ARM64 build

Run these commands in **Windows PowerShell**, from the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\windows-arm64\build.ps1
```

This creates an isolated MSYS2 installation, installs CLANGARM64 dependencies,
builds additional dependencies from source, compiles Ardour, and packages it.
The compiler and application target ARM64. The MSYS shell and some build tools
can run through Windows 11's x64 emulation. WSL is not used for compilation.

The source must be a Git checkout with version tags. Build files, package caches,
source dependencies, and logs are under `.build/windows-arm64`. The default is
two compiler jobs to limit memory use; specify `-Jobs 4` to change this.

## Resume a build

Each stage can be run separately:

```powershell
.\tools\windows-arm64\build.ps1 -Stage Bootstrap
.\tools\windows-arm64\build.ps1 -Stage Dependencies
.\tools\windows-arm64\build.ps1 -Stage SourceDependencies
.\tools\windows-arm64\build.ps1 -Stage Configure
.\tools\windows-arm64\build.ps1 -Stage Build
.\tools\windows-arm64\build.ps1 -Stage Test
.\tools\windows-arm64\build.ps1 -Stage Package
.\tools\windows-arm64\build.ps1 -Stage Validate
```

`Hardware` is a separate, interactive stage for the connected ZOOM H2n. It is
not part of `All` because it requires microphone input, headphones, and a
3.5 mm loopback cable:

```powershell
.\tools\windows-arm64\build.ps1 -Stage Hardware
```

Set the H2n to stereo **USB Audio I/F**, **44.1 kHz**, and USB **INPUT ON**.
In Windows sound properties, set both H2n input and output endpoint formats to
**2 channels, 16 bit, 44100 Hz**. The hardware stage checks the actual Windows
endpoint formats and stops with the observed values if they do not match. It
records the active WASAPI mix rate and tests shared and exclusive modes. When
Windows keeps the H2n input mix at 48 kHz, the hardware stage uses exclusive
WASAPI at 44.1 kHz if the interface accepts it. Ardour's PortAudio backend
also falls back to exclusive mode when shared WASAPI rejects the selected rate.
Exclusive mode requires other applications to release the H2n endpoints. The
stage then captures a microphone
sound, runs a two-minute duplex WASAPI stream, and opens
an isolated Ardour session for recording, listening, WAV export, and Ardour's
Latency Measurement Tool. Export the GUI test session to the path printed by
the stage using stereo 44.1 kHz, 16-bit PCM WAV.
After selecting the H2n devices, connect the audio track's two input channels
to `system:capture_1` and `system:capture_2` in its Input Routing Grid. Connect
the Master outputs to `system:playback_1` and `system:playback_2`. Ardour saves
physical connections for each selected device; connections made before the H2n
was selected do not activate for that device. The report verifies the active
device routing and requires a fresh recording and export from this GUI run.

For latency measurement, first finish the headphone listening check. Then
connect the H2n headphone/line output to its mic/line input using the 3.5 mm
cable. Set output and input levels low, turn plug-in power and direct monitoring
off, and take three clean latency readings in Ardour. Enter their round-trip
sample counts when prompted. The stage checks that the readings differ by at
most one 1024-frame buffer. Enter `defer` if the cable test is unavailable; the
report will mark latency blocked and preserve the completed checks.

The machine-specific results are written to
`.build/windows-arm64/hardware-h2n/hardware-h2n-report.json` and copied beside
the ZIP as `dist/Ardour-arm64-hardware-validation.json`. The existing portable
folder, ZIP, and build manifest are not rewritten. The stage also inventories
ZOOM's H2n ASIO package and any registered driver without installing one.
ASIO hardware testing runs only if a native ARM64 driver is already installed.

To test a separately compiled backend without rewriting the existing portable
package, copy the portable folder to another path, replace that copy's backend
DLL, and run `-Stage Hardware -HardwarePackage <test-copy-path>`. The report
records the tested path. The default stage tests `dist\Ardour-arm64`.
The H2n-tested copy in this workspace is `dist\Ardour-arm64-h2n-candidate`, with
a separate `dist\Ardour-arm64-h2n-candidate.zip`; the original release ZIP is
unchanged. The accompanying hardware report identifies the tested candidate
and records the deferred latency and ASIO checks.

### H2n validation recorded on 2026-10-22

The connected H2n's Windows input and output endpoint defaults were stereo
44,100 Hz/16-bit PCM. Windows reported a 48 kHz float input mix, so the test
used exclusive WASAPI for the requested 44.1 kHz stream. The 10-second
microphone capture contained 440,320 frames, had a peak of 13,478 on the
16-bit scale, and had no clipped samples. The two-minute, 1024-frame duplex
stream completed 5,164 callbacks with no reported input or output underflows
or overflows.

In the portable Ardour GUI, the H2n microphone and earphone were selected in
PortAudio's Windows WASAPI driver. A saved stereo take contained audio on both
channels after the track's inputs were connected to the active H2n capture
ports. The session reopened and played through H2n headphones. Its fresh
export was 44,100 Hz, stereo, 16-bit PCM and contained audio. The candidate
package passed the required-feature audit with 18 features and 280 ARM64
binaries; the packaging regression suite passed eight tests.

The hardware report's overall status is **blocked**, not passed. The three
loopback round-trip latency readings remain outstanding because the cable test
was unavailable. No native ARM64 H2n ASIO driver was registered, so ASIO
hardware operation remains untested. The detailed evidence is in
`dist/Ardour-arm64-hardware-validation.json`; the candidate ZIP includes its
updated `build-manifest.json`. Both generated files are local artifacts and are
ignored by Git.

For a GUI-only backend correction, `-ResumeHardwareCapture` rechecks an existing
44.1 kHz/16-bit WAV and reuses its passed two-minute stream when the H2n device
settings still match. It does not reuse any GUI or listening result.

To inspect devices, endpoint formats, and ASIO driver status without recording:

```powershell
.\.build\windows-arm64\msys64\clangarm64\bin\python.exe tools\windows-arm64\hardware_h2n.py --package dist\Ardour-arm64 --work .build\windows-arm64 --probe-only
```

`Dependencies` includes `SourceDependencies`; use the latter to retry source
builds without repeating package installation. Existing source checkouts are
reused without fetching a new revision. Their commit IDs and installed package
versions are recorded alongside the package.

The Waf interface is `--dist-target=mingw --windows-arch=arm64`. The compiler's
predefined macros determine the target; a mismatched explicit architecture
fails configuration. Other Windows builds keep automatic target detection.

## Portable output

- `dist/Ardour-arm64/bin/Ardour.exe`
- `dist/Ardour-arm64.zip`

Extract the entire ZIP and launch `bin\Ardour.exe`. Keep the DLLs and the `lib`,
`share`, and `etc` directories together. No MSYS2 installation or installer
registry entries should be needed to run it. Plugins and in-process ASIO
components must support ARM64. Commercial third-party bundles are not included.

## Sharing the source and portable ZIP

This is an unofficial Windows ARM64 build, not an ardour.org release. Ardour is
licensed under the GNU GPL version 2 or later; keep `COPYING`, copyright
notices, and the separate license notices for third-party components intact
when publishing the source fork. The repository ignores `dist/`, so pushing
the source to GitHub does not publish the portable folder or ZIP.

Before publishing the ZIP, provide the complete corresponding source for the
exact build, including its build scripts and the source required by the licenses
of bundled dependencies. Review the license terms of every shipped component.
In particular, this build's ASIO-enabled PortAudio uses Steinberg's ASIO SDK,
whose license offers GPL version 3 or separate proprietary terms. The presence
of license notices and a build manifest in the ZIP does not establish that a
public binary release has completed a license audit.

Ardour's [packager guidance](../../PACKAGER_README) asks distributors not to
use the plain `ardour` package name for VST-enabled builds. Use a distinct
release name such as `ArdourVST-arm64` if publishing this ZIP. The local output
paths above are unchanged.

Packaging fails if a required feature, DLL, or native video helper is missing,
if an executable targets another architecture, or if it imports the MSYS/Cygwin
runtime. It scans ordinary and delayed DLL imports recursively. SDK source
archives stay in the build directory and are excluded from the portable ZIP.

`build-manifest.json` records architecture and dependency audit results. These
checks do not substitute for GUI, audio, plugin, or video functional testing.
ASIO device testing requires an interface with an ARM64-compatible driver.

`Test` runs the native DSP/NEON, threading, timer, MIDI, temporal, and audio
tests. It also records a generated sine wave through the dummy engine, loads a
bundled LV2 compressor, saves and reloads the session, plays it, and checks a
stereo WAV export for nonzero samples. These synthetic tests do not exercise
physical audio or MIDI interfaces. A separate JACK server checks native client
connections and audio callbacks; native video tests decode and render a clip.
Minimal ARM64 VST2 and VST3 effects are compiled as test fixtures, discovered by
the scanners, instantiated in sessions, saved, and reloaded. The VST3 fixture
uses `Contents/arm64-win`; these fixtures are not shipped as user plugins.

`Validate` uses the Windows shell's executable launch operation with a PATH
containing only Windows system directories. It opens the test session, checks
that loaded DLLs come from Windows or the portable folder, extracts the ZIP to
a different path containing spaces, and repeats the launch and bundled audio,
video, and JACK tests.
The resulting `portable-validation.json` and updated manifest are included in
the final ZIP. Test configuration is isolated from your normal Ardour profile.

For libc++ 23 and newer, Ardour uses the standard allocator instead of its
legacy allocator with inline storage. The latter is incompatible with libc++'s
allocator copies during vector growth. This follows the existing heap-backed
Windows/MSVC container approach and is covered by a native regression test;
audio performance and latency still require testing with your chosen devices.

The packaging regression tests can run independently:

```powershell
python tools/windows-arm64/test_package.py
```

See `.build/windows-arm64/logs` for each stage's output. A failed stage leaves
its build products for diagnosis and incremental retry; it does not declare a
successful full-feature package.
