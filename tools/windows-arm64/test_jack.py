#!/usr/bin/env python3
"""Exercise an isolated native JACK dummy server and its client connections."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    system = Path(os.environ.get('SYSTEMROOT', os.environ.get('SystemRoot', 'C:/Windows')))
    env['PATH'] = os.pathsep.join([str(args.bin), str(system / 'System32')])
    name = 'ardour_arm64_validation_' + str(os.getpid())
    with os.add_dll_directory(str(args.bin)):
        jack = ctypes.CDLL(str(args.bin / 'libjack.dll'))
    jack.jack_client_open.restype = ctypes.c_void_p
    jack.jack_client_open.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    jack.jack_client_close.argtypes = [ctypes.c_void_p]
    jack.jack_get_sample_rate.argtypes = [ctypes.c_void_p]
    jack.jack_get_sample_rate.restype = ctypes.c_uint32
    jack.jack_port_register.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p,
                                       ctypes.c_ulong, ctypes.c_ulong]
    jack.jack_port_register.restype = ctypes.c_void_p
    jack.jack_port_name.argtypes = [ctypes.c_void_p]
    jack.jack_port_name.restype = ctypes.c_char_p
    jack.jack_activate.argtypes = [ctypes.c_void_p]
    jack.jack_deactivate.argtypes = [ctypes.c_void_p]
    jack.jack_connect.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
    jack.jack_port_connected_to.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    jack.jack_port_get_buffer.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    jack.jack_port_get_buffer.restype = ctypes.c_void_p
    callback_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_uint32, ctypes.c_void_p)
    jack.jack_set_process_callback.argtypes = [ctypes.c_void_p, callback_type, ctypes.c_void_p]
    client = None
    with (args.output / 'jackd.log').open('wb') as log:
        server = subprocess.Popen([args.bin / 'jackd.exe', '-r', '-n', name,
            '-d', 'dummy', '-r', '48000', '-p', '256'], cwd=args.output, env=env,
            stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                status = ctypes.c_int()
                # JackNoStartServer | JackServerName: never attach to a user's server.
                client = jack.jack_client_open(b'arm64_test', 1 | 4, ctypes.byref(status), name.encode())
                if client:
                    break
                if server.poll() is not None:
                    raise RuntimeError('Native JACK server exited; see jackd.log')
                time.sleep(0.25)
            if not client:
                raise RuntimeError('Native JACK connection timed out; see jackd.log')
            assert jack.jack_get_sample_rate(client) == 48000
            output = jack.jack_port_register(client, b'output', b'32 bit float mono audio', 2, 0)
            capture = jack.jack_port_register(client, b'input', b'32 bit float mono audio', 1, 0)
            assert output and capture
            counts = {'callbacks': 0, 'nonzero_input_callbacks': 0}

            @callback_type
            def process(frames, _):
                source = (ctypes.c_float * frames).from_address(jack.jack_port_get_buffer(output, frames))
                sink = (ctypes.c_float * frames).from_address(jack.jack_port_get_buffer(capture, frames))
                if abs(sink[0]) > 0.1:
                    counts['nonzero_input_callbacks'] += 1
                for i in range(frames):
                    source[i] = 0.25
                counts['callbacks'] += 1
                return 0

            assert jack.jack_set_process_callback(client, process, None) == 0
            assert jack.jack_activate(client) == 0
            assert jack.jack_connect(client, jack.jack_port_name(output), jack.jack_port_name(capture)) == 0
            assert jack.jack_port_connected_to(output, jack.jack_port_name(capture)) == 1
            time.sleep(1)
            assert jack.jack_deactivate(client) == 0
            assert counts['callbacks'] > 5 and counts['nonzero_input_callbacks'] > 5, counts
            (args.output / 'results.json').write_text(json.dumps(dict(status='passed', **counts), indent=2))
            print('Native ARM64 JACK connected, processed audio, and disconnected:', counts)
        finally:
            if client:
                jack.jack_client_close(client)
            if server.poll() is None:
                server.terminate()
            server.wait(timeout=10)


if __name__ == '__main__':
    main()
