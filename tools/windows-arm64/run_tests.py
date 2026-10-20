#!/usr/bin/env python3
"""Run native Ardour tests with configuration and temporary data isolated."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import wave
import array


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', required=True, type=Path)
    parser.add_argument('--runtime', required=True, type=Path)
    parser.add_argument('--pbd-only', action='store_true')
    parser.add_argument('--smoke-only', action='store_true', help='Diagnose session recording without repeating unit tests')
    parser.add_argument('--core-only', action='store_true')
    parser.add_argument('--component', action='append', help='Select a named unit test with --core-only')
    args = parser.parse_args()
    if args.component and not args.core_only:
        parser.error('--component requires --core-only')
    if os.name != 'nt':
        parser.error('Use native Windows ARM64 Python')
    repo = Path(__file__).resolve().parents[2]
    work = args.work.resolve()
    build = work / 'build'
    testdir = work / 'tests'
    testdir.mkdir(exist_ok=True)
    data = repo / 'share' if args.core_only else ((work / 'stage/share/ardour9')
        if args.pbd_only else next((work / 'stage/share').glob('ardour[0-9]*')))
    modules = work / 'stage/lib' / data.name
    env = os.environ.copy()
    dll_dirs = sorted({str(p.parent) for p in build.rglob('*.dll')})
    env['PATH'] = os.pathsep.join([str(work / 'stack/bin'), str(args.runtime / 'bin'),
                                 *dll_dirs, env.get('PATH', '')])
    for name, value in {
        'ARDOUR_DLL_PATH': modules, 'ARDOUR_DATA_PATH': data, 'ARDOUR_CONFIG_PATH': data,
        'ARDOUR_TEST_PATH': repo / 'libs/ardour/test', 'PBD_TEST_PATH': repo / 'libs/pbd/test',
        'EVORAL_TEST_PATH': repo / 'libs/evoral/test/testdata',
        'MIDIPP_TEST_PATH': repo / 'share/patchfiles',
        'XDG_CONFIG_HOME': testdir / 'config', 'XDG_CACHE_HOME': testdir / 'cache',
        'TMP': testdir / 'tmp', 'TEMP': testdir / 'tmp', 'TMPDIR': testdir / 'tmp',
    }.items():
        env[name] = str(value)
    for name in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'TMP'):
        Path(env[name]).mkdir(parents=True, exist_ok=True)
    env['LANG'] = 'C'
    plugins = testdir / 'test-plugins'
    plugins.mkdir(exist_ok=True)
    env['VST_PATH'] = env['VST3_PATH'] = str(plugins)
    unit_tests = (
        ('pbd-threading', build / 'libs/pbd/run-tests.exe'),
        ('temporal', build / 'libs/temporal/run-tests.exe'),
        ('evoral-midi', build / 'libs/evoral/run-tests.exe'),
        ('midi-patches', build / 'libs/midi++2/run-tests.exe'),
        ('audiographer', build / 'libs/audiographer/run-tests.exe'),
        ('arm64-dsp', build / 'libs/ardour/test_fpu.exe'),
        ('midi-clock', build / 'libs/ardour/test_midi_clock.exe'),
        ('audio-engine', build / 'libs/ardour/test_audio_engine.exe'),
    )
    if args.core_only:
        env['ARDOUR_BACKEND_PATH'] = os.pathsep.join(str(p) for p in (build / 'libs/backends').iterdir() if p.is_dir())
        env['ARDOUR_PANNER_PATH'] = os.pathsep.join(str(p) for p in (build / 'libs/panners').iterdir() if p.is_dir())
    results = []

    def run(name, command, timeout=300):
        started = time.monotonic()
        print(f'Testing {name}', flush=True)
        result = {'name': name, 'command': [str(x) for x in command]}
        results.append(result)
        try:
            logfile = testdir / (name + '.log')
            with logfile.open('wb') as log:
                completed = subprocess.run(command, cwd=testdir, env=env, stdout=log,
                                           stderr=subprocess.STDOUT, timeout=timeout)
            result.update(returncode=completed.returncode, seconds=round(time.monotonic() - started, 2),
                          status='passed' if completed.returncode == 0 else 'failed')
            if completed.returncode:
                raise RuntimeError(f'{name} failed ({completed.returncode}); see {testdir / (name + ".log")}')
            return logfile.read_bytes()
        except Exception:
            result.setdefault('status', 'failed')
            raise

    try:
        if args.pbd_only:
            run('pbd-threading', [build / 'libs/pbd/run-tests.exe'])
            return
        if args.smoke_only:
            session = testdir / ('session-smoke-diagnostic-' + time.strftime('%Y%m%d-%H%M%S'))
            new_session = next((work / 'stage/bin').glob('ardour*-new_session.exe'))
            run('create-session', [new_session, '-s', '48000', session, 'session-smoke'])
            lua = next((work / 'stage/bin').glob('ardour*-lua.exe'))
            run('record-play-effect', [lua, repo / 'tools/windows-arm64/session_smoke.lua', session], timeout=90)
            return
        if args.core_only:
            for name, binary in unit_tests[1:]:
                if not args.component or name in args.component:
                    run(name, [binary])
            if args.component and set(args.component) - {name for name, _ in unit_tests[1:]}:
                raise RuntimeError('Unknown core test component')
            return
        run('packaging-regressions', [sys.executable, repo / 'tools/windows-arm64/test_package.py'])
        run('audio-capabilities', [sys.executable, repo / 'tools/windows-arm64/check_audio.py',
            '--stack', work / 'stack', '--runtime', args.runtime,
            '--output', work / 'portaudio-capabilities.json', '--stream'])
        allocator_test = testdir / 'allocator-test.exe'
        run('allocator-compile', [args.runtime / 'bin/clang++.exe', '-std=c++17',
            '-I' + str(repo / 'libs/pbd'), repo / 'tools/windows-arm64/test_allocator.cc',
            '-o', allocator_test])
        run('allocator', [allocator_test])
        mismatch_env = dict(env, CC=str(args.runtime / 'bin/clang.exe'),
                            CXX=str(args.runtime / 'bin/clang++.exe'),
                            AR=str(args.runtime / 'bin/llvm-ar.exe'))
        mismatch = subprocess.run([sys.executable, repo / 'waf', 'configure',
            '--dist-target=mingw', '--windows-arch=x64',
            '--check-c-compiler=clang', '--check-cxx-compiler=clang++',
            '--out=' + str(work / 'mismatched-target')], cwd=repo, env=mismatch_env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90)
        (testdir / 'target-mismatch.log').write_bytes(mismatch.stdout)
        if not mismatch.returncode or b'does not match compiler target arm64' not in mismatch.stdout:
            raise RuntimeError('Waf did not reject an x64 target with the ARM64 compiler')
        results.append({'name': 'target-mismatch-rejection', 'status': 'passed'})
        for name, binary in unit_tests:
            run(name, [binary])
        run('native-video', [sys.executable, repo / 'tools/windows-arm64/test_video.py',
            '--bin', work / 'stack/bin', '--runtime-bin', args.runtime / 'bin',
            '--output', testdir / 'video'], timeout=120)
        run('jack-connectivity', [sys.executable, repo / 'tools/windows-arm64/test_jack.py',
            '--bin', args.runtime / 'bin', '--output', testdir / 'jack'], timeout=90)
        new_session = next((work / 'stage/bin').glob('ardour*-new_session.exe'))
        session = testdir / ('session-smoke-' + time.strftime('%Y%m%d-%H%M%S'))
        run('create-session', [new_session, '-s', '48000', session, 'session-smoke'])
        lua = next((work / 'stage/bin').glob('ardour*-lua.exe'))
        test_plugin = plugins / 'ARM64TestGain.dll'
        run('vst2-fixture-compile', [args.runtime / 'bin/clang++.exe', '-std=c++17', '-O2', '-shared',
            '-I' + str(repo / 'libs/ardour/ardour'), repo / 'tools/windows-arm64/test_vst2.cc',
            '-o', test_plugin])
        env['VST_PATH'] = str(plugins)
        scanner = next(modules.glob('ardour-vst-scanner.exe'))
        # PluginManager discovers Windows paths with backslashes.  The scanner
        # caches the exact path spelling, so use that spelling for direct tests.
        scanned = run('vst2-scanner', [scanner, '-f', '-v', str(test_plugin).replace('/', '\\')])
        if b'ARM64 Validation Gain' not in scanned:
            raise RuntimeError('Native VST2 scanner did not discover the test effect')
        run('vst3-scanner-startup', [modules / 'ardour-vst3-scanner.exe', '--version'])
        vst3_bundle = plugins / 'ARM64TestGain.vst3'
        vst3_module = vst3_bundle / 'Contents/arm64-win/ARM64TestGain.vst3'
        vst3_module.parent.mkdir(parents=True, exist_ok=True)
        run('vst3-fixture-compile', [args.runtime / 'bin/clang++.exe', '-std=c++17', '-O2', '-shared',
            '-I' + str(repo / 'libs/vst3'), repo / 'tools/windows-arm64/test_vst3.cc', '-o', vst3_module])
        env['VST3_PATH'] = str(plugins)
        scanned = run('vst3-scanner', [modules / 'ardour-vst3-scanner.exe', '-f', '-v',
                                     str(vst3_bundle).replace('/', '\\')])
        if b'ARM64 Validation Gain VST3' not in scanned:
            raise RuntimeError('Native VST3 scanner did not discover the arm64-win test bundle')
        plugin_script = testdir / 'plugin.lua'
        plugin_script.write_text('assert(create_session(arg[1], "plugin-smoke", 48000))\n'
            'local p = ARDOUR.LuaAPI.new_plugin(Session, "ARM64 Validation Gain", ARDOUR.PluginType.Windows_VST, "")\n'
            'assert(not p:isnil(), "Native VST2 plugin could not be instantiated")\n'
            'assert(Session:master_out():add_processor_by_index(p, 0, nil, true) == 0)\n'
            'p:activate()\nsleep(0.5)\nSession:save_state("")\nclose_session()\n'
            'assert(load_session(arg[1], "plugin-smoke"))\nsleep(0.5)\nclose_session()\n'
            'print("ARM64_VST2_HOSTING_PASSED")\n', encoding='utf-8')
        hosted = run('vst2-hosting', [lua, plugin_script, testdir / ('plugin-smoke-' + time.strftime('%Y%m%d-%H%M%S'))])
        if b'ARM64_VST2_HOSTING_PASSED' not in hosted:
            raise RuntimeError('Native VST2 hosting fixture did not complete')
        plugin_script.write_text(plugin_script.read_text(encoding='utf-8')
            .replace('ARM64 Validation Gain', 'ARM64 Validation Gain VST3')
            .replace('PluginType.Windows_VST', 'PluginType.VST3')
            .replace('VST2', 'VST3'), encoding='utf-8')
        hosted = run('vst3-hosting', [lua, plugin_script, testdir / ('vst3-smoke-' + time.strftime('%Y%m%d-%H%M%S'))])
        if b'ARM64_VST3_HOSTING_PASSED' not in hosted:
            raise RuntimeError('Native VST3 hosting fixture did not complete')
        script = testdir / 'reopen.lua'
        script.write_text('assert(load_session(arg[1], "session-smoke"))\n'
                          'Session:save_state("")\nclose_session()\n', encoding='utf-8')
        run('reopen-session', [lua, script, session])
        output = run('record-play-effect', [lua, repo / 'tools/windows-arm64/session_smoke.lua', session])
        if b'ARM64_RECORD_PLAY_RELOAD_EFFECT_PASSED' not in output:
            raise RuntimeError('Session recording/effect fixture did not complete')
        exporter = next((work / 'stage/bin').glob('ardour*-export.exe'))
        exported = testdir / 'session-export.wav'
        run('wav-export', [exporter, '-o', exported, session, 'session-smoke'])
        with wave.open(str(exported), 'rb') as audio:
            if (audio.getnchannels(), audio.getframerate(), audio.getsampwidth()) != (2, 48000, 2):
                raise RuntimeError('Export did not produce stereo 48 kHz 16-bit PCM')
            samples = array.array('h', audio.readframes(audio.getnframes()))
            if len(samples) < 48000 or max(map(abs, samples), default=0) < 100:
                raise RuntimeError('Synthetic recording/export was too short or silent')
        print(f'Passed {len(results)} native test commands. Logs: {testdir}', flush=True)
    finally:
        report = ('smoke-results.json' if args.smoke_only else
                  'core-results.json' if args.core_only else
                  'pbd-results.json' if args.pbd_only else 'test-results.json')
        (work / report).write_text(json.dumps(results, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
