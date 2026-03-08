"""
test_flat_template.py — Validate a generated flat AC autocross track project.

Usage:
    # Generate project then test it:
    python test_flat_template.py

    # Test an existing project (skip generation):
    python test_flat_template.py --no-generate

    # Test a specific track name / dimensions:
    python test_flat_template.py --name mytrack --width 150 --length 100

    # Specify blender path:
    python test_flat_template.py --blender /snap/bin/blender
"""

import argparse
import configparser
import glob
import json
import math
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import unittest
import zlib

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GENERATED_DIR = os.path.join(SCRIPT_DIR, 'generated')

# Filled in by main() before running tests
TEST_NAME   = 'test_flat_01'
TEST_WIDTH  = 120.0
TEST_LENGTH = 80.0
BLENDER_EXE = None
GENERATE    = True


def find_blender():
    blender = shutil.which('blender')
    if blender:
        return blender
    system = platform.system()
    if system == 'Windows':
        patterns = [r'C:\Program Files\Blender Foundation\Blender*\blender.exe']
    elif system == 'Darwin':
        patterns = ['/Applications/Blender.app/Contents/MacOS/Blender']
    else:
        patterns = [
            '/snap/bin/blender',
            os.path.expanduser('~/snap/blender/current/usr/bin/blender'),
            '/var/lib/flatpak/exports/bin/org.blender.Blender',
            '/usr/local/bin/blender',
        ]
    for pattern in patterns:
        matches = glob.glob(pattern)
        if matches:
            return sorted(matches)[-1]
    return None


def is_valid_png(path):
    """Return True if the file starts with the PNG signature."""
    PNG_SIG = b'\x89PNG\r\n\x1a\n'
    try:
        with open(path, 'rb') as f:
            return f.read(8) == PNG_SIG
    except OSError:
        return False


# ── File / text tests ─────────────────────────────────────────────────────────

class TestFileStructure(unittest.TestCase):
    """All required files and directories exist."""

    def setUp(self):
        self.root = os.path.join(GENERATED_DIR, TEST_NAME)
        self.ac   = os.path.join(self.root, TEST_NAME)
        self.bl   = os.path.join(self.root, 'blender')

    def _path(self, *parts):
        return os.path.join(self.root, *parts)

    def test_blender_dir_exists(self):
        self.assertTrue(os.path.isdir(self.bl), f'missing: {self.bl}')

    def test_blend_file_exists(self):
        p = os.path.join(self.bl, f'{TEST_NAME}.blend')
        self.assertTrue(os.path.isfile(p), f'missing: {p}')

    def test_fbx_file_exists(self):
        p = os.path.join(self.bl, f'{TEST_NAME}.fbx')
        self.assertTrue(os.path.isfile(p), f'missing: {p}')

    def test_ac_data_dir_exists(self):
        self.assertTrue(os.path.isdir(self.ac), f'missing: {self.ac}')

    def test_surfaces_ini_exists(self):
        p = os.path.join(self.ac, 'data', 'surfaces.ini')
        self.assertTrue(os.path.isfile(p), f'missing: {p}')

    def test_map_ini_exists(self):
        p = os.path.join(self.ac, 'data', 'map.ini')
        self.assertTrue(os.path.isfile(p), f'missing: {p}')

    def test_ext_config_ini_exists(self):
        p = os.path.join(self.ac, 'extension', 'ext_config.ini')
        self.assertTrue(os.path.isfile(p), f'missing: {p}')

    def test_ui_track_json_exists(self):
        p = os.path.join(self.ac, 'ui', 'ui_track.json')
        self.assertTrue(os.path.isfile(p), f'missing: {p}')

    def test_preview_png_exists(self):
        p = os.path.join(self.ac, 'ui', 'preview.png')
        self.assertTrue(os.path.isfile(p), f'missing: {p}')

    def test_outline_png_exists(self):
        p = os.path.join(self.ac, 'ui', 'outline.png')
        self.assertTrue(os.path.isfile(p), f'missing: {p}')

    def test_kn5_placeholder_exists(self):
        p = os.path.join(self.ac, f'{TEST_NAME}.kn5')
        self.assertTrue(os.path.isfile(p), f'missing: {p}')


class TestSurfacesIni(unittest.TestCase):
    """surfaces.ini has required physics surface entries."""

    def setUp(self):
        path = os.path.join(GENERATED_DIR, TEST_NAME, TEST_NAME,
                            'data', 'surfaces.ini')
        self.cfg = configparser.ConfigParser()
        self.cfg.read(path)

    def test_surface0_section_exists(self):
        self.assertIn('SURFACE_0', self.cfg)

    def test_surface0_key_road(self):
        self.assertEqual(self.cfg.get('SURFACE_0', 'KEY'), 'ROAD')

    def test_surface0_friction(self):
        self.assertAlmostEqual(float(self.cfg.get('SURFACE_0', 'FRICTION')), 0.98)

    def test_surface0_is_valid_track(self):
        self.assertEqual(self.cfg.get('SURFACE_0', 'IS_VALID_TRACK'), '1')

    def test_surface1_section_exists(self):
        self.assertIn('SURFACE_1', self.cfg)

    def test_surface1_key_grass(self):
        self.assertEqual(self.cfg.get('SURFACE_1', 'KEY'), 'Grass')

    def test_surface1_is_valid_track_false(self):
        self.assertEqual(self.cfg.get('SURFACE_1', 'IS_VALID_TRACK'), '0')


class TestMapIni(unittest.TestCase):
    """map.ini dimensions match road parameters."""

    def setUp(self):
        path = os.path.join(GENERATED_DIR, TEST_NAME, TEST_NAME,
                            'data', 'map.ini')
        self.cfg = configparser.ConfigParser()
        self.cfg.read(path)

    def test_width_matches(self):
        w = float(self.cfg.get('PARAMETERS', 'WIDTH'))
        self.assertAlmostEqual(w, TEST_WIDTH, places=0)

    def test_height_matches(self):
        h = float(self.cfg.get('PARAMETERS', 'HEIGHT'))
        self.assertAlmostEqual(h, TEST_LENGTH, places=0)

    def test_x_offset_is_half_width(self):
        x = float(self.cfg.get('PARAMETERS', 'X_OFFSET'))
        self.assertAlmostEqual(x, TEST_WIDTH / 2, places=2)

    def test_z_offset_is_half_length(self):
        z = float(self.cfg.get('PARAMETERS', 'Z_OFFSET'))
        self.assertAlmostEqual(z, TEST_LENGTH / 2, places=2)


class TestUiTrackJson(unittest.TestCase):
    """ui_track.json has required metadata fields."""

    def setUp(self):
        path = os.path.join(GENERATED_DIR, TEST_NAME, TEST_NAME,
                            'ui', 'ui_track.json')
        with open(path, 'r') as f:
            self.raw = f.read()

    def test_name_field_present(self):
        self.assertRegex(self.raw, r'"name"\s*:', 'missing "name" key')

    def test_name_field_not_empty(self):
        m = re.search(r'"name"\s*:\s*"([^"]*)"', self.raw)
        self.assertIsNotNone(m, 'could not find "name" value')
        self.assertTrue(len(m.group(1)) > 0, '"name" value is empty')

    def test_description_field_present(self):
        self.assertRegex(self.raw, r'"description"\s*:', 'missing "description" key')

    def test_pitboxes_field_present(self):
        self.assertRegex(self.raw, r'"pitboxes"\s*:', 'missing "pitboxes" key')


class TestExtConfigIni(unittest.TestCase):
    """ext_config.ini has required sections."""

    def setUp(self):
        path = os.path.join(GENERATED_DIR, TEST_NAME, TEST_NAME,
                            'extension', 'ext_config.ini')
        self.cfg = configparser.ConfigParser()
        self.cfg.read(path)

    def test_lighting_section(self):
        self.assertIn('LIGHTING', self.cfg)

    def test_grass_fx_section(self):
        self.assertIn('GRASS_FX', self.cfg)

    def test_grass_materials(self):
        self.assertEqual(self.cfg.get('GRASS_FX', 'GRASS_MATERIALS'), 'Grass')


class TestPngFiles(unittest.TestCase):
    """PNG placeholder files are valid."""

    def _ui(self, filename):
        return os.path.join(GENERATED_DIR, TEST_NAME, TEST_NAME, 'ui', filename)

    def test_preview_png_valid(self):
        self.assertTrue(is_valid_png(self._ui('preview.png')),
                        'preview.png is not a valid PNG')

    def test_outline_png_valid(self):
        self.assertTrue(is_valid_png(self._ui('outline.png')),
                        'outline.png is not a valid PNG')

    def test_preview_png_not_empty(self):
        size = os.path.getsize(self._ui('preview.png'))
        self.assertGreater(size, 100, 'preview.png is too small to be a real PNG')

    def test_outline_png_not_empty(self):
        size = os.path.getsize(self._ui('outline.png'))
        self.assertGreater(size, 100, 'outline.png is too small to be a real PNG')


# ── Blender scene tests ───────────────────────────────────────────────────────

def run_blender_scene_check():
    """Invoke Blender to run _check_flat_scene.py on the .blend file.

    Returns list of {name, ok, msg} dicts, or raises on failure.
    """
    blend_path  = os.path.join(GENERATED_DIR, TEST_NAME, 'blender', f'{TEST_NAME}.blend')
    check_script = os.path.join(SCRIPT_DIR, '_check_flat_scene.py')

    if not os.path.isfile(blend_path):
        raise FileNotFoundError(f'Blend file not found: {blend_path}')
    if not BLENDER_EXE:
        raise RuntimeError('Blender not found — set BLENDER_EXE or pass --blender')

    cmd = [
        BLENDER_EXE, '--background', blend_path,
        '--python', check_script,
        '--',
        '--width',  str(TEST_WIDTH),
        '--length', str(TEST_LENGTH),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    output = result.stdout + result.stderr

    # Find the JSON line
    for line in output.splitlines():
        if line.startswith('SCENE_CHECK_RESULTS:'):
            data = json.loads(line[len('SCENE_CHECK_RESULTS:'):])
            return data['results']

    raise RuntimeError(
        f'No SCENE_CHECK_RESULTS found in Blender output.\n'
        f'Exit code: {result.returncode}\n'
        f'--- stdout ---\n{result.stdout}\n'
        f'--- stderr ---\n{result.stderr}'
    )


_scene_results = None  # cached so Blender only runs once

def get_scene_results():
    global _scene_results
    if _scene_results is None:
        _scene_results = run_blender_scene_check()
    return _scene_results


class TestBlenderScene(unittest.TestCase):
    """Blender scene meets all template_requirements.md specifications."""

    _results = None
    _error   = None

    @classmethod
    def setUpClass(cls):
        try:
            cls._results = run_blender_scene_check()
        except Exception as e:
            cls._error = str(e)

    def _get(self, name):
        """Return a check result dict, or None if Blender didn't run."""
        if self._error:
            self.skipTest(f'Blender unavailable: {self._error}')
        for r in self._results:
            if r['name'] == name:
                return r
        return None

    def _ok(self, check_name):
        r = self._get(check_name)
        if r is None:
            self.fail(f'Scene check not returned by Blender: {check_name!r}')
        if not r['ok']:
            self.fail(f'{check_name}: {r["msg"]}' if r['msg'] else check_name)

    # ── Catchall: fails if ANY check failed ───────────────────────────────────

    def test_all_scene_checks_pass(self):
        """No scene check results may be False."""
        if self._error:
            self.fail(f'Blender scene check failed to run:\n{self._error}')
        failures = [(r['name'], r['msg']) for r in self._results if not r['ok']]
        if failures:
            lines = '\n'.join(f'  FAIL: {n}: {m}' for n, m in failures)
            self.fail(f'{len(failures)} scene check(s) failed:\n{lines}')

    # ── Required objects ──────────────────────────────────────────────────────

    def test_1road0_exists(self):
        self._ok('1ROAD0 exists')

    def test_1grass0_exists(self):
        self._ok('1GRASS0 exists')

    def test_1wall0_exists(self):
        self._ok('1WALL0 exists')

    def test_terrain_exists(self):
        self._ok('Terrain exists')

    def test_cone_template_exists(self):
        self._ok('AC_POBJECT_MovableCone exists')

    def test_ac_pit_0_exists(self):
        self._ok('AC_PIT_0 exists')

    def test_ac_start_0_exists(self):
        self._ok('AC_START_0 exists')

    def test_ac_hotlap_start_0_exists(self):
        self._ok('AC_HOTLAP_START_0 exists')

    def test_ac_time_0_l_exists(self):
        self._ok('AC_TIME_0_L exists')

    def test_ac_time_0_r_exists(self):
        self._ok('AC_TIME_0_R exists')

    def test_ac_time_1_l_exists(self):
        self._ok('AC_TIME_1_L exists')

    def test_ac_time_1_r_exists(self):
        self._ok('AC_TIME_1_R exists')

    # ── Road dimensions ───────────────────────────────────────────────────────

    def test_road_width(self):
        self._ok('1ROAD0 width matches --width')

    def test_road_length(self):
        self._ok('1ROAD0 length matches --length')

    def test_road_material(self):
        self._ok('1ROAD0 material is ROAD')

    # ── Grass larger than road ────────────────────────────────────────────────

    def test_grass_wider_than_road(self):
        self._ok('1GRASS0 wider than road')

    def test_grass_longer_than_road(self):
        self._ok('1GRASS0 longer than road')

    # ── Wall encloses road ────────────────────────────────────────────────────

    def test_wall_encloses_road_width(self):
        self._ok('1WALL0 encloses road width')

    def test_wall_encloses_road_length(self):
        self._ok('1WALL0 encloses road length')

    # ── Terrain below road ────────────────────────────────────────────────────

    def test_terrain_below_road(self):
        self._ok('Terrain Z < 0 (below road)')

    # ── Cone geometry ─────────────────────────────────────────────────────────

    def test_cone_hide_render(self):
        self._ok('AC_POBJECT_MovableCone hide_render')

    def test_cone_base_at_z0(self):
        self._ok('Cone base at Z>=0 (origin at base centre)')

    def test_cone_height(self):
        self._ok('Cone height in valid range (0.3–1.0m)')

    def test_cone_base_radius(self):
        self._ok('Cone base_r >= 0.1397m')

    # ── Spawn marker rotation ─────────────────────────────────────────────────

    def test_ac_pit_rotation(self):
        self._ok('AC_PIT_0 rotation.x ~ -pi/2')

    def test_ac_start_rotation(self):
        self._ok('AC_START_0 rotation.x ~ -pi/2')

    def test_ac_hotlap_rotation(self):
        self._ok('AC_HOTLAP_START_0 rotation.x ~ -pi/2')

    # ── Materials ─────────────────────────────────────────────────────────────

    def test_material_road(self):
        self._ok("material 'ROAD' exists")

    def test_material_grass(self):
        self._ok("material 'Grass' exists")

    def test_material_concrete_wall(self):
        self._ok("material 'ConcreteWall' exists")

    def test_material_cone(self):
        self._ok("material 'Cone' exists")

    def test_material_null(self):
        self._ok("material 'Null' exists")
        # Note: use_nodes=False is deprecated in Blender 5.0 — not checked.

    # ── Material slots ────────────────────────────────────────────────────────

    def test_all_meshes_have_material_slots(self):
        self._ok('All MESH objects have material slots')


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    global TEST_NAME, TEST_WIDTH, TEST_LENGTH, BLENDER_EXE, GENERATE

    p = argparse.ArgumentParser(description='Test a generated flat AC track project')
    p.add_argument('--name',        default='test_flat_01',  help='Track name to test')
    p.add_argument('--width',  type=float, default=120.0,    help='Road width (default 120)')
    p.add_argument('--length', type=float, default=80.0,     help='Road length (default 80)')
    p.add_argument('--blender',     default=None,            help='Blender executable path')
    p.add_argument('--no-generate', action='store_true',     help='Skip project generation')
    p.add_argument('--verbosity',   type=int, default=2)

    # Allow unittest args after --
    my_args, remaining = p.parse_known_args()

    TEST_NAME   = my_args.name
    TEST_WIDTH  = my_args.width
    TEST_LENGTH = my_args.length
    GENERATE    = not my_args.no_generate
    BLENDER_EXE = my_args.blender or find_blender()

    dest_dir = os.path.join(GENERATED_DIR, TEST_NAME)

    # ── Optionally generate the project first ─────────────────────────────────
    if GENERATE:
        if os.path.exists(dest_dir):
            print(f'[setup] Project already exists, skipping generation: {dest_dir}')
        else:
            print(f'[setup] Generating project: {TEST_NAME} ({TEST_WIDTH}m x {TEST_LENGTH}m) …')
            cmd = [
                sys.executable,
                os.path.join(SCRIPT_DIR, 'new_flat_project.py'),
                TEST_NAME,
                '--width',  str(TEST_WIDTH),
                '--length', str(TEST_LENGTH),
            ]
            if BLENDER_EXE:
                cmd += ['--blender', BLENDER_EXE]
            r = subprocess.run(cmd)
            if r.returncode != 0:
                print('ERROR: new_flat_project.py failed — aborting tests.')
                sys.exit(1)
    else:
        if not os.path.exists(dest_dir):
            print(f'ERROR: Project not found: {dest_dir}  (run without --no-generate)')
            sys.exit(1)

    # ── Run unittest ──────────────────────────────────────────────────────────
    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()
    for cls in (TestFileStructure, TestSurfacesIni, TestMapIni,
                TestUiTrackJson, TestExtConfigIni, TestPngFiles,
                TestBlenderScene):
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=my_args.verbosity)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == '__main__':
    main()
