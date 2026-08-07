import os
import tempfile
import unittest
from pathlib import Path

from sbc.core.session import SBCSession

class SessionPathTests(unittest.TestCase):
    def test_bare_filename_finds_sessions_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "sessions").mkdir()
            SBCSession(server="https://bbb.example", websocket_url="wss://bbb.example/graphql").save(root / "sessions" / "teacher.sbc")
            old = Path.cwd(); os.chdir(root)
            try: self.assertEqual(SBCSession.load("teacher.sbc").server, "https://bbb.example")
            finally: os.chdir(old)
