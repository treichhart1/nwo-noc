"""Packager contract tests; these are not application screenshots or GUI evidence."""
import json
import struct
import tempfile
import unittest
from pathlib import Path
from package import build

class PackageTests(unittest.TestCase):
    def fixture(self, root, name='bad.png', width=390):
        (root/'capture.json').write_text(json.dumps({'id':1,'title':'test only','viewport':{'name':'desktop','width':1920,'height':1080},'files':[name],'layout':{}}))
        if '/' not in name:
            (root/name).write_bytes(b'\x89PNG\r\n\x1a\n'+b'\0'*8+struct.pack('>II',width,1080))

    def test_missing_views_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'parts').mkdir()
            with self.assertRaisesRegex(ValueError,'Missing/unexpected'):
                build(root/'parts',root/'output')

    def test_wrong_resolution_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'parts').mkdir();self.fixture(root/'parts')
            with self.assertRaisesRegex(ValueError,'resolution'):
                build(root/'parts',root/'output')

    def test_full_page_preserves_horizontal_overflow(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'parts').mkdir();self.fixture(root/'parts','01-desktop-full.png',2300)
            with self.assertRaisesRegex(ValueError,'Missing/unexpected'):
                build(root/'parts',root/'output')

    def test_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'parts').mkdir();self.fixture(root/'parts','../outside.png')
            with self.assertRaisesRegex(ValueError,'Unsafe'):
                build(root/'parts',root/'output')

if __name__=='__main__':unittest.main()
