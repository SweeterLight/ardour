#!/usr/bin/env python3
"""Exercise native video decoding, thumbnail serving, and monitor control."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.parse
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin', required=True, type=Path)
    parser.add_argument('--runtime-bin', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    system = Path(os.environ.get('SYSTEMROOT', os.environ.get('SystemRoot', 'C:/Windows')))
    env['PATH'] = os.pathsep.join([str(args.bin), str(args.runtime_bin), str(system / 'System32')])
    env['XDG_CONFIG_HOME'] = str(output / 'config')
    env['XDG_CACHE_HOME'] = str(output / 'cache')
    results = {}

    def command(name, argv):
        result = subprocess.run(argv, env=env, cwd=output, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=60)
        (output / (name + '.log')).write_bytes(result.stdout)
        if result.returncode:
            raise RuntimeError(f'{name}: exit {result.returncode}; see {output}')
        results[name] = 'passed'
        return result.stdout

    try:
        command('harvid-version', [args.bin / 'harvid.exe', '--version'])
        command('xjadeo-version', [args.bin / 'xjadeo.exe', '--version'])
        clip = output / 'video-test.mkv'
        command('encode', [args.runtime_bin / 'ffmpeg.exe', '-nostdin', '-hide_banner',
            '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc=size=320x180:rate=25:duration=1',
            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=1',
            '-c:v', 'ffv1', '-c:a', 'pcm_s16le', clip])
        info = json.loads(command('probe', [args.runtime_bin / 'ffprobe.exe', '-v', 'error',
                           '-show_streams', '-of', 'json', clip]))
        if not {'audio', 'video'} <= {stream['codec_type'] for stream in info['streams']}:
            raise RuntimeError('Audio/video streams missing from synthetic test clip')
        # Serve only the synthetic fixture directory, on loopback.
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        with (output / 'server.log').open('wb') as log:
            server = subprocess.Popen([args.bin / 'harvid.exe', '-P', '127.0.0.1',
                '-p', str(port), '-T', '15', str(output) + os.sep], env=env, cwd=output,
                stdout=log, stderr=subprocess.STDOUT)
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                endpoint = f'http://127.0.0.1:{port}'
                deadline = time.monotonic() + 12
                while True:
                    try:
                        with opener.open(endpoint + '/status', timeout=1) as response:
                            response.read()
                        break
                    except OSError:
                        if server.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError(f'Native video server failed to start; see {output / "server.log"}')
                        time.sleep(0.1)
                query = urllib.parse.urlencode({'file': clip.name, 'frame': '0', 'format': 'png', 'w': '320', 'h': '180'})
                with opener.open(endpoint + '/?' + query, timeout=10) as response:
                    thumbnail = response.read()
                if not thumbnail.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise RuntimeError('Video server did not return a decoded PNG frame')
                (output / 'thumbnail.png').write_bytes(thumbnail)
                results['serve-decoded-frame'] = 'passed'
            finally:
                server.terminate()
                server.wait(timeout=10)
        monitor = subprocess.Popen([args.bin / 'xjadeo.exe', '-J', '-R', str(clip)],
            env=env, cwd=output, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            time.sleep(1)
            if monitor.poll() is not None:
                raise RuntimeError('Native video monitor exited before rendering')
            monitor.stdin.write(b'seek 12\n')
            monitor.stdin.flush()
            time.sleep(0.5)
            response, _ = monitor.communicate(b'get position\nquit\n', timeout=20)
            (output / 'monitor.log').write_bytes(response)
            if monitor.returncode or b'position=12' not in response:
                raise RuntimeError(f'Video monitor remote seek failed; see {output / "monitor.log"}')
            results['monitor-render-and-seek'] = 'passed'
        finally:
            if monitor.poll() is None:
                monitor.kill()
                monitor.wait(timeout=10)
        print(json.dumps(results), flush=True)
    finally:
        (output / 'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
