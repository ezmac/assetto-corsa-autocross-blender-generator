"""
Tests for the pure-Python track-building logic in build_track.py.

Run from the project root:
    pytest tests/test_track_building.py -v

Integration tests (require Blender) are skipped unless BLENDER_EXE is found:
    pytest tests/test_track_building.py -v -m integration
"""

import glob
import json
import math
import os
import shutil
import sys
import tempfile

import pytest

# ── path setup ────────────────────────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from track_core import convert_editor_json as _convert_editor_json, get_dims_from_json, update_track_info

FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')
FLR_GANK_JSON = os.path.join(PROJECT_ROOT, 'flr-gank.json')


# ── helpers ───────────────────────────────────────────────────────────────────

def load_fixture(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return json.load(f)


def make_ui_track_dir(tmp_path, track_name, description="placeholder"):
    """Create the {tmp}/{name}/ui/ui_track.json structure update_track_info expects."""
    ui_dir = os.path.join(tmp_path, track_name, 'ui')
    os.makedirs(ui_dir)
    ui_path = os.path.join(ui_dir, 'ui_track.json')
    content = (
        '{\n'
        f'  "name": "{track_name}",\n'
        f'  "description": "{description}",\n'
        '  "tags": []\n'
        '}\n'
    )
    with open(ui_path, 'w') as f:
        f.write(content)
    return ui_path


# ══ TestConvertEditorJson ══════════════════════════════════════════════════════

class TestConvertEditorJson:

    def _convert(self, cones, scale=0.2, extra_auto=None):
        editor = {'cones': cones, 'scale': scale, 'siteW': 400, 'siteH': 500}
        auto = extra_auto or {}
        return _convert_editor_json(editor, auto)

    def test_standing_cone_position(self):
        result = self._convert([
            {'id': 's1', 'coneType': 'standing', 'x': 100, 'y': 200, 'rotation': 0}
        ])
        assert result['standing'] == [{'bx': 20.0, 'by': -40.0, 'type': 'standing', 'size': 1}]

    def test_y_axis_negated(self):
        # Y is flipped: by = -y * scale
        result = self._convert([
            {'id': 's1', 'coneType': 'standing', 'x': 0, 'y': 100, 'rotation': 0}
        ])
        assert result['standing'][0]['by'] == -20.0

    def test_pointer_facing_negated(self):
        # Editor rotation is CCW in screen (y-down); negate to get Blender facing_deg
        result = self._convert([
            {'id': 'p1', 'coneType': 'pointer', 'x': 10, 'y': 10,
             'rotation': math.pi / 2}  # 90° CCW in screen → -90° in Blender
        ])
        p = result['pointers'][0]
        assert abs(p['facing_deg'] - (-90.0)) < 0.1

    def test_pointer_zero_rotation(self):
        result = self._convert([
            {'id': 'p1', 'coneType': 'pointer', 'x': 10, 'y': 10, 'rotation': 0}
        ])
        assert result['pointers'][0]['facing_deg'] == 0.0

    def test_car_start_becomes_stage_cone_pos(self):
        result = self._convert([
            {'id': 'c1', 'coneType': 'car_start', 'x': 150, 'y': 150, 'rotation': 0}
        ])
        # bx = 150 * 0.2 = 30.0, by = -150 * 0.2 = -30.0
        assert result['stage_cone_pos'] == {'bx': 30.0, 'by': -30.0, 'facing_deg': 0.0}

    def test_car_start_centroid_of_multiple(self):
        result = self._convert([
            {'id': 'c1', 'coneType': 'car_start', 'x': 100, 'y': 0, 'rotation': 0},
            {'id': 'c2', 'coneType': 'car_start', 'x': 200, 'y': 0, 'rotation': 0},
        ])
        assert result['stage_cone_pos'] == {'bx': 30.0, 'by': 0.0, 'facing_deg': 0.0}  # mean of 20 and 40

    def test_noexport_skipped(self):
        result = self._convert([
            {'id': 'nx', 'coneType': 'standing', 'x': 50, 'y': 50, 'rotation': 0,
             'noExport': True},
        ])
        assert result['standing'] == []

    def test_timing_start_end_separated(self):
        result = self._convert([
            {'id': 'ts', 'coneType': 'timing_start', 'x': 10, 'y': 10, 'rotation': 0},
            {'id': 'te', 'coneType': 'timing_end',   'x': 20, 'y': 20, 'rotation': 0},
        ])
        assert len(result['timing_start']) == 1
        assert len(result['timing_end']) == 1
        assert result['timing_start'][0]['bx'] == pytest.approx(2.0)
        assert result['timing_end'][0]['bx'] == pytest.approx(4.0)

    def test_bounds_computed_from_all_cones(self):
        result = self._convert([
            {'id': 's1', 'coneType': 'standing', 'x': 0,   'y': 0,   'rotation': 0},
            {'id': 's2', 'coneType': 'standing', 'x': 100, 'y': 0,   'rotation': 0},
            {'id': 's3', 'coneType': 'standing', 'x': 100, 'y': 200, 'rotation': 0},
        ])
        b = result['bounds']
        assert b['xmin'] == pytest.approx(0.0)
        assert b['xmax'] == pytest.approx(20.0)
        assert b['ymin'] == pytest.approx(-40.0)
        assert b['ymax'] == pytest.approx(0.0)

    def test_gcp_cones_extracted(self):
        result = self._convert([
            {'id': 'g1', 'coneType': 'gcp', 'x': 50,  'y': 100, 'rotation': 0},
            {'id': 'g2', 'coneType': 'gcp', 'x': 300, 'y': 100, 'rotation': 0},
            {'id': 'g3', 'coneType': 'gcp', 'x': 300, 'y': 400, 'rotation': 0},
        ])
        assert len(result['gcp']) == 3
        assert result['gcp'][0] == {'bx': 10.0, 'by': -20.0}

    def test_gcp_dedup_drops_near_duplicate(self):
        # g1 and g1b are within 2m of each other — only g1 should survive
        result = self._convert([
            {'id': 'g1',  'coneType': 'gcp', 'x': 50,   'y': 100, 'rotation': 0},
            {'id': 'g1b', 'coneType': 'gcp', 'x': 50.5, 'y': 100, 'rotation': 0},  # 0.1m away
            {'id': 'g2',  'coneType': 'gcp', 'x': 300,  'y': 100, 'rotation': 0},
            {'id': 'g3',  'coneType': 'gcp', 'x': 300,  'y': 400, 'rotation': 0},
        ], scale=1.0)
        assert len(result['gcp']) == 3

    def test_gcp_capped_at_three(self):
        result = self._convert([
            {'id': 'g1', 'coneType': 'gcp', 'x': 0,   'y': 0,   'rotation': 0},
            {'id': 'g2', 'coneType': 'gcp', 'x': 100, 'y': 0,   'rotation': 0},
            {'id': 'g3', 'coneType': 'gcp', 'x': 100, 'y': 100, 'rotation': 0},
            {'id': 'g4', 'coneType': 'gcp', 'x': 0,   'y': 100, 'rotation': 0},
        ])
        assert len(result['gcp']) == 3

    def test_auto_data_scale_overrides_editor_scale(self):
        editor = {'cones': [
            {'id': 's1', 'coneType': 'standing', 'x': 100, 'y': 0, 'rotation': 0}
        ], 'scale': 0.2}
        auto = {'transform': {'scale': 0.5, 'ox': 0.0, 'oy': 0.0}}
        result = _convert_editor_json(editor, auto)
        assert result['standing'][0]['bx'] == pytest.approx(50.0)

    def test_auto_data_offset_applied(self):
        editor = {'cones': [
            {'id': 's1', 'coneType': 'standing', 'x': 0, 'y': 0, 'rotation': 0}
        ], 'scale': 1.0}
        auto = {'transform': {'scale': 1.0, 'ox': 5.0, 'oy': 3.0}}
        result = _convert_editor_json(editor, auto)
        assert result['standing'][0]['bx'] == pytest.approx(5.0)
        assert result['standing'][0]['by'] == pytest.approx(3.0)

    def test_full_fixture(self):
        editor = load_fixture('simple_editor.json')
        result = _convert_editor_json(editor, {})
        assert result['n_standing'] == 3
        assert result['n_pointer'] == 1
        assert len(result['gcp']) == 3
        assert result['stage_cone_pos'] is not None
        assert 'bounds' in result


# ══ TestGetDimsFromJson ════════════════════════════════════════════════════════

class TestGetDimsFromJson:

    def test_dims_from_simple_pipeline(self, tmp_path):
        src = os.path.join(FIXTURES, 'simple_pipeline.json')
        w, l = get_dims_from_json(src, padding=0.0)
        # bounds: xmin=0, xmax=10, ymin=0, ymax=20 → width=10, length=20
        assert w == pytest.approx(10.0)
        assert l == pytest.approx(20.0)

    def test_padding_adds_both_sides(self, tmp_path):
        src = os.path.join(FIXTURES, 'simple_pipeline.json')
        w0, l0 = get_dims_from_json(src, padding=0.0)
        w20, l20 = get_dims_from_json(src, padding=20.0)
        assert w20 == pytest.approx(w0 + 40.0)
        assert l20 == pytest.approx(l0 + 40.0)

    @pytest.mark.skipif(not os.path.isfile(FLR_GANK_JSON), reason="flr-gank.json not present")
    def test_dims_from_flr_gank(self):
        w, l = get_dims_from_json(FLR_GANK_JSON, padding=20.0)
        assert w > 0
        assert l > 0
        # Known values from flr-gank.json bounds + 40m padding
        assert w == pytest.approx(366.8, abs=1.0)
        assert l == pytest.approx(225.2, abs=1.0)

    def test_page_dims_override_bounds(self, tmp_path):
        data = {
            'transform': {'scale': 0.3048, 'page_w_pt': 792.0, 'page_h_pt': 612.0},
            'standing': [{'bx': 0, 'by': 0, 'type': 'standing', 'size': 1}],
        }
        p = os.path.join(str(tmp_path), 'page.json')
        with open(p, 'w') as f:
            json.dump(data, f)
        w, l = get_dims_from_json(p)
        assert w == pytest.approx(792.0 * 0.3048, abs=0.1)
        assert l == pytest.approx(612.0 * 0.3048, abs=0.1)

    def test_no_bounds_falls_back_to_cones(self, tmp_path):
        data = {
            'standing': [
                {'bx': 5.0, 'by': 5.0, 'type': 'standing', 'size': 1},
                {'bx': 55.0, 'by': 85.0, 'type': 'standing', 'size': 1},
            ],
            'pointers': [],
        }
        p = os.path.join(str(tmp_path), 'nobounds.json')
        with open(p, 'w') as f:
            json.dump(data, f)
        w, l = get_dims_from_json(p, padding=0.0)
        assert w == pytest.approx(50.0)
        assert l == pytest.approx(80.0)

    def test_empty_json_returns_defaults(self, tmp_path):
        p = os.path.join(str(tmp_path), 'empty.json')
        with open(p, 'w') as f:
            json.dump({}, f)
        w, l = get_dims_from_json(p)
        assert w == 120.0
        assert l == 80.0


# ══ TestUpdateTrackInfo ════════════════════════════════════════════════════════

class TestUpdateTrackInfo:

    def test_description_updated(self, tmp_path):
        tmp = str(tmp_path)
        track_name = 'my_event'
        ui_path = make_ui_track_dir(tmp, track_name)
        json_path = os.path.join(FIXTURES, 'simple_pipeline.json')

        update_track_info(tmp, track_name, json_path)

        with open(ui_path) as f:
            content = f.read()
        assert '4 standing + 1 pointer cones' in content

    def test_lot_dimensions_in_description(self, tmp_path):
        tmp = str(tmp_path)
        track_name = 'size_test'
        ui_path = make_ui_track_dir(tmp, track_name)
        json_path = os.path.join(FIXTURES, 'simple_pipeline.json')

        update_track_info(tmp, track_name, json_path)

        with open(ui_path) as f:
            content = f.read()
        # bounds: 0–10 x 0–20 → "10m x 20m"
        assert '10m x 20m' in content

    def test_missing_ui_track_json_does_not_raise(self, tmp_path):
        # Should print a warning but not crash
        update_track_info(str(tmp_path), 'nonexistent', os.path.join(FIXTURES, 'simple_pipeline.json'))

    def test_description_replaces_existing(self, tmp_path):
        tmp = str(tmp_path)
        track_name = 'replace_test'
        make_ui_track_dir(tmp, track_name, description='old description here')
        json_path = os.path.join(FIXTURES, 'simple_pipeline.json')

        update_track_info(tmp, track_name, json_path)

        ui_path = os.path.join(tmp, track_name, 'ui', 'ui_track.json')
        with open(ui_path) as f:
            content = f.read()
        assert 'old description here' not in content
        assert 'standing' in content


# ══ Integration tests (require Blender) ═══════════════════════════════════════

def _find_blender():
    import glob
    import platform
    blender = shutil.which('blender')
    if blender:
        return blender
    system = platform.system()
    if system == 'Linux':
        for pattern in ['/snap/bin/blender',
                        os.path.expanduser('~/snap/blender/current/usr/bin/blender'),
                        '/usr/local/bin/blender']:
            if os.path.isfile(pattern):
                return pattern
    return None


BLENDER_EXE = _find_blender()


@pytest.mark.integration
@pytest.mark.skipif(BLENDER_EXE is None, reason="Blender not found")
class TestBuildTrackIntegration:

    TRACK_NAME = 'test_refactor_smoke'

    def setup_method(self):
        import subprocess
        self.out_dir = os.path.join(PROJECT_ROOT, 'generated', self.TRACK_NAME)
        if os.path.isdir(self.out_dir):
            shutil.rmtree(self.out_dir)

    def teardown_method(self):
        if os.path.isdir(self.out_dir):
            shutil.rmtree(self.out_dir)

    def test_json_to_blend_rem_gymkhana(self):
        import subprocess
        json_path = os.path.join(FIXTURES, 'simple_pipeline.json')
        result = subprocess.run([
            sys.executable, os.path.join(PROJECT_ROOT, 'build_track.py'),
            '--name', self.TRACK_NAME,
            '--json', json_path,
            '--template', 'rem_gymkhana',
            '--flat',
            '--blender', BLENDER_EXE,
        ], capture_output=True, text=True)
        assert result.returncode == 0, f"build_track.py failed:\n{result.stderr}"
        blend_files = glob.glob(os.path.join(self.out_dir, 'blender', '**', '*.blend'), recursive=True)
        assert blend_files, "No .blend file generated"
