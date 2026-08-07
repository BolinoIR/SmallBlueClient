import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, urlopen

from sbc.bridge import SessionBridge
from sbc.core.session import SBCSession

class BridgeTests(unittest.TestCase):
    def test_bridge_saves_paired_session_and_delivers_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = SessionBridge(directory, port=0).start()
            try:
                payload = SBCSession(server="https://bbb.example", websocket_url="wss://bbb.example/graphql", user_id="u").to_dict()
                request = Request(bridge.endpoint, json.dumps(payload).encode(), {"Content-Type": "application/json"})
                with urlopen(request) as response: self.assertEqual(response.status, 201)
                loaded = SBCSession.load(bridge.sessions[0])
                self.assertEqual(loaded.metadata["bridge_endpoint"], bridge.endpoint)
                command_url = bridge.endpoint.replace("/sessions", "/commands")
                with urlopen(Request(command_url, json.dumps({"identity": loaded.metadata["sbc_identity"], "command": {"kind": "audio", "action": "mute"}}).encode(), {"Content-Type": "application/json"})) as response: self.assertEqual(response.status, 202)
                with urlopen(f"{command_url}&identity={loaded.metadata['sbc_identity']}") as response: commands = json.loads(response.read())["commands"]
                self.assertEqual(commands, [{"kind": "audio", "action": "mute"}])
            finally: bridge.close()
