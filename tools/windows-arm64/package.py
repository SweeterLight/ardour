#!/usr/bin/env python3
"""Assemble and audit a relocatable, pure ARM64 Ardour Windows distribution."""

import argparse
import ast
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import struct
import subprocess
import zipfile

ARM64 = 0xAA64
SYSTEM_DLLS = {name + '.dll' for name in '''
advapi32 avicap32 avrt authz bcrypt bcryptprimitives cabinet cfgmgr32 comctl32 comdlg32 crypt32 cryptbase
cryptsp d2d1 d3d9 d3d11 d3d12 dbgcore dbghelp dhcpcsvc dnsapi dwrite dwmapi dxgi
dxva2 gdi32 gdiplus glu32 hid imagehlp imm32 iphlpapi kernel32 ksuser mf mfplat mfreadwrite
mfuuid mpr msacm32 msasn1 msimg32 msvcrt mswsock ncrypt netapi32 normaliz ntdll
ole32 oleacc oleaut32 opengl32 pdh powrprof propsys psapi rpcrt4 runtimeobject
secur32 setupapi shell32 shcore shlwapi sspicli ucrtbase user32 userenv usp10
uxtheme version vfw32 winhttp wininet winmm winmmbase wintrust winusb wldap32
ws2_32 wsock32 wtsapi32 xmllite
'''.split()} | {'winspool.drv'}


def system_dll(name):
    return name in SYSTEM_DLLS or name.startswith(('api-ms-win-', 'ext-ms-win-'))


def pe_info(path):
    """Read machine and normal/delay DLL imports without executing the image."""
    data = Path(path).read_bytes()
    if len(data) < 64 or data[:2] != b'MZ':
        raise ValueError(f'{path}: not a PE executable')
    pe = struct.unpack_from('<I', data, 60)[0]
    if data[pe:pe + 4] != b'PE\0\0':
        raise ValueError(f'{path}: invalid PE header')
    machine, sections = struct.unpack_from('<HH', data, pe + 4)
    optional_size = struct.unpack_from('<H', data, pe + 20)[0]
    optional = pe + 24
    magic = struct.unpack_from('<H', data, optional)[0]
    if magic == 0x20B:
        image_base = struct.unpack_from('<Q', data, optional + 24)[0]
        directory, count_offset = optional + 112, optional + 108
    elif magic == 0x10B:
        image_base = struct.unpack_from('<I', data, optional + 28)[0]
        directory, count_offset = optional + 96, optional + 92
    else:
        raise ValueError(f'{path}: unsupported PE optional header')
    count = struct.unpack_from('<I', data, count_offset)[0]
    headers_size = struct.unpack_from('<I', data, optional + 60)[0]
    table = optional + optional_size

    def offset(rva):
        if rva < headers_size:
            return rva
        for i in range(sections):
            virtual_size, virtual, raw_size, raw = struct.unpack_from('<IIII', data, table + 40 * i + 8)
            if virtual <= rva < virtual + max(virtual_size, raw_size):
                result = raw + rva - virtual
                if result < len(data):
                    return result
        raise ValueError(f'{path}: invalid PE address 0x{rva:x}')

    def dll_name(rva):
        start = offset(rva)
        end = data.find(b'\0', start, start + 1024)
        if end < 0:
            raise ValueError(f'{path}: unterminated DLL import')
        name = data[start:end].decode('ascii').lower()
        if not name or '/' in name or '\\' in name:
            raise ValueError(f'{path}: invalid DLL import {name!r}')
        return name

    imports = set()
    for index, size in ((1, 20), (13, 32)):
        if index >= count:
            continue
        rva, length = struct.unpack_from('<II', data, directory + index * 8)
        if not rva or not length:
            continue
        for pos in range(0, length, size):
            descriptor = struct.unpack_from('<' + 'I' * (size // 4), data, offset(rva + pos))
            if not any(descriptor):
                break
            address = descriptor[3] if index == 1 else descriptor[1]
            if index == 13 and not descriptor[0] & 1:
                address -= image_base
            imports.add(dll_name(address))
    return machine, sorted(imports)


def images(root):
    return sorted(p for p in root.rglob('*') if p.is_file() and p.suffix.lower() in ('.exe', '.dll', '.vst3'))


def require_features(root):
    required = {
        'GUI': 'bin/Ardour.exe',
        'VST2 scanner': 'bin/ardour-vst-scanner.exe',
        'VST3 scanner': 'bin/ardour-vst3-scanner.exe',
        'PortAudio backend': 'lib/ardour*/backends/*portaudio*.dll',
        'JACK backend': 'lib/ardour*/backends/*jack*.dll',
        'dummy backend': 'lib/ardour*/backends/*dummy*.dll',
        'JACK server drivers': 'lib/jack/jack_dummy.dll',
        'control surfaces': 'lib/ardour*/surfaces/*.dll',
        'bundled LV2 effects': 'lib/ardour*/LV2/*/*.dll',
        'Vamp analysis': 'lib/ardour*/vamp/*.dll',
        'translations': 'share/**/LC_MESSAGES/*.mo',
        'fonts': 'share/ardour*/ArdourMono.ttf',
        'LV2 native UI bridge': 'lib/ardour*/suil/*win_in_gtk2.dll',
        'GTK theme': 'lib/gtk-2.0/engines/libclearlooks.dll',
        'video server': 'bin/harvid.exe',
        'video monitor': 'bin/xjadeo.exe',
        'transcoder': 'bin/ffmpeg.exe',
        'media probe': 'bin/ffprobe.exe',
    }
    missing = [name for name, pattern in required.items() if not any(root.glob(pattern))]
    if missing:
        raise ValueError('Required features missing: ' + ', '.join(missing))
    return list(required)


def validate_config(work):
    """Check actual Waf settings before accepting a full-feature package."""
    cache = {}
    for line in (work / 'build/c4che/_cache.py').read_text(encoding='utf-8').splitlines():
        key, separator, value = line.partition(' = ')
        if separator and key.isidentifier():
            try:
                cache[key] = ast.literal_eval(value)
            except (ValueError, TypeError, SyntaxError):
                pass
    if cache.get('WINDOWS_ARCH') != 'arm64':
        raise ValueError('Waf was not configured for Windows ARM64')
    missing_backends = {'portaudio', 'jack', 'dummy'} - set(cache.get('BACKENDS', []))
    if missing_backends:
        raise ValueError('Required backends missing: ' + ', '.join(sorted(missing_backends)))
    required = {'LV2_SUPPORT', 'WINDOWS_VST_SUPPORT', 'VST3_SUPPORT', 'WITH_ASIO',
                'ENABLE_NLS', 'HAVE_LRDF', 'HAVE_SUIL', 'HAVE_USB', 'HAVE_WEBSOCKETS', 'PTFORMAT'}
    defined = {entry[:-2] for entry in cache.get('DEFINES', []) if entry.endswith('=1')}
    if required - defined:
        raise ValueError('Required build options missing: ' + ', '.join(sorted(required - defined)))
    return sorted(required)


def audit(root):
    features = require_features(root)
    available = {p.name.lower() for p in (root / 'bin').glob('*.dll')}
    result = []
    for path in images(root):
        machine, imports = pe_info(path)
        if machine != ARM64:
            raise ValueError(f'{path}: expected ARM64 (0xAA64), found 0x{machine:04x}')
        local = {p.name.lower() for p in path.parent.glob('*.dll')}
        for name in imports:
            if name.startswith(('msys-', 'cygwin')):
                raise ValueError(f'{path}: forbidden MSYS/Cygwin runtime import {name}')
            if not system_dll(name) and name not in available | local:
                raise ValueError(f'{path}: missing DLL dependency {name}')
        result.append({'path': path.relative_to(root).as_posix(), 'machine': 'ARM64',
                       'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'imports': imports})
    return features, result


def write_archive(output):
    archive = output.with_suffix('.zip')
    temporary = archive.with_suffix('.zip.tmp')
    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
        for path in sorted(output.rglob('*')):
            if path.is_file():
                zipped.write(path, Path(output.name) / path.relative_to(output))
    temporary.replace(archive)
    return archive


def finalize_validation(output, work):
    features, binaries = audit(output)
    manifest_path = output / 'build-manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest['binaries'] != binaries:
        raise ValueError('Portable binaries changed since the package manifest was written')
    results = json.loads((work / 'portable-validation.json').read_text(encoding='utf-8'))
    required = {'shell-launch', 'relocated-shell-launch', 'bundled-video', 'bundled-jack', 'bundled-audio-apis'}
    if any(results.get(name, {}).get('status') != 'passed' for name in required):
        raise ValueError('Portable runtime validation has not passed all required checks')
    shutil.copy2(work / 'portable-validation.json', output / 'portable-validation.json')
    manifest['validation']['portable_runtime'] = {name: 'passed' for name in sorted(required)}
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    archive = write_archive(output)
    print(f'Validated {len(features)} required features, {len(binaries)} ARM64 binaries, and portable runtime.\nArchive: {archive}')


def bundle(stage, stack, runtime, work, output):
    configured_features = validate_config(work)
    if not stage.is_dir():
        raise ValueError(f'Install staging directory is missing: {stage}')
    # Construct a fresh candidate. A failed audit never replaces a prior package.
    candidate = output.with_name(output.name + '.candidate')
    if candidate.exists():
        shutil.rmtree(candidate)
    shutil.copytree(stage, candidate)
    bindir = candidate / 'bin'
    bindir.mkdir(exist_ok=True)
    mains = list(candidate.glob('lib/ardour*/ardour-[0-9]*.exe'))
    if len(mains) != 1:
        raise ValueError(f'Expected one Ardour GUI executable, found {mains}')
    shutil.copy2(mains[0], bindir / 'Ardour.exe')
    mains[0].unlink()
    for path in list(candidate.glob('lib/ardour*/*.dll')):
        shutil.copy2(path, bindir / path.name)
    for modules in candidate.glob('lib/ardour[0-9]*'):
        for name in ('ardour-vst-scanner.exe', 'ardour-vst3-scanner.exe'):
            scanner = modules / name
            if not scanner.is_file():
                raise ValueError(f'Required scanner missing: {scanner}')
            shutil.copy2(scanner, bindir / name)
        for path in modules.glob('*win_in_gtk2.dll'):
            (modules / 'suil').mkdir(exist_ok=True)
            shutil.copy2(path, modules / 'suil' / path.name)
        # Lilv also needs the LV2 core vocabulary to classify bundled plugins.
        for specification in (runtime / 'lib/lv2').glob('*.lv2'):
            if not any(specification.glob('*.dll')):
                destination = modules / 'LV2' / specification.name
                destination.mkdir(parents=True, exist_ok=True)
                for path in specification.glob('*.ttl'):
                    shutil.copy2(path, destination / path.name)
    for name in ('harvid.exe', 'xjadeo.exe', 'xjremote.exe'):
        shutil.copy2(stack / 'bin' / name, bindir / name)
    shutil.copy2(stack / 'share/xjadeo/ArdourMono.ttf', bindir / 'ArdourMono.ttf')
    for name in ('ffmpeg.exe', 'ffprobe.exe', 'jackd.exe'):
        shutil.copy2(runtime / 'bin' / name, bindir / name)
    shutil.copytree(runtime / 'lib/jack', candidate / 'lib/jack', dirs_exist_ok=True)
    for pattern in ('libjack*.dll', 'jack*.dll'):
        for path in (runtime / 'bin').glob(pattern):
            shutil.copy2(path, bindir / path.name)
    # GTK's theme module name differs from Waf's Windows DLL filename.
    for path in candidate.glob('lib/ardour*/engines/*clearlooks*.dll'):
        engines = candidate / 'lib/gtk-2.0/engines'
        engines.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, engines / 'libclearlooks.dll')
        shutil.copy2(path, engines / 'clearlooks.dll')
    licenses = candidate / 'share/licenses'
    if (runtime / 'share/licenses').is_dir():
        shutil.copytree(runtime / 'share/licenses', licenses, dirs_exist_ok=True)
    for name in ('portaudio', 'lrdf', 'harvid', 'xjadeo'):
        source = work / 'sources' / name
        for pattern in ('COPYING*', 'LICENSE*'):
            for path in source.glob(pattern):
                if path.is_file():
                    (licenses / name).mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, licenses / name / path.name)
    sdk = work / 'dependency-builds/portaudio/ASIOSDK'
    sdk_notices = list(sdk.rglob('LICENSE.txt'))
    if not sdk_notices:
        raise ValueError('ASIO SDK license notices are missing')
    for path in sdk_notices + list(sdk.glob('*Licensing*.pdf')) + list(sdk.glob('*Usage Guidelines*.pdf')):
        target = licenses / 'ASIOSDK' / path.relative_to(sdk)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    repo = Path(__file__).resolve().parents[2]
    notice_name = re.compile(r'^(copying|license|licence|copyright|notice)([.-].*)?$', re.I)
    for subtree in ('libs', 'gtk2_ardour', 'share'):
        for path in (repo / subtree).rglob('*'):
            if path.is_file() and notice_name.match(path.name):
                target = licenses / 'ardour-vendored' / path.relative_to(repo)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
    for name, source in (('Lua', 'libs/lua/lua-5.3.5/lua.h'), ('LuaBridge', 'libs/lua/LuaBridge/LuaBridge.h')):
        blocks = re.findall(r'/\*.*?\*/', (repo / source).read_text(encoding='utf-8'), re.S)
        notice = next((block for block in blocks if 'Permission is hereby granted' in block), None)
        if not notice:
            raise ValueError(f'{name}: embedded license notice could not be found')
        (licenses / (name + '-LICENSE.txt')).write_text(notice + '\n', encoding='utf-8')
    shutil.copy2(repo / 'COPYING', candidate / 'COPYING')
    # Runtime lookup uses this portable fontconfig file instead of its build prefix.
    fonts = candidate / 'etc/fonts'
    fonts.mkdir(parents=True, exist_ok=True)
    (fonts / 'fonts.conf').write_text(
        '<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">\n'
        '<fontconfig><dir>WINDOWSFONTDIR</dir><cachedir prefix="xdg">fontconfig</cachedir>'
        '<config><rescan><int>30</int></rescan></config></fontconfig>\n', encoding='utf-8')
    for cert in (runtime / 'etc/ssl/certs/ca-bundle.crt', runtime / 'ssl/certs/ca-bundle.crt'):
        if cert.exists():
            shutil.copy2(cert, bindir / 'curl-ca-bundle.crt')
            break

    providers = {}
    for directory in (stack / 'bin', runtime / 'bin'):
        for path in directory.glob('*.dll'):
            providers.setdefault(path.name.lower(), path)
    queue, visited = images(candidate), set()
    while queue:
        path = queue.pop()
        if path in visited:
            continue
        visited.add(path)
        machine, imports = pe_info(path)
        if machine != ARM64:
            raise ValueError(f'{path}: non-ARM64 binary cannot be bundled')
        for name in imports:
            if system_dll(name):
                continue
            if name.startswith(('msys-', 'cygwin')):
                raise ValueError(f'{path}: forbidden runtime dependency {name}')
            target = bindir / name
            if target.exists() or (path.parent / name).exists():
                continue
            if name not in providers:
                raise ValueError(f'{path}: unresolved dependency {name}')
            shutil.copy2(providers[name], target)
            queue.append(target)
    for path in candidate.rglob('*'):
        if path.is_file() and path.suffix.lower() in ('.a', '.la', '.pc', '.h'):
            path.unlink()
    features, binaries = audit(candidate)
    capabilities = json.loads((work / 'portaudio-capabilities.json').read_text(encoding='utf-8'))
    if any(capabilities['host_api_indices'].get(name, -1) < 0 for name in ('ASIO', 'WASAPI')):
        raise ValueError('The native audio runtime did not pass ASIO/WASAPI capability checks')
    if capabilities.get('sha256') != hashlib.sha256((bindir / 'libportaudio.dll').read_bytes()).hexdigest():
        raise ValueError('Bundled PortAudio differs from the DLL verified for ASIO/WASAPI')
    native_tests = json.loads((work / 'test-results.json').read_text(encoding='utf-8'))
    required_tests = {'pbd-threading', 'temporal', 'evoral-midi', 'audiographer', 'arm64-dsp',
                      'midi-clock', 'audio-engine', 'native-video', 'create-session', 'reopen-session',
                      'record-play-effect', 'wav-export', 'jack-connectivity', 'midi-patches', 'allocator',
                      'vst2-scanner', 'vst2-hosting', 'vst3-scanner', 'vst3-hosting', 'vst3-scanner-startup',
                      'audio-capabilities'}
    passed_tests = {test['name'] for test in native_tests if test.get('status') == 'passed'}
    if required_tests - passed_tests:
        raise ValueError('Required native tests not passed: ' + ', '.join(sorted(required_tests - passed_tests)))
    for name in ('packages.txt', 'source-revisions.txt', 'asiosdk.sha256', 'msys2-release.txt',
                 'portaudio-capabilities.json', 'test-results.json'):
        shutil.copy2(work / name, candidate / name)
    revision = subprocess.check_output(['git', 'describe', '--tags', '--always'], text=True).strip()
    manifest = {'revision': revision, 'target': 'arm64-windows', 'features': features,
                'configured_features': configured_features, 'source_tree': 'includes local Windows ARM64 port changes',
                'binaries': binaries, 'validation': {'PE_and_dependency_audit': 'passed',
                'native_tests': sorted(passed_tests),
                'ASIO_hardware': 'not tested: no compatible interface available'}}
    (candidate / 'build-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    (candidate / 'README.txt').write_text(
        'Launch bin\\Ardour.exe. Keep the whole folder together.\n'
        'This build requires Windows 11 ARM64 and ARM64 plugins/ASIO components.\n'
        'ASIO hardware testing has not been performed. See build-manifest.json.\n', encoding='utf-8')
    if output.exists():
        shutil.rmtree(output)
    candidate.rename(output)
    archive = write_archive(output)
    print(f'Packaged {len(binaries)} ARM64 binaries: {output}\nArchive: {archive}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--validate', action='store_true')
    parser.add_argument('--output', required=True, type=Path)
    for name in ('stage', 'stack', 'runtime', 'work'):
        parser.add_argument('--' + name, type=Path)
    args = parser.parse_args()
    try:
        if args.validate:
            if args.work:
                finalize_validation(args.output, args.work)
            else:
                features, binaries = audit(args.output)
                print(f'Validated {len(features)} required features and {len(binaries)} ARM64 binaries.')
        else:
            if any(getattr(args, n) is None for n in ('stage', 'stack', 'runtime', 'work')):
                parser.error('Packaging requires --stage, --stack, --runtime, and --work')
            bundle(args.stage, args.stack, args.runtime, args.work, args.output)
    except (OSError, ValueError, struct.error) as error:
        parser.exit(1, f'Packaging failed: {error}\n')


if __name__ == '__main__':
    main()
