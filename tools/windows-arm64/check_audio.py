"""Verify the actual native PortAudio runtime exposes ASIO and WASAPI."""
import argparse
import ctypes
import json
import hashlib
import time
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stack', required=True, type=Path)
    parser.add_argument('--runtime', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--stream', action='store_true', help='Open a silent shared WASAPI output stream')
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('Run with native Windows ARM64 Python')
    # Keep these handles alive until the DLL is unloaded.
    directories = [os.add_dll_directory(str(p / 'bin')) for p in (args.stack, args.runtime)]
    audio = ctypes.CDLL(str(args.stack / 'bin/libportaudio.dll'))
    audio.Pa_GetVersionText.restype = ctypes.c_char_p
    audio.Pa_GetErrorText.argtypes = [ctypes.c_int]
    audio.Pa_GetErrorText.restype = ctypes.c_char_p
    audio.Pa_HostApiTypeIdToHostApiIndex.argtypes = [ctypes.c_int]
    for name in ('PaAsio_GetAvailableBufferSizes', 'PaAsio_GetInputChannelName',
                 'PaAsio_GetOutputChannelName', 'PaAsio_ShowControlPanel'):
        getattr(audio, name)
    error = audio.Pa_Initialize()
    if error:
        raise RuntimeError(audio.Pa_GetErrorText(error).decode())
    try:
        hosts = {name: audio.Pa_HostApiTypeIdToHostApiIndex(value)
                 for name, value in (('ASIO', 3), ('WASAPI', 13))}
        if any(index < 0 for index in hosts.values()):
            raise RuntimeError(f'Required audio API missing: {hosts}')
        result = {'version': audio.Pa_GetVersionText().decode(), 'host_api_indices': hosts,
                  'sha256': hashlib.sha256((args.stack / 'bin/libportaudio.dll').read_bytes()).hexdigest(),
                  'ASIO_extensions': 'present', 'ASIO_hardware': 'not tested'}
        if args.stream:
            class HostInfo(ctypes.Structure):
                _fields_ = [('version', ctypes.c_int), ('type', ctypes.c_int), ('name', ctypes.c_char_p),
                            ('devices', ctypes.c_int), ('default_input', ctypes.c_int), ('default_output', ctypes.c_int)]
            class DeviceInfo(ctypes.Structure):
                _fields_ = [('version', ctypes.c_int), ('name', ctypes.c_char_p), ('host', ctypes.c_int),
                            ('inputs', ctypes.c_int), ('outputs', ctypes.c_int),
                            ('low_input', ctypes.c_double), ('low_output', ctypes.c_double),
                            ('high_input', ctypes.c_double), ('high_output', ctypes.c_double),
                            ('rate', ctypes.c_double)]
            class Parameters(ctypes.Structure):
                _fields_ = [('device', ctypes.c_int), ('channels', ctypes.c_int), ('format', ctypes.c_ulong),
                            ('latency', ctypes.c_double), ('specific', ctypes.c_void_p)]
            audio.Pa_GetHostApiInfo.argtypes = [ctypes.c_int]
            audio.Pa_GetHostApiInfo.restype = ctypes.POINTER(HostInfo)
            audio.Pa_GetDeviceInfo.argtypes = [ctypes.c_int]
            audio.Pa_GetDeviceInfo.restype = ctypes.POINTER(DeviceInfo)
            device_id = audio.Pa_GetHostApiInfo(hosts['WASAPI']).contents.default_output
            if device_id < 0:
                raise RuntimeError('No default WASAPI output device is available for stream validation')
            device = audio.Pa_GetDeviceInfo(device_id).contents
            channels = min(2, device.outputs)
            parameters = Parameters(device_id, channels, 1, device.low_output, None)  # paFloat32
            callback_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p)
            callbacks = [0]

            @callback_type
            def callback(_, output, frames, __, ___, ____):
                ctypes.memset(output, 0, frames * channels * ctypes.sizeof(ctypes.c_float))
                callbacks[0] += 1
                return 0

            audio.Pa_OpenStream.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
                ctypes.POINTER(Parameters), ctypes.c_double, ctypes.c_ulong, ctypes.c_ulong,
                callback_type, ctypes.c_void_p]
            for method in ('Pa_StartStream', 'Pa_StopStream', 'Pa_CloseStream'):
                getattr(audio, method).argtypes = [ctypes.c_void_p]
            stream = ctypes.c_void_p()
            error = audio.Pa_OpenStream(ctypes.byref(stream), None, ctypes.byref(parameters),
                                       device.rate, 256, 0, callback, None)
            if error:
                raise RuntimeError('WASAPI open: ' + audio.Pa_GetErrorText(error).decode())
            try:
                error = audio.Pa_StartStream(stream)
                if error:
                    raise RuntimeError('WASAPI start: ' + audio.Pa_GetErrorText(error).decode())
                time.sleep(0.75)
                error = audio.Pa_StopStream(stream)
                if error or callbacks[0] < 5:
                    raise RuntimeError(f'WASAPI callbacks failed: error={error}, callbacks={callbacks[0]}')
                result['WASAPI_output_stream'] = {'status': 'passed', 'callbacks': callbacks[0],
                    'device': device.name.decode(errors='replace'), 'sample_rate': device.rate,
                    'signal': 'silence'}
            finally:
                audio.Pa_CloseStream(stream)
        args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result))
    finally:
        audio.Pa_Terminate()


if __name__ == '__main__':
    main()
