"""Synthetic non-confidential PDF fixtures: no access to the original review thesis."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import fitz

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'read_pdf.py'


def make_pdf(pth):
    pdf = fitz.open()
    for i in range(4):
        p = pdf.new_page(width=612, height=792)
        p.insert_text((72, 85), f'Chapter {i+1}: Concrete materials', fontsize=16)
        p.insert_text((72, 130), 'a', fontsize=12)
        p.insert_text((72, 160), '5%', fontsize=12)
        p.insert_text((72, 190), 'R2', fontsize=12)
        p.insert_text((72, 220), 'Strength = 42 MPa; water-cement ratio = 0.40.', fontsize=12)
        p.insert_text((515, 650), 'DRAFT REVIEW ONLY', fontsize=32,
                      rotate=90, fill_opacity=.23)
        # An 8-glyph watermark drawn as separate horizontal glyphs in a vertical column.
        for j, c in enumerate('WATERMARK'):
            p.insert_text((340, 240+22*j), c, fontsize=14, color=(.79,.79,.79))
        # A vertical opaque legitimate figure label should not be filtered.
        p.insert_text((45, 480), 'Stress MPa', fontsize=10, rotate=90)
    pdf.save(pth)
    pdf.close()


def cli(*arguments):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, arguments)],
                          capture_output=True, text=True)


class PdfenvTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.src = self.root / 'fixture.pdf'
        make_pdf(self.src)

    def tearDown(self):
        self.temp.cleanup()

    def test_full_watermark_and_short_lines(self):
        out = self.root/'text.md'
        result = cli('full', self.src, '-o', out, '--clean', '--format', 'md', '--quiet')
        self.assertEqual(result.returncode, 0, result.stderr)
        t = out.read_text('utf-8')
        self.assertNotIn('DRAFT REVIEW ONLY', t)
        self.assertNotIn('W\nA\nT\nE\nR\nM\nA\nR\nK', t)
        self.assertIn('5%', t)
        self.assertIn('a\n', t)
        self.assertIn('Stress MPa', t)
        au = json.loads((self.root/'text.watermark-audit.json').read_text('utf-8'))
        self.assertTrue(all(x['removed_glyphs'] >= 17 for x in au['pages']))

    def test_pages_and_render(self):
        out = self.root/'renders'
        result = cli('render', self.src, '-o', out, '--pages', '2,4', '--dpi', '200',
                     '--tile', '2', '--max-pixels', '2500000')
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((out/'render_manifest.json').read_text('utf-8'))
        self.assertEqual(len(manifest), 8)
        self.assertTrue(all(Path(x['path']).exists() for x in manifest))

    def test_audit_and_info(self):
        inf = cli('info', self.src, '--json')
        self.assertEqual(inf.returncode, 0, inf.stderr)
        self.assertEqual(json.loads(inf.stdout)['pages'], 4)
        aud = cli('audit', self.src, '--pages', '1,3')
        self.assertEqual(aud.returncode, 0, aud.stderr)
        self.assertEqual(len(json.loads(aud.stdout)['pages']), 2)

    def test_invalid_page_range(self):
        result = cli('sample', self.src, '--pages', '1-9')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('超出范围', result.stderr)



class SafetyAndRenderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.src = self.root/'single.pdf'
        doc = fitz.open()
        p = doc.new_page(width=612, height=792)
        p.insert_text((72, 100), 'a', fontsize=11)
        p.insert_text((72, 120), '5%', fontsize=11)
        p.insert_text((72, 140), 'Equation (3) = 1', fontsize=11)
        p.insert_text((300, 650), 'WATERMARK TEST', fontsize=28, rotate=90,
                      fill_opacity=.2)
        doc.save(self.src)
        doc.close()

    def tearDown(self):
        self.temp.cleanup()

    def test_single_page_ambiguous_watermark_not_auto_deleted(self):
        target = self.root/'single.md'
        r = cli('full', self.src, '-o', target, '--clean', '--format', 'md')
        self.assertEqual(r.returncode, 0, r.stderr)
        text = target.read_text('utf-8')
        self.assertIn('WATERMARK', text)
        self.assertIn('TEST', text)
        self.assertIn('a\n', text)
        self.assertIn('5%', text)

    def test_explicit_rejection_when_removal_threshold_too_low(self):
        # Multi-page watermark is detected, but rollback protects raw text.
        src = self.root/'multi.pdf'
        make_pdf(src)
        target = self.root/'rollback.txt'
        r = cli('full', src, '--clean', '--max-remove-ratio', '0.1', '-o', target)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('DRAFT', target.read_text('utf-8'))
        self.assertIn('REVIEW', target.read_text('utf-8'))
        self.assertIn('ONLY', target.read_text('utf-8'))
        au = json.loads((self.root/'rollback.watermark-audit.json').read_text('utf-8'))
        self.assertEqual(au['summary']['rollback_pages'], [1, 2, 3, 4])

    def test_relative_crop_render(self):
        out = self.root/'render'
        r = cli('render', self.src, '--pages', '1', '-o', out, '--dpi', '200',
                '--clip-rel', '.25', '.25', '.75', '.75', '--max-pixels', '6000000')
        self.assertEqual(r.returncode, 0, r.stderr)
        j = json.loads((out/'render_manifest.json').read_text('utf-8'))
        self.assertEqual(len(j), 1)
        self.assertAlmostEqual(j[0]['clip_pdf_points'][0], 153)
        self.assertAlmostEqual(j[0]['clip_pdf_points'][2], 459)

    def test_default_render_limits(self):
        src = self.root/'multi.pdf'
        make_pdf(src)
        out = self.root/'render-limit'
        # Default renders only a sample for large docs; requested 4 > cap 2 rejected.
        r = cli('render', src, '--pages', '1-4', '-o', out, '--max-render-pages', '2')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('上限', r.stderr)

    def test_parity_renderer(self):
        try:
            import pypdfium2
            import numpy
        except ImportError:
            self.skipTest('optional parity dependencies unavailable')
        out = self.root/'parity'
        r = cli('parity', self.src, '--pages', '1', '-o', out, '--dpi', '96')
        self.assertEqual(r.returncode, 0, r.stderr)
        j = json.loads((out/'parity.json').read_text('utf-8'))
        self.assertEqual(len(j), 1)
        self.assertGreaterEqual(j[0]['mean_absolute_pixel_diff'], 0)

    def test_scanned_pdf_auto_ocr(self):
        try:
            import pytesseract
            if 'eng' not in pytesseract.get_languages(config=''):
                self.skipTest('eng language data not installed')
        except (ImportError, FileNotFoundError):
            self.skipTest('optional tesseract not installed')
        from PIL import Image, ImageDraw, ImageFont
        from io import BytesIO
        image = Image.new('RGB', (1400, 900), 'white')
        draw = ImageDraw.Draw(image)
        try:
            font = ImageFont.truetype('DejaVuSans.ttf', 52)
        except OSError:
            font = ImageFont.load_default(size=52)
        draw.text((85, 180), 'SCANNED STRENGTH 42 MPa', fill='black', font=font)
        b = BytesIO()
        image.save(b, 'PNG')
        pdf = fitz.open()
        page = pdf.new_page(width=700, height=450)
        page.insert_image(page.rect, stream=b.getvalue())
        pdfpath = self.root/'scanned.pdf'
        pdf.save(pdfpath)
        pdf.close()
        out = self.root/'ocr.md'
        r = cli('full', pdfpath, '--pages', '1', '--ocr', 'auto', '--ocr-lang', 'eng',
                '--ocr-dpi', '200', '-o', out, '--format', 'md')
        self.assertEqual(r.returncode, 0, r.stderr)
        txt = out.read_text('utf-8')
        self.assertIn('STRENGTH 42', txt)


if __name__ == "__main__":
    unittest.main()
