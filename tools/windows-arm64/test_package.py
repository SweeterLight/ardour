"""Dependency audit regressions; runs without Windows or a compiler."""
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('arm64_package', Path(__file__).with_name('package.py'))
PACKAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKAGE)


def write_pe(path, imports=(), delay_imports=(), machine=0xAA64):
    """Construct a minimal PE32+ image with a normal and delay import table."""
    data = bytearray(4096)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 60, 128)
    data[128:132] = b'PE\0\0'
    struct.pack_into('<HH', data, 132, machine, 1)
    struct.pack_into('<H', data, 148, 240)
    struct.pack_into('<H', data, 152, 0x20B)
    struct.pack_into('<Q', data, 176, 0x140000000)
    struct.pack_into('<I', data, 212, 512)
    struct.pack_into('<I', data, 260, 16)
    data[392:400] = b'.rdata\0\0'
    struct.pack_into('<IIII', data, 400, 3584, 4096, 3584, 512)
    string_pos = 2048
    for directory, names, raw, size in ((1, imports, 512, 20), (13, delay_imports, 1024, 32)):
        if not names:
            continue
        rva = raw + 4096 - 512
        struct.pack_into('<II', data, 264 + directory * 8, rva, size * (len(names) + 1))
        for i, name in enumerate(names):
            encoded = name.encode() + b'\0'
            data[string_pos:string_pos + len(encoded)] = encoded
            name_rva = string_pos + 4096 - 512
            if directory == 1:
                struct.pack_into('<IIIII', data, raw + size * i, 1, 0, 0, name_rva, 1)
            else:
                struct.pack_into('<IIIIIIII', data, raw + size * i, 1, name_rva, 0, 1, 0, 0, 0, 0)
            string_pos += len(encoded)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


class DependencyAuditTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_normal_and_delay_imports(self):
        path = self.root / 'test.exe'
        write_pe(path, ('KERNEL32.dll', 'Runtime.dll'), ('PluginHelper.dll',))
        machine, imports = PACKAGE.pe_info(path)
        self.assertEqual(machine, 0xAA64)
        self.assertEqual(imports, ['kernel32.dll', 'pluginhelper.dll', 'runtime.dll'])

    def audit(self):
        with patch.object(PACKAGE, 'require_features', return_value=[]):
            return PACKAGE.audit(self.root)

    def test_rejects_x64_image(self):
        write_pe(self.root / 'bin/Ardour.exe', machine=0x8664)
        with self.assertRaisesRegex(ValueError, 'expected ARM64'):
            self.audit()

    def test_rejects_transitive_missing_runtime(self):
        write_pe(self.root / 'bin/Ardour.exe', ('audio.dll',))
        write_pe(self.root / 'bin/audio.dll', ('missing.dll',))
        with self.assertRaisesRegex(ValueError, 'missing DLL dependency missing.dll'):
            self.audit()

    def test_accepts_complete_graph_and_windows_api_sets(self):
        write_pe(self.root / 'bin/Ardour.exe', ('audio.dll', 'kernel32.dll'))
        write_pe(self.root / 'bin/audio.dll', ('api-ms-win-crt-runtime-l1-1-0.dll',))
        features, binaries = self.audit()
        self.assertEqual(len(binaries), 2)
        self.assertTrue(all(len(binary['sha256']) == 64 for binary in binaries))

    def test_rejects_msys_even_when_bundled(self):
        write_pe(self.root / 'bin/Ardour.exe', ('msys-2.0.dll',))
        write_pe(self.root / 'bin/msys-2.0.dll')
        with self.assertRaisesRegex(ValueError, 'forbidden MSYS/Cygwin'):
            self.audit()

    def test_required_features_are_not_silently_omitted(self):
        write_pe(self.root / 'bin/Ardour.exe')
        with self.assertRaisesRegex(ValueError, 'Required features missing:.*VST3 scanner.*video server'):
            PACKAGE.require_features(self.root)

    def test_corrupt_image_is_rejected(self):
        path = self.root / 'broken.exe'
        path.write_bytes(b'not an executable')
        with self.assertRaisesRegex(ValueError, 'not a PE executable'):
            PACKAGE.pe_info(path)

    def test_waf_cache_with_non_python_keys(self):
        cache = self.root / 'build/c4che/_cache.py'
        cache.parent.mkdir(parents=True)
        definitions = ['LV2_SUPPORT', 'WINDOWS_VST_SUPPORT', 'VST3_SUPPORT', 'WITH_ASIO',
                       'ENABLE_NLS', 'HAVE_LRDF', 'HAVE_SUIL', 'HAVE_USB', 'HAVE_WEBSOCKETS', 'PTFORMAT']
        cache.write_text("WINDOWS_ARCH = 'arm64'\nBACKENDS = ['portaudio', 'jack', 'dummy']\n"
                         "INCLUDES_BOOST/PTR_CONTAINER/PTR_LIST.HPP = ['']\n"
                         f"DEFINES = {[key + '=1' for key in definitions]!r}\n")
        self.assertEqual(PACKAGE.validate_config(self.root), sorted(definitions))
        cache.write_text(cache.read_text().replace('WITH_ASIO=1', 'WITH_ASIO=0'))
        with self.assertRaisesRegex(ValueError, 'Required build options missing: WITH_ASIO'):
            PACKAGE.validate_config(self.root)


if __name__ == '__main__':
    unittest.main()
