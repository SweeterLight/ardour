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
