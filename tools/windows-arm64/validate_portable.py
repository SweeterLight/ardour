#!/usr/bin/env python3
"""Launch through the Windows shell, relocate, and test the bundled helpers."""
import argparse
import ctypes
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


class ShellExecuteInfo(ctypes.Structure):
    _fields_ = [('cbSize', W.DWORD), ('fMask', W.ULONG), ('hwnd', W.HWND),
                ('lpVerb', W.LPCWSTR), ('lpFile', W.LPCWSTR), ('lpParameters', W.LPCWSTR),
                ('lpDirectory', W.LPCWSTR), ('nShow', ctypes.c_int), ('hInstApp', W.HINSTANCE),
                ('lpIDList', W.LPVOID), ('lpClass', W.LPCWSTR), ('hkeyClass', W.HKEY),
                ('dwHotKey', W.DWORD), ('hIcon', W.HANDLE), ('hProcess', W.HANDLE)]


def shell_launch(root, output, session):
    """Use the same ShellExecute 'open' verb as an Explorer double-click."""
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    user = ctypes.WinDLL('user32', use_last_error=True)
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    kernel.GetProcessId.argtypes = [W.HANDLE]
    kernel.GetProcessId.restype = W.DWORD
    kernel.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
    kernel.TerminateProcess.argtypes = [W.HANDLE, W.UINT]
    kernel.CloseHandle.argtypes = [W.HANDLE]
    user.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
    user.IsWindowVisible.argtypes = [W.HWND]
    user.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
    user.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
    enum_type = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    user.EnumWindows.argtypes = [enum_type, W.LPARAM]
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    shell.ShellExecuteExW.restype = W.BOOL
    psapi.EnumProcessModules.argtypes = [W.HANDLE, ctypes.POINTER(W.HMODULE), W.DWORD,
                                        ctypes.POINTER(W.DWORD)]
    psapi.GetModuleFileNameExW.argtypes = [W.HANDLE, W.HMODULE, W.LPWSTR, W.DWORD]
    profile = output / 'config'
    version = next((root / 'share').glob('ardour[0-9]*')).name.removeprefix('ardour')
    config = profile / ('Ardour' + version)
    config.mkdir(parents=True, exist_ok=True)
    (config / ('.a' + version)).touch()
    hints = ET.parse(session / 'session-smoke.ardour').getroot().find('EngineHints')
    device = hints.get('input-device', 'Silence') if hints is not None else 'Silence'
    configuration = ET.Element('Ardour')
    ET.SubElement(ET.SubElement(configuration, 'Config'), 'Option',
                  name='try-autostart-engine', value='1')
    ET.SubElement(configuration.find('Config'), 'Option', name='hide-dummy-backend', value='0')
    states = ET.SubElement(ET.SubElement(ET.SubElement(configuration, 'Extra'), 'AudioMIDISetup'), 'EngineStates')
    ET.SubElement(states, 'State', {'backend': 'None (Dummy)', 'driver': '', 'device': device,
        'input-device': device, 'output-device': device, 'sample-rate': '48000', 'buffer-size': '1024',
        'input-latency': '0', 'output-latency': '0', 'active': '1', 'use-buffered-io': '0', 'midi-option': 'None'})
    ET.ElementTree(configuration).write(config / 'config', encoding='utf-8', xml_declaration=True)
    system = Path(os.environ.get('SYSTEMROOT', os.environ.get('SystemRoot', 'C:/Windows')))
    saved = os.environ.copy()
    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x40 | 0x100 | 0x400  # NOCLOSEPROCESS | NOASYNC | FLAG_NO_UI
    info.lpVerb, info.lpFile = 'open', str(root / 'bin/Ardour.exe')
    # The session directory is timestamped, while its snapshot is named
    # session-smoke. Pass the snapshot so Ardour derives the correct name.
    info.lpParameters = subprocess.list2cmdline(['-a', '-n', str(session / 'session-smoke.ardour')])
    info.lpDirectory, info.nShow = str(output), 1
    windows = []
    try:
        # No build/runtime paths or inherited Ardour resource overrides.
        for key in list(os.environ):
            if key.upper().startswith(('ARDOUR_', 'GTK_', 'GDK_', 'SUIL_', 'VAMP_', 'LV2_')):
                del os.environ[key]
        os.environ.update(PATH=str(system / 'System32') + os.pathsep + str(system),
                          XDG_CONFIG_HOME=str(profile), XDG_CACHE_HOME=str(output / 'cache'),
                          ARDOUR_TRY_AUTOSTART_ENGINE='1', LANG='C')
        if not shell.ShellExecuteExW(ctypes.byref(info)) or not info.hProcess:
            raise ctypes.WinError(ctypes.get_last_error())
        pid = kernel.GetProcessId(info.hProcess)

        @enum_type
        def collect(hwnd, _):
            owner = W.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid and user.IsWindowVisible(hwnd):
                text = ctypes.create_unicode_buffer(2048)
                user.GetWindowTextW(hwnd, text, len(text))
                if text.value:
                    windows.append((hwnd, text.value))
            return True

        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if kernel.WaitForSingleObject(info.hProcess, 0) == 0:
                raise RuntimeError('Portable GUI exited during startup')
            windows.clear()
            user.EnumWindows(collect, 0)
            if any('session-smoke' in title for _, title in windows):
                break
            time.sleep(0.25)
        else:
            raise RuntimeError('Portable editor did not open the test session: ' + repr(windows))
        modules = (W.HMODULE * 2048)()
        needed = W.DWORD()
        if not psapi.EnumProcessModules(info.hProcess, modules, ctypes.sizeof(modules), ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        loaded = []
        for handle in modules[:needed.value // ctypes.sizeof(W.HMODULE)]:
            filename = ctypes.create_unicode_buffer(32768)
            if not psapi.GetModuleFileNameExW(info.hProcess, handle, filename, len(filename)):
                raise ctypes.WinError(ctypes.get_last_error())
            path = Path(filename.value).resolve()
            if not path.is_relative_to(root) and not path.is_relative_to(system):
                raise RuntimeError(f'GUI loaded a module from outside the portable folder/Windows: {path}')
            loaded.append(str(path))
        return {'status': 'passed', 'launch': 'Windows ShellExecute open verb',
                'windows': [title for _, title in windows], 'loaded_modules': loaded,
                'PATH': os.environ['PATH']}
    finally:
        if info.hProcess:
            for hwnd, _ in windows:
                user.PostMessageW(hwnd, 0x10, 0, 0)  # Close only this test process's windows.
            if kernel.WaitForSingleObject(info.hProcess, 10000) != 0:
                kernel.TerminateProcess(info.hProcess, 1)
                kernel.WaitForSingleObject(info.hProcess, 5000)
            kernel.CloseHandle(info.hProcess)
        os.environ.clear()
        os.environ.update(saved)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('Run with native Windows ARM64 Python')
    root, work = args.package.resolve(), args.work.resolve()
    output = work / 'portable-validation'
    output.mkdir(exist_ok=True)
    session = max((work / 'tests').glob('session-smoke-*'), key=lambda p: p.stat().st_mtime)
    results = {}
    try:
        results['shell-launch'] = shell_launch(root, output / 'original', session)
        extraction = output / 'Relocated Ardour ARM64 with spaces'
        if extraction.exists():
            raise RuntimeError(f'Relocation destination already exists: {extraction}')
        extraction.mkdir()
        try:
            # Extract the actual deliverable to a different path. Windows may
            # prohibit renaming a recently loaded directory after process exit.
            shutil.unpack_archive(root.with_suffix('.zip'), extraction)
            relocated = extraction / root.name
            if not (relocated / 'bin/Ardour.exe').is_file():
                raise RuntimeError('Portable ZIP did not contain bin/Ardour.exe')
            results['relocated-shell-launch'] = shell_launch(relocated, output / 'relocated', session)
            for name, argv in (
                ('bundled-audio-apis', [sys.executable, Path(__file__).with_name('check_audio.py'),
                    '--stack', relocated, '--runtime', relocated,
                    '--output', output / 'portaudio-capabilities.json', '--stream']),
                ('bundled-video', [sys.executable, Path(__file__).with_name('test_video.py'),
                    '--bin', relocated / 'bin', '--runtime-bin', relocated / 'bin',
                    '--output', output / 'video']),
                ('bundled-jack', [sys.executable, Path(__file__).with_name('test_jack.py'),
                    '--bin', relocated / 'bin', '--output', output / 'jack']),
            ):
                completed = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
                (output / (name + '.log')).write_bytes(completed.stdout)
                if completed.returncode:
                    raise RuntimeError(f'{name} failed; see {output / (name + ".log")}')
                results[name] = {'status': 'passed'}
        finally:
            shutil.rmtree(extraction)
        print('Portable shell launch, relocation, video, and JACK validation passed.')
    finally:
        (work / 'portable-validation.json').write_text(json.dumps(results, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
