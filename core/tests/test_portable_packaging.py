"""Exercise the portable spec without importing Windows or GUI libraries."""
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[2]


class PortablePackagingTests(unittest.TestCase):
    def setUp(self):
        self.calls = {}

        def analysis(scripts, **options):
            self.calls['analysis'] = (scripts, options)
            return SimpleNamespace(
                pure=['module'], scripts=scripts, datas=['assets'],
                binaries=[('ucrtbase.dll', 'system', 'BINARY'),
                          ('api-ms-win-core.dll', 'system', 'BINARY'),
                          ('Qt6Core.dll', 'qt', 'BINARY')],
            )

        def capture(name):
            def call(*args, **options):
                self.calls[name] = (args, options)
                return name
            return call

        spec = ROOT / 'win_app' / 'build_portable.spec'
        exec(compile(spec.read_text(), spec.name, 'exec'), {
            'SPECPATH': str(spec.parent), 'Analysis': analysis,
            'PYZ': capture('pyz'), 'EXE': capture('exe'), 'COLLECT': capture('collect'),
        })

    def test_one_folder_build_runs_without_admin_or_extraction(self):
        scripts, analysis = self.calls['analysis']
        self.assertEqual(scripts, [str(ROOT / 'win_app' / 'portable_entry.py')])
        self.assertIn('kvm_bridge_win', analysis['hiddenimports'])
        self.assertEqual(analysis['runtime_hooks'], [str(ROOT / 'win_app' / 'hooks' / 'portable_storage.py')])
        args, options = self.calls['exe']
        self.assertIs(options['exclude_binaries'], True)
        self.assertIs(options['uac_admin'], False)
        self.assertIs(options['uac_uiaccess'], False)
        self.assertEqual(options['contents_directory'], '_internal')
        self.assertEqual(args[-1], [])
        self.assertNotIn('runtime_tmpdir', options)
        self.assertEqual(self.calls['collect'][1]['name'], 'Beamer-portable')

    def test_qt_dlls_stay_beside_exe_and_system_stubs_stay_out(self):
        args, _ = self.calls['collect']
        self.assertEqual(args[0], 'exe')
        self.assertEqual(args[1], [('Qt6Core.dll', 'qt', 'BINARY')])
        self.assertEqual(args[2], ['assets'])

    def test_dynamic_core_modules_and_locales_are_included(self):
        _, options = self.calls['analysis']
        self.assertIn('core.effects', options['hiddenimports'])
        self.assertTrue(any(destination == 'locales' for _, destination in options['datas']))

    def test_runtime_only_hook_is_not_an_installed_module_probe(self):
        from tools.import_probe import module_names
        self.assertNotIn('portable_storage', module_names('win'))
        self.assertNotIn('hooks.portable_storage', module_names('win'))
        _, options = self.calls['analysis']
        self.assertNotIn('portable_storage', options['hiddenimports'])


if __name__ == '__main__':
    unittest.main()
