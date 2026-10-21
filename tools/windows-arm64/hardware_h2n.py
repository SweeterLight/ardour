#!/usr/bin/env python3
"""Interactive, machine-specific H2n validation of the portable ARM64 build."""

import argparse
import array
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import struct
import subprocess
import sys
import time
import urllib.request
import wave
import winreg
import xml.etree.ElementTree as ET
import zipfile


RATE = 44100
FRAMES = 1024
CHANNELS = 2
PA_INT16 = 0x00000008
PA_FLOAT32 = 0x00000001
WASAPI = 13
ASIO = 3
ASIO_URL = 'https://zoomcorp.com/documents/2767/H_and_F-Series_ASIO_v4.1.0_E.zip'


class HardwareBlocked(RuntimeError):
    """A prerequisite needs action outside the automated test."""


class HostInfo(ctypes.Structure):
    _fields_ = [('version', ctypes.c_int), ('type', ctypes.c_int), ('name', ctypes.c_char_p),
                ('device_count', ctypes.c_int), ('default_input', ctypes.c_int),
                ('default_output', ctypes.c_int)]


class DeviceInfo(ctypes.Structure):
    _fields_ = [('version', ctypes.c_int), ('name', ctypes.c_char_p), ('host', ctypes.c_int),
                ('inputs', ctypes.c_int), ('outputs', ctypes.c_int),
                ('low_input', ctypes.c_double), ('low_output', ctypes.c_double),
                ('high_input', ctypes.c_double), ('high_output', ctypes.c_double),
                ('rate', ctypes.c_double)]


class Parameters(ctypes.Structure):
    _fields_ = [('device', ctypes.c_int), ('channels', ctypes.c_int),
                ('format', ctypes.c_ulong), ('latency', ctypes.c_double),
                ('specific', ctypes.c_void_p)]


class WasapiStreamInfo(ctypes.Structure):
    _fields_ = [('size', ctypes.c_ulong), ('host_api_type', ctypes.c_int),
                ('version', ctypes.c_ulong), ('flags', ctypes.c_ulong),
                ('channel_mask', ctypes.c_ulong), ('host_output', ctypes.c_void_p),
                ('host_input', ctypes.c_void_p), ('thread_priority', ctypes.c_int),
                ('stream_category', ctypes.c_int), ('stream_option', ctypes.c_int),
                ('passthrough_format', ctypes.c_int),
                ('passthrough_rate', ctypes.c_uint),
                ('passthrough_channels', ctypes.c_uint),
                ('passthrough_bytes_per_second', ctypes.c_uint)]


class StreamInfo(ctypes.Structure):
    _fields_ = [('struct_version', ctypes.c_int), ('input_latency', ctypes.c_double),
                ('output_latency', ctypes.c_double), ('sample_rate', ctypes.c_double)]


Callback = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                           ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p)


def save_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def pe_machine(data):
    if len(data) < 64 or data[:2] != b'MZ':
        return None
    header = struct.unpack_from('<I', data, 0x3c)[0]
    if len(data) < header + 6 or data[header:header + 4] != b'PE\0\0':
        return None
    return {0xaa64: 'arm64', 0xa641: 'arm64ec', 0x8664: 'x64',
            0x014c: 'x86'}.get(struct.unpack_from('<H', data, header + 4)[0], 'other')


def asio_inspection(work):
    result = {'source': ASIO_URL, 'installed': [], 'package': {}, 'status': 'blocked'}
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\ASIO',
                            0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            for index in range(winreg.QueryInfoKey(key)[0]):
                name = winreg.EnumKey(key, index)
                with winreg.OpenKey(key, name) as driver:
                    try:
                        clsid = winreg.QueryValueEx(driver, 'CLSID')[0]
                    except FileNotFoundError:
                        clsid = None
                module = None
                machine = None
                if clsid:
                    try:
                        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,
                                            f'CLSID\\{clsid}\\InprocServer32') as registered:
                            module = os.path.expandvars(winreg.QueryValue(registered, None))
                        machine = pe_machine(Path(module).read_bytes())
                    except (FileNotFoundError, OSError):
                        pass
                result['installed'].append({'name': name, 'clsid': clsid,
                                            'module': module, 'machine': machine})
    except FileNotFoundError:
        pass

    archive = work / 'H_and_F-Series_ASIO_v4.1.0_E.zip'
    try:
        if not archive.exists():
            request = urllib.request.Request(ASIO_URL, headers={'User-Agent': 'Ardour-ARM64-hardware-audit'})
            with urllib.request.urlopen(request, timeout=30) as source, archive.open('wb') as target:
                shutil.copyfileobj(source, target)
        result['package']['sha256'] = hashlib.sha256(archive.read_bytes()).hexdigest()
        images = []
        with zipfile.ZipFile(archive) as zipped:
            for item in zipped.infolist():
                if item.is_dir():
                    continue
                name = item.filename
                if name.lower().endswith(('.exe', '.dll', '.sys')):
                    with zipped.open(item) as source:
                        machine = pe_machine(source.read(min(item.file_size, 1024 * 1024)))
                    images.append({'path': name, 'machine': machine})
            result['package']['files'] = [x.filename for x in zipped.infolist()]
        result['package']['images'] = images
        result['package']['arm64_visible'] = any(x['machine'] == 'arm64' for x in images)
        result['package']['note'] = ('Archive inspection alone cannot establish the architecture of '
                                     'components embedded in an installer.')
    except Exception as exc:
        result['package']['error'] = f'{type(exc).__name__}: {exc}'
        if archive.exists() and not zipfile.is_zipfile(archive):
            archive.unlink()
    if not any('h2n' in x['name'].lower() or 'zoom' in x['name'].lower()
               for x in result['installed']):
        result['reason'] = 'No ZOOM H2n ASIO driver is registered for native 64-bit applications.'
    else:
        result['reason'] = 'Registered driver needs native ARM64 and PortAudio device verification.'
    return result


def wave_format(audio, device, function):
    method = getattr(audio, function)
    method.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int]
    buffer = ctypes.create_string_buffer(128)
    size = method(buffer, len(buffer), device)
    if size < 0:
        raise RuntimeError(f'{function} failed: {size}')
    tag, channels, rate, _, _, bits, _ = struct.unpack_from('<HHIIHHH', buffer.raw)
    valid_bits = struct.unpack_from('<H', buffer.raw, 18)[0] if tag == 0xfffe else bits
    subtype = struct.unpack_from('<I', buffer.raw, 24)[0] if tag == 0xfffe else tag
    return {'channels': channels, 'rate': rate, 'bits': bits,
            'valid_bits': valid_bits, 'subtype': subtype, 'bytes': size}


def wasapi_format_support(audio, input_device, output_device, flags, sample_format=PA_INT16):
    input_info = WasapiStreamInfo()
    input_info.size = ctypes.sizeof(WasapiStreamInfo)
    input_info.host_api_type = WASAPI
    input_info.version = 1
    input_info.flags = flags
    output_info = WasapiStreamInfo()
    output_info.size = ctypes.sizeof(WasapiStreamInfo)
    output_info.host_api_type = WASAPI
    output_info.version = 1
    output_info.flags = flags
    inp = Parameters(input_device['index'], CHANNELS, sample_format,
                     input_device['low_input_latency'], ctypes.addressof(input_info))
    out = Parameters(output_device['index'], CHANNELS, sample_format,
                     output_device['low_output_latency'], ctypes.addressof(output_info))
    result = {}
    for name, input_pointer, output_pointer in (
            ('input', ctypes.byref(inp), None),
            ('output', None, ctypes.byref(out)),
            ('duplex', ctypes.byref(inp), ctypes.byref(out))):
        code = audio.Pa_IsFormatSupported(input_pointer, output_pointer, RATE)
        result[name] = {'code': code,
                        'message': 'supported' if code == 0 else
                        audio.Pa_GetErrorText(code).decode(errors='replace')}
    return result


class PortAudio:
    def __init__(self, package):
        self.directory = os.add_dll_directory(str(package / 'bin'))
        self.audio = ctypes.CDLL(str(package / 'bin/libportaudio.dll'))
        a = self.audio
        a.Pa_GetErrorText.argtypes = [ctypes.c_int]
        a.Pa_GetErrorText.restype = ctypes.c_char_p
        a.Pa_GetHostApiInfo.argtypes = [ctypes.c_int]
        a.Pa_GetHostApiInfo.restype = ctypes.POINTER(HostInfo)
        a.Pa_HostApiTypeIdToHostApiIndex.argtypes = [ctypes.c_int]
        a.Pa_GetDeviceInfo.argtypes = [ctypes.c_int]
        a.Pa_GetDeviceInfo.restype = ctypes.POINTER(DeviceInfo)
        a.Pa_GetDeviceCount.restype = ctypes.c_int
        a.Pa_IsFormatSupported.argtypes = [ctypes.POINTER(Parameters),
                                           ctypes.POINTER(Parameters), ctypes.c_double]
        a.Pa_OpenStream.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(Parameters),
                                    ctypes.POINTER(Parameters), ctypes.c_double, ctypes.c_ulong,
                                    ctypes.c_ulong, Callback, ctypes.c_void_p]
        a.Pa_GetStreamInfo.argtypes = [ctypes.c_void_p]
        a.Pa_GetStreamInfo.restype = ctypes.POINTER(StreamInfo)
        for name in ('Pa_StartStream', 'Pa_StopStream', 'Pa_CloseStream'):
            getattr(a, name).argtypes = [ctypes.c_void_p]
        self.check(a.Pa_Initialize(), 'PortAudio initialization')

    def close(self):
        self.audio.Pa_Terminate()
        self.directory.close()

    def check(self, code, action):
        if code:
            raise RuntimeError(f'{action}: {self.audio.Pa_GetErrorText(code).decode(errors="replace")} ({code})')

    def devices(self):
        count = self.audio.Pa_GetDeviceCount()
        self.check(count if count < 0 else 0, 'Device enumeration')
        entries = []
        for index in range(count):
            d = self.audio.Pa_GetDeviceInfo(index).contents
            host = self.audio.Pa_GetHostApiInfo(d.host).contents
            entries.append({'index': index, 'name': d.name.decode(errors='replace'),
                            'host': host.name.decode(errors='replace'), 'host_type': host.type,
                            'inputs': d.inputs, 'outputs': d.outputs, 'default_rate': d.rate,
                            'low_input_latency': d.low_input,
                            'low_output_latency': d.low_output})
        return entries

    def pair(self, entries, host_type):
        matches = [d for d in entries if d['host_type'] == host_type
                   and 'h2n' in d['name'].lower() and '[loopback]' not in d['name'].lower()]
        inputs = [d for d in matches if d['inputs'] >= CHANNELS]
        outputs = [d for d in matches if d['outputs'] >= CHANNELS]
        if len(inputs) != 1 or len(outputs) != 1:
            raise RuntimeError(f'Expected one stereo H2n input and output for host API {host_type}; '
                               f'found inputs={inputs}, outputs={outputs}')
        return inputs[0], outputs[0]

    def duplex(self, input_device, output_device, seconds, capture, exclusive=False,
               sample_format=PA_INT16):
        a = self.audio
        inp = Parameters(input_device['index'], CHANNELS, sample_format,
                         input_device['low_input_latency'], None)
        out = Parameters(output_device['index'], CHANNELS, sample_format,
                         output_device['low_output_latency'], None)
        sample_bytes = 4 if sample_format == PA_FLOAT32 else 2
        if exclusive:
            input_info = WasapiStreamInfo()
            input_info.size = ctypes.sizeof(WasapiStreamInfo)
            input_info.host_api_type = WASAPI
            input_info.version = 1
            input_info.flags = 1
            output_info = WasapiStreamInfo()
            output_info.size = ctypes.sizeof(WasapiStreamInfo)
            output_info.host_api_type = WASAPI
            output_info.version = 1
            output_info.flags = 1
            inp.specific = ctypes.addressof(input_info)
            out.specific = ctypes.addressof(output_info)
        self.check(a.Pa_IsFormatSupported(ctypes.byref(inp), ctypes.byref(out), RATE),
                   '44.1 kHz stereo duplex format')
        chunks = []
        observed = {'wasapi_mode': 'exclusive' if exclusive else 'shared',
                    'sample_format': ('stereo float32' if sample_format == PA_FLOAT32
                                      else 'stereo signed 16-bit PCM'), 'buffer_frames': FRAMES,
                    'callbacks': 0, 'status_or': 0, 'input_underflows': 0,
                    'input_overflows': 0, 'output_underflows': 0, 'output_overflows': 0,
                    'callback_error': None}

        @Callback
        def callback(input_buffer, output_buffer, frames, _timing, flags, _user):
            try:
                observed['callbacks'] += 1
                observed['status_or'] |= flags
                for bit, name in ((1, 'input_underflows'), (2, 'input_overflows'),
                                  (4, 'output_underflows'), (8, 'output_overflows')):
                    if flags & bit:
                        observed[name] += 1
                if capture and input_buffer:
                    chunks.append(ctypes.string_at(input_buffer, frames * CHANNELS * sample_bytes))
                if output_buffer:
                    ctypes.memset(output_buffer, 0, frames * CHANNELS * sample_bytes)
                return 0
            except Exception as exc:
                observed['callback_error'] = str(exc)
                return 2  # paAbort

        stream = ctypes.c_void_p()
        self.check(a.Pa_OpenStream(ctypes.byref(stream), ctypes.byref(inp), ctypes.byref(out),
                                   RATE, FRAMES, 0, callback, None), 'Duplex stream open')
        try:
            info = a.Pa_GetStreamInfo(stream).contents
            observed['actual_rate'] = info.sample_rate
            observed['reported_input_latency'] = info.input_latency
            observed['reported_output_latency'] = info.output_latency
            self.check(a.Pa_StartStream(stream), 'Duplex stream start')
            try:
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    time.sleep(min(1, max(0, deadline - time.monotonic())))
            finally:
                self.check(a.Pa_StopStream(stream), 'Duplex stream stop')
        finally:
            self.check(a.Pa_CloseStream(stream), 'Duplex stream close')
        observed['frames'] = sum(len(x) for x in chunks) // (CHANNELS * sample_bytes) if capture else (
            observed['callbacks'] * FRAMES)
        if observed['callback_error']:
            raise RuntimeError('PortAudio callback failed: ' + observed['callback_error'])
        return observed, b''.join(chunks)


def analyze_capture(raw, output):
    with wave.open(str(output), 'wb') as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(raw)
    samples = array.array('h')
    samples.frombytes(raw)
    if sys.byteorder != 'little':
        samples.byteswap()
    rms = lambda seq: math.sqrt(sum(int(x) * int(x) for x in seq) / max(1, len(seq)))
    window_size = int(RATE * CHANNELS / 4)
    window_levels = [rms(samples[i:i + window_size])
                     for i in range(0, len(samples), window_size)
                     if len(samples[i:i + window_size]) >= window_size // 2]
    quiet_windows = sorted(window_levels)[:max(3, len(window_levels) // 10)]
    baseline = statistics.median(quiet_windows) if quiet_windows else 0
    loudest_window = max(window_levels, default=0)
    peak = max((abs(x) for x in samples), default=0)
    clipped = sum(abs(x) >= 32760 for x in samples)
    return {'wav': str(output), 'frames': len(samples) // CHANNELS,
            'baseline_rms': round(baseline, 2),
            'baseline_method': 'median of quietest 10% of quarter-second windows',
            'loudest_quarter_second_rms': round(loudest_window, 2),
            'peak': peak, 'clipped_samples': clipped,
            'status': 'passed' if loudest_window >= max(300, baseline * 3)
                      and not clipped else 'failed'}


def inspect_wav(path):
    with wave.open(str(path), 'rb') as wav:
        info = {'path': str(path), 'rate': wav.getframerate(),
                'channels': wav.getnchannels(), 'bits': 8 * wav.getsampwidth(),
                'frames': wav.getnframes()}
        raw = wav.readframes(wav.getnframes())
    samples = array.array('h')
    if info['bits'] == 16:
        samples.frombytes(raw)
    info['peak'] = max((abs(x) for x in samples), default=0)
    info['status'] = ('passed' if (info['rate'], info['channels'], info['bits']) ==
                      (RATE, CHANNELS, 16) and info['frames'] >= RATE and info['peak'] > 500
                      else 'failed')
    return info


def inspect_session(package, session, since):
    state = session / 'hardware-h2n.ardour'
    if not state.is_file():
        raise RuntimeError(f'Ardour did not save its session: {state}')
    state_root = ET.parse(state).getroot()
    audiofiles = sorted((session / 'interchange').rglob('*.wav'))
    if not audiofiles:
        raise RuntimeError('Ardour session contains no recorded audio file')
    probe = package / 'bin/ffmpeg.exe'
    evidence = []
    for path in audiofiles:
        completed = subprocess.run([probe, '-v', 'error', '-i', path, '-t', '12',
            '-f', 's16le', '-acodec', 'pcm_s16le', '-ac', '2', '-ar', str(RATE), '-'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45)
        if completed.returncode:
            raise RuntimeError(f'Could not decode recorded file {path}: '
                               + completed.stderr.decode(errors='replace'))
        samples = array.array('h')
        samples.frombytes(completed.stdout)
        evidence.append({'path': str(path), 'peak': max((abs(x) for x in samples), default=0),
                         'samples': len(samples), 'modified_after_launch':
                         path.stat().st_mtime >= since - 2})
    routes = state_root.findall('./Routes/Route')
    selections = [engine.attrib for engine in state_root.findall('EngineState/PortEngine')]
    selected = next((item for item in selections
                     if item.get('backend') == 'PortAudio' and
                     item.get('driver') == 'Windows WASAPI' and
                     'h2n' in item.get('input-device', '').lower() and
                     'h2n' in item.get('output-device', '').lower()), {})
    input_key = f"PortAudio;Windows WASAPI;{selected.get('input-device', '')}"
    output_key = f"PortAudio;Windows WASAPI;{selected.get('output-device', '')}"
    physical_inputs = []
    physical_outputs = []
    for route in routes:
        ports = route.findall('./IO/Port')
        connections = [(port.get('direction'), connection.get('for', ''),
                        connection.get('other', ''))
                       for port in ports for connection in port.findall('ExtConnection')
                       if connection.get('for', '') in (input_key, output_key)]
        if route.get('audio-playlist'):
            physical_inputs.extend(other for direction, key, other in connections
                                   if direction == 'Input' and key == input_key and
                                   other.startswith('system:capture_'))
        else:
            physical_outputs.extend(other for direction, key, other in connections
                                    if direction == 'Output' and key == output_key and
                                    other.startswith('system:playback_'))
    return {'status': 'passed' if any(x['peak'] > 500 and x['samples'] >= RATE and
                                    x['modified_after_launch'] for x in evidence)
            else 'failed', 'audiofiles': evidence, 'session_file': str(state),
            'active_input_routing_key': input_key,
            'active_output_routing_key': output_key,
            'physical_input_connections': sorted(set(physical_inputs)),
            'physical_output_connections': sorted(set(physical_outputs)),
            'routing_status': 'passed' if len(set(physical_inputs)) >= CHANNELS and
                              len(set(physical_outputs)) >= CHANNELS else 'failed'}


def prompt_yes(question):
    return input(question + ' [y/N]: ').strip().lower() in ('y', 'yes')


def gui_check(package, work, report):
    session = work / 'session'
    state = session / 'hardware-h2n.ardour'
    if not state.exists():
        subprocess.run([package / 'bin/ardour9-new_session.exe', '-s', str(RATE),
                        session, 'hardware-h2n'], check=True, timeout=45)
    version = next((package / 'share').glob('ardour[0-9]*')).name.removeprefix('ardour')
    profile = work / 'profile'
    config = profile / ('Ardour' + version)
    config.mkdir(parents=True, exist_ok=True)
    (config / ('.a' + version)).touch()
    env = os.environ.copy()
    env['XDG_CONFIG_HOME'] = str(profile)
    env['XDG_CACHE_HOME'] = str(work / 'cache')
    env['ARDOUR_TRY_AUTOSTART_ENGINE'] = '1'
    env['LANG'] = 'C.UTF-8'
    env['LC_ALL'] = 'C.UTF-8'
    export = work / 'gui-export.wav'
    print('\nArdour will open a dedicated 44.1 kHz session. In Audio/MIDI setup select', flush=True)
    print('PortAudio (default) -> Windows WASAPI, H2n microphone and H2n output, 1024 frames.', flush=True)
    print('In the track routing grid, connect Audio 1 input 1/2 to system:capture_1/2;', flush=True)
    print('connect Master output 1/2 to system:playback_1/2.', flush=True)
    print('Record a short stereo track from the H2n, save, close, reopen, and listen on H2n headphones.', flush=True)
    print(f'Export the session as stereo 44.1 kHz 16-bit PCM WAV to: {export}', flush=True)
    print('Then, disconnect the headphones and connect the H2n line/headphone output', flush=True)
    print('to its mic/line input with the 3.5 mm cable. Set levels very low, plug-in power', flush=True)
    print('and direct monitoring OFF. In Ardour Audio/MIDI setup -> Latency, measure', flush=True)
    print('three times; note each round-trip sample count and use the clean result.', flush=True)
    print('Close Ardour when done.\n', flush=True)
    launch_time = time.time()
    process = subprocess.Popen([package / 'bin/Ardour.exe', '-a', '-n', state],
                               cwd=work, env=env)
    input('Press Enter after Ardour has been closed: ')
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        raise RuntimeError('Please close the hardware-test Ardour window before continuing')
    report['checks']['ardour_session'] = inspect_session(package, session, launch_time)
    report['checks']['ardour_routing'] = {
        'status': report['checks']['ardour_session']['routing_status'],
        'input_connections': report['checks']['ardour_session']['physical_input_connections'],
        'output_connections': report['checks']['ardour_session']['physical_output_connections']}
    save_report(work / 'hardware-h2n-report.json', report)
    if not export.is_file() or export.stat().st_mtime < launch_time - 2:
        alternatives = [path for path in work.glob('*.wav')
                        if path.name not in ('h2n-capture.wav', 'gui-export.wav') and
                        path.stat().st_mtime >= launch_time - 2]
        if len(alternatives) != 1:
            raise RuntimeError(f'Ardour export is missing: {export}; alternatives: {alternatives}')
        shutil.copy2(alternatives[0], export)
        report['ardour_export_original_path'] = str(alternatives[0])
    report['checks']['ardour_export'] = inspect_wav(export)
    if export.stat().st_mtime < launch_time - 2:
        report['checks']['ardour_export']['status'] = 'failed'
        report['checks']['ardour_export']['reason'] = 'Export predates this PortAudio GUI test.'
    state_root = ET.parse(state).getroot()
    selections = [engine.attrib for engine in state_root.findall('EngineState/PortEngine')]
    selected_h2n = next((selection for selection in selections
                         if selection.get('backend') == 'PortAudio' and
                         selection.get('driver') == 'Windows WASAPI' and
                         'h2n' in selection.get('input-device', '').lower() and
                         'h2n' in selection.get('output-device', '').lower()), None)
    report['checks']['ardour_wasapi_selection'] = {
        'status': 'passed' if selected_h2n else 'failed',
        'saved_engine_selection': selected_h2n,
        'all_engine_selections': selections,
        'reason': None if selected_h2n else
                  'Saved Ardour session does not select both H2n WASAPI endpoints.'}
    save_report(work / 'hardware-h2n-report.json', report)
    if not selected_h2n:
        raise HardwareBlocked('Ardour did not save both H2n Windows WASAPI endpoints; '
                              'complete PortAudio setup before latency measurement.')
    report['checks']['headphone_listening'] = {
        'status': 'passed' if prompt_yes('Was the saved recording audible and clean through H2n headphones?')
                  else 'failed', 'evidence': 'user listening check'}
    report['checks']['ardour_reopen'] = {
        'status': 'passed' if prompt_yes('Did the session reopen and play correctly in Ardour?')
                  else 'failed', 'evidence': 'user GUI check'}
    readings = input('Enter three Ardour round-trip latency readings in samples, '
                     'comma separated (or "defer"): ')
    if readings.strip().lower() == 'defer':
        reason = 'Loopback latency measurement deferred because the H2n cable test is unavailable.'
        report['checks']['latency'] = {'status': 'blocked', 'reason': reason,
                                        'measurements': []}
        raise HardwareBlocked(reason)
    values = [int(x.strip()) for x in readings.split(',')]
    if len(values) != 3 or min(values) <= 0:
        raise RuntimeError('Expected three positive round-trip latency readings')
    report['checks']['latency'] = {'samples': values,
        'milliseconds': [round(x * 1000 / RATE, 3) for x in values],
        'buffer_frames': FRAMES,
        'status': 'passed' if max(values) - min(values) <= FRAMES and
                  prompt_yes('Were all three Ardour measurements clean and unclipped?') else 'failed',
        'evidence': 'user readings from Ardour Latency Measurement Tool'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', required=True, type=Path)
    parser.add_argument('--work', required=True, type=Path)
    parser.add_argument('--report-copy', type=Path,
                        help='Copy the hardware result beside the portable ZIP')
    parser.add_argument('--probe-only', action='store_true', help='Enumerate devices without opening streams')
    parser.add_argument('--stability-only', action='store_true',
                        help='Run only the unattended two-minute duplex test')
    parser.add_argument('--resume-capture', action='store_true',
                        help='Recheck the existing capture and continue hardware testing')
    parser.add_argument('--float-probe', action='store_true',
                        help='Diagnose the float32 WASAPI input format used by Ardour')
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('Use native Windows ARM64 Python')
    package = args.package.resolve()
    work = args.work.resolve() / 'hardware-h2n'
    work.mkdir(parents=True, exist_ok=True)
    path = work / ('h2n-float-probe-run.json' if args.float_probe else
                   'hardware-h2n-report.json')
    previous_capture = (json.loads(path.read_text(encoding='utf-8'))
                        if args.resume_capture and path.exists() else None)
    report = {'target': 'H2n stereo, WASAPI, 44100 Hz, 16-bit PCM',
              'package': str(package), 'build_sha256': hashlib.sha256(
                  (package / 'bin/Ardour.exe').read_bytes()).hexdigest(),
              'portaudio_backend_sha256': hashlib.sha256(
                  (package / 'lib/ardour9/backends/portaudio_callback_backend.dll')
                  .read_bytes()).hexdigest(),
              'checks': {}, 'asio': {}}
    audio = None
    try:
        audio = PortAudio(package)
        devices = audio.devices()
        report['devices'] = devices
        inp, out = audio.pair(devices, WASAPI)
        report['selected'] = {'input': inp, 'output': out}
        formats = {'input': wave_format(audio.audio, inp['index'], 'PaWasapi_GetDeviceDefaultFormat'),
                   'output': wave_format(audio.audio, out['index'], 'PaWasapi_GetDeviceDefaultFormat')}
        report['endpoint_formats'] = formats
        report['mix_formats'] = {
            'input': wave_format(audio.audio, inp['index'], 'PaWasapi_GetDeviceMixFormat'),
            'output': wave_format(audio.audio, out['index'], 'PaWasapi_GetDeviceMixFormat')}
        in_params = Parameters(inp['index'], CHANNELS, PA_INT16, inp['low_input_latency'], None)
        out_params = Parameters(out['index'], CHANNELS, PA_INT16, out['low_output_latency'], None)
        report['format_support'] = {}
        for name, input_pointer, output_pointer in (
                ('input', ctypes.byref(in_params), None),
                ('output', None, ctypes.byref(out_params)),
                ('duplex', ctypes.byref(in_params), ctypes.byref(out_params))):
            code = audio.audio.Pa_IsFormatSupported(input_pointer, output_pointer, RATE)
            report['format_support'][name] = {'code': code,
                'message': 'supported' if code == 0 else
                           audio.audio.Pa_GetErrorText(code).decode(errors='replace')}
        report['wasapi_modes'] = {
            'exclusive': wasapi_format_support(audio.audio, inp, out, 1),
            'exclusive_float32': wasapi_format_support(audio.audio, inp, out, 1, 1),
            'shared_auto_convert': wasapi_format_support(audio.audio, inp, out, 1 << 6)}
        report['asio'] = asio_inspection(work)
        asio_devices = [d for d in devices if d['host_type'] == ASIO and 'h2n' in d['name'].lower()]
        report['asio']['portaudio_devices'] = asio_devices
        native_asio = any(x['machine'] == 'arm64' for x in report['asio']['installed'])
        if native_asio and asio_devices:
            try:
                asio_in, asio_out = audio.pair(devices, ASIO)
                result, _ = audio.duplex(asio_in, asio_out, 10, False)
                report['asio']['stream'] = result
                report['asio']['status'] = ('passed' if not result['status_or'] & 15 else 'failed')
                report['asio']['reason'] = ('Native ARM64 ASIO H2n duplex stream passed' if
                    report['asio']['status'] == 'passed' else 'ASIO stream reported an xrun')
            except Exception as exc:
                report['asio']['status'] = 'failed'
                report['asio']['stream_error'] = str(exc)
        save_report(path, report)
        if args.probe_only:
            report['status'] = 'probe-complete'
            save_report(path, report)
            print(json.dumps({'selected': report['selected'], 'endpoint_formats': formats,
                              'mix_formats': report['mix_formats'],
                              'format_support': report['format_support'],
                              'wasapi_modes': report['wasapi_modes'],
                              'asio': report['asio']}, indent=2))
            return
        for direction, fmt in formats.items():
            if (fmt['rate'], fmt['channels'], fmt['bits'], fmt['valid_bits'], fmt['subtype']) != (
                    RATE, CHANNELS, 16, 16, 1):
                raise HardwareBlocked(f'H2n {direction} Windows endpoint must be set to stereo '
                                      f'44.1 kHz/16-bit PCM; observed {fmt}')
        exclusive = bool(report['format_support']['duplex']['code'])
        if exclusive and report['wasapi_modes']['exclusive']['duplex']['code']:
            raise HardwareBlocked('H2n 44.1 kHz duplex format is unavailable in both shared '
                'and exclusive WASAPI modes: ' + json.dumps(report['wasapi_modes']))
        report['selected_wasapi_mode'] = 'exclusive' if exclusive else 'shared'
        save_report(path, report)
        if args.float_probe:
            stream, raw = audio.duplex(inp, out, 5, True, exclusive, PA_FLOAT32)
            samples = array.array('f')
            samples.frombytes(raw)
            peak = max((abs(x) for x in samples if math.isfinite(x)), default=0)
            rms = math.sqrt(sum(x * x for x in samples if math.isfinite(x)) /
                            len(samples)) if samples else 0
            diagnosis = {'stream': stream, 'peak': peak, 'rms': rms,
                         'digital_silence': peak == 0, 'sample_count': len(samples)}
            (work / 'h2n-float-probe.json').write_text(
                json.dumps(diagnosis, indent=2), encoding='utf-8')
            print(json.dumps(diagnosis, indent=2))
            return
        if args.stability_only:
            stable, _ = audio.duplex(inp, out, 120, False, exclusive)
            stable['status'] = ('passed' if stable['callbacks'] > 100 and
                                not stable['status_or'] & 15 and
                                abs(stable['actual_rate'] - RATE) < 1 else 'failed')
            report['checks']['two_minute_duplex'] = stable
            report['status'] = 'stability-passed' if stable['status'] == 'passed' else 'failed'
            save_report(path, report)
            print(json.dumps(stable, indent=2), flush=True)
            return 0 if stable['status'] == 'passed' else 1
        if args.resume_capture:
            previous = previous_capture or {}
            # Direct capture uses libportaudio.dll, not Ardour's backend DLL.
            # A GUI-only backend fix may therefore reuse this hardware evidence.
            if (previous.get('selected') != report['selected']
                    or previous.get('endpoint_formats') != report['endpoint_formats']
                    or previous.get('selected_wasapi_mode') != report['selected_wasapi_mode']):
                raise HardwareBlocked('Existing H2n capture does not match the current package '
                                      'and device settings; record a fresh capture.')
            old_stream = previous.get('checks', {}).get('microphone_stream', {})
            if (old_stream.get('status_or') != 0 or old_stream.get('actual_rate') != RATE
                    or old_stream.get('sample_format') != 'stereo signed 16-bit PCM'):
                raise HardwareBlocked('Existing H2n capture stream was not clean 44.1 kHz/16-bit')
            capture_file = work / 'h2n-capture.wav'
            with wave.open(str(capture_file), 'rb') as wav:
                if (wav.getnchannels(), wav.getframerate(), wav.getsampwidth()) != (2, RATE, 2):
                    raise HardwareBlocked('Existing H2n capture WAV format does not match')
                raw = wav.readframes(wav.getnframes())
            report['checks']['microphone_stream'] = old_stream
            report['checks']['microphone_capture'] = analyze_capture(raw, capture_file)
            report['capture_backend_sha256'] = previous.get('capture_backend_sha256') or \
                previous.get('portaudio_backend_sha256')
        else:
            print('The H2n must show stereo USB Audio I/F at 44.1 kHz with USB INPUT ON.', flush=True)
            input('Press Enter when ready to capture ten seconds; make a short sound after two seconds: ')
            capture, raw = audio.duplex(inp, out, 10, True, exclusive)
            report['checks']['microphone_stream'] = capture
            report['checks']['microphone_capture'] = analyze_capture(raw, work / 'h2n-capture.wav')
        save_report(path, report)
        if (report['checks']['microphone_stream']['status_or'] & 15 or
                report['checks']['microphone_capture']['status'] != 'passed'):
            raise RuntimeError('H2n microphone capture failed signal or stream-status checks')
        previous_stable = ((previous_capture or {}).get('checks', {}).get('two_minute_duplex')
                           if args.resume_capture else None)
        if (previous_stable and previous_stable.get('status') == 'passed' and
                previous_stable.get('wasapi_mode') == report['selected_wasapi_mode'] and
                previous_stable.get('buffer_frames') == FRAMES and
                previous_stable.get('sample_format') == 'stereo signed 16-bit PCM' and
                previous_stable.get('actual_rate') == RATE and
                previous_stable.get('status_or') == 0):
            stable = previous_stable
            print('Reusing the passed two-minute H2n stream from this package and device.',
                  flush=True)
        else:
            print('Running a two-minute silent H2n input/output stream at 1024 frames.', flush=True)
            stable, _ = audio.duplex(inp, out, 120, False, exclusive)
            stable['status'] = ('passed' if stable['callbacks'] > 100 and
                                not stable['status_or'] & 15 and
                                abs(stable['actual_rate'] - RATE) < 1 else 'failed')
        report['checks']['two_minute_duplex'] = stable
        save_report(path, report)
        if stable['status'] != 'passed':
            raise RuntimeError('Two-minute H2n duplex stream reported an xrun or rate mismatch')
        audio.close()
        audio = None
        gui_check(package, work, report)
        for name, result in report['checks'].items():
            if result.get('status') == 'failed':
                raise RuntimeError(f'Hardware check failed: {name}')
        report['status'] = 'passed'
        print('H2n WASAPI capture, duplex, Ardour, export, listening, and latency checks passed.')
    except (Exception, KeyboardInterrupt) as exc:
        report['status'] = ('blocked' if isinstance(exc, (HardwareBlocked, EOFError))
                            else 'failed')
        report['error'] = f'{type(exc).__name__}: {exc}'
        print(report['error'], file=sys.stderr)
        return 1
    finally:
        if audio is not None:
            audio.close()
        save_report(path, report)
        if not (args.probe_only or args.float_probe):
            shutil.copy2(path, args.report_copy or
                         package.parent / 'Ardour-arm64-hardware-validation.json')


if __name__ == '__main__':
    sys.exit(main())
