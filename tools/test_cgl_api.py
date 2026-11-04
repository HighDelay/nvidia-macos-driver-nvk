#!/usr/bin/env python3
"""Public loader coverage must qualify both GL versions and extension strings."""
import sys
import tempfile
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'build'))
from opengl_api import requirements, supported


class CGLAPITests(unittest.TestCase):
    def contract(self):
        xml = '''<registry>
          <feature api="gl" number="1.0"><require><command name="glBegin"/><command name="glDraw"/></require></feature>
          <feature api="gl" number="3.2"><require><command name="glSync"/></require><remove profile="core"><command name="glBegin"/></remove></feature>
          <feature api="gl" number="4.1"><require><command name="glShader"/></require></feature>
          <feature api="gl" number="4.2"><require><command name="glStorage"/></require></feature>
          <feature api="gles2" number="9.0"><require><command name="glESOnly"/></require></feature>
          <extensions>
            <extension name="GL_ENUM_ONLY" supported="gl|glcore"><require><enum name="GL_MODE"/></require></extension>
            <extension name="GL_STORAGE" supported="gl|glcore"><require><command name="glStorage"/></require></extension>
            <extension name="GL_ALIASED" supported="gl|glcore"><require><command name="glDrawARB"/></require></extension>
            <extension name="GL_ES_ONLY" supported="gles2"><require><command name="glESOnly"/></require></extension>
            <extension name="GL_SPLIT" supported="gl|glcore|gles2"><require api="gl"><command name="glShader"/></require><require api="gles2"><command name="glESOnly"/></require></extension>
            <extension name="GL_LEGACY" supported="gl"><require><command name="glBegin"/></require></extension>
          </extensions></registry>'''
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'gl.xml';path.write_text(xml)
            return requirements(path)

    def test_core_removal_does_not_remove_compatibility_requirement(self):
        contract = self.contract()
        self.assertEqual(supported(contract, ['glDraw','glSync','glShader'], 'core')[0], 41)
        self.assertEqual(supported(contract, ['glDraw','glSync','glShader'], 'compatibility')[0], 0)
        self.assertEqual(supported(contract, ['glBegin','glDraw','glSync'], 'compatibility')[0], 32)

    def test_extensions_require_callable_symbols_including_aliases(self):
        contract = self.contract()
        version, extensions = supported(contract, ['glDraw','glSync','glShader'], 'core')
        self.assertEqual(version, 41)
        self.assertEqual(extensions, ['GL_ENUM_ONLY','GL_SPLIT'])
        version, extensions = supported(contract, ['glDraw','glSync','glShader','glStorage','glDrawARB'], 'core')
        self.assertEqual(version, 42)
        self.assertIn('GL_STORAGE', extensions)
        self.assertIn('GL_ALIASED', extensions)

    def test_desktop_contract_excludes_es_and_respects_profiles(self):
        contract = self.contract()
        self.assertNotIn('GL_ES_ONLY', contract['extensions']['core'])
        self.assertNotIn('GL_LEGACY', contract['extensions']['core'])
        self.assertEqual(contract['extensions']['core']['GL_SPLIT'], ['glShader'])
        self.assertIn('GL_LEGACY', supported(contract, ['glBegin'], 'compatibility')[1])


if __name__ == '__main__':
    unittest.main()
