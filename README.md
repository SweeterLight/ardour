# Ardour for Windows ARM64 (unofficial)

This fork builds a portable, native ARM64 version of [Ardour](https://ardour.org/)
for Windows 11 on ARM. It uses a workspace-local
[MSYS2 CLANGARM64](https://www.msys2.org/docs/arm64/) toolchain and packages
the application with its required runtime files. This is an independent build,
not affiliated with or endorsed by ardour.org, and it is not an installer.

The repository contains **source and build tooling**. The generated application
and ZIP are local build outputs; publishing this repository does not publish a
binary release.

## Included features

- Ardour GUI, audio engine, bundled effects, translations, MIDI and control
  surfaces, and LV2, VST2, and VST3 hosting.
- PortAudio with WASAPI and ASIO support, plus JACK and the dummy audio backend.
- Native video helpers for import, playback, transcoding, and media probing.
- ARM64 NEON DSP where available, with generic implementations elsewhere.

The build excludes separately supplied commercial bundles. In-process plugins
and ASIO components need ARM64 versions; this build does not host x64 plugins.

## Build on Windows 11 ARM64

Use a **full Git clone with version tags**, not a GitHub-generated source archive:
Ardour derives its version from Git history. From the repository root, run in
Windows PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\windows-arm64\build.ps1
```

The script provisions MSYS2 and dependencies under `.build/windows-arm64`,
builds Ardour, runs the native tests, packages it, and validates the portable
ZIP. It does not require an existing MSYS2 installation. The MSYS shell and
some bootstrap tools may run through Windows x64 emulation; the Ardour binaries
and compiler target ARM64. Use `-Jobs 4` to change the default of two compiler
jobs. For individual stages, dependency details, and troubleshooting, see the
[Windows ARM64 build guide](tools/windows-arm64/README.md).

## Portable output

The full build produces:

| Path | Purpose |
| --- | --- |
| `dist/Ardour-arm64/bin/Ardour.exe` | Application executable |
| `dist/Ardour-arm64.zip` | Portable folder as a ZIP |
| `dist/Ardour-arm64/build-manifest.json` | Dependency versions, features, and validation record |
| `.build/windows-arm64/logs/` | Logs for each build stage |

Extract the entire ZIP and launch `bin\Ardour.exe`. Keep the `bin`, `lib`,
`share`, and `etc` directories together. The package is designed to run without
MSYS2 installed. `dist/` and `.build/` are ignored by Git, so they are absent
from a source-only GitHub push.

## Validation and current limits

The Windows ARM64 validation run passed 26 native test commands covering DSP,
threading, MIDI, audio, video, JACK, plugin scanners, and test VST2/VST3
plugins. Session tests covered creation, recording and playback with the dummy
backend, a bundled LV2 effect, save and reopen, and non-silent WAV export.
Packaging audited 278 ARM64 executables and DLLs and their runtime imports.

The portable GUI opened a test session through the Windows shell with a clean
command path. The ZIP was extracted to a different path containing spaces and
launched again; bundled audio, video, and JACK checks also passed there. The
results are recorded in the generated manifest and
`dist/Ardour-arm64/portable-validation.json`.

Physical audio and MIDI interfaces have not been exercised. ASIO support was
built and linked, but an ARM64-compatible ASIO interface was unavailable for
hardware testing. These checks do not establish performance or latency on a
particular device.

## License and redistribution

Ardour is licensed under the GNU GPL version 2 or later; see [COPYING](COPYING).
Retain the original copyright and license notices. Bundled dependencies have
their own licenses, recorded under `share/licenses` in the portable package.

Before publishing a binary ZIP, review the licenses of every shipped component
and make the complete corresponding source for that exact build available as
required. The ASIO-enabled PortAudio build uses Steinberg's ASIO SDK, whose
license offers GPL version 3 or separate proprietary terms; its compatibility
and source-distribution obligations require review before a public binary
release. The existing package and manifest do not constitute a completed
distribution-license audit.

Ardour's [packager guidance](PACKAGER_README) asks that VST-enabled builds not
be distributed under the plain `ardour` package name. Choose a distinct release
name such as `ArdourVST-arm64` if publishing a ZIP. The local output paths above
remain unchanged.

For Ardour user documentation and official releases, visit
[ardour.org](https://ardour.org/). Upstream build information is in Ardour's
[development documentation](https://ardour.org/development.html).
