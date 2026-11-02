import asyncio
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
sys.path.insert(0, str(Path(__file__).resolve().parent))
import server
import webui
from design_session import begin_design, list_sessions, update_design


class BrowserHostTests(unittest.TestCase):
    def test_browser_host_reads_shared_sessions_and_rejects_authoring(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as temp:
                server.ROOT = Path(temp)
                sid = begin_design(server.ROOT, "Browser durable session", str(uuid.uuid4()))["session_id"]
                spec = {"schema": "custodian.enshrouded.house.v1", "title": "Browser durable session",
                        "altar": {"at": [0, 0, 0], "level": 1}, "ground": True,
                        "materials": {"stone": "Block_T3_Stone_CastleWall_REWARD"}, "parts": []}
                saved = update_design(server.ROOT, sid, str(uuid.uuid4()), None, "Начало", spec=spec)
                self.assertEqual(list_sessions(server.ROOT)[0]["session_id"], sid)
                listener = await asyncio.start_server(webui.http_client, "127.0.0.1", 0)
                port = listener.sockets[0].getsockname()[1]
                async with listener:
                    async def request(method, path, payload=None, host="127.0.0.1", origin=None):
                        reader, writer = await asyncio.open_connection("127.0.0.1", port)
                        body = json.dumps(payload).encode() if payload is not None else b""
                        headers = f"{method} {path} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n"
                        if payload is not None: headers += f"Origin: {origin or f'http://127.0.0.1:{port}'}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n"
                        writer.write(headers.encode() + b"\r\n" + body); await writer.drain()
                        raw = await asyncio.wait_for(reader.read(), 8); writer.close(); await writer.wait_closed()
                        return raw
                    listing = await request("GET", "/api/sessions")
                    self.assertIn(sid.encode(), listing)
                    initialized = await request("POST", "/rpc", {"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_house_design_status","arguments":{"session_id":sid}}})
                    rpc_payload = json.loads(initialized.split(b"\r\n\r\n", 1)[1])
                    self.assertIn(saved["revision"], rpc_payload["result"]["content"][0]["text"])
                    updates = await request("POST", "/rpc", {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_house_design_updates","arguments":{"session_id":sid,"after_sequence":1,"limit":1}}})
                    update_payload = json.loads(updates.split(b"\r\n\r\n", 1)[1])
                    self.assertEqual(update_payload["result"]["_meta"]["houseDesign"]["events"][0]["revision"], saved["revision"])
                    viewer = await request("POST", "/rpc", {"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"get_house_design_viewer","arguments":{"session_id":sid,"snapshot_revision":saved["revision"],"event_sequence":saved["sequence"]}}})
                    viewer_payload = json.loads(viewer.split(b"\r\n\r\n", 1)[1])
                    self.assertEqual(viewer_payload["result"]["_meta"]["houseDesign"]["revision"], saved["revision"])
                    forbidden = await request("POST", "/rpc", {"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"update_house_design","arguments":{}}})
                    self.assertIn(b"403", forbidden)
                    hostile = await request("GET", "/api/sessions", host="attacker.example")
                    self.assertIn(b"403", hostile)
                    port_mismatch = await request("POST", "/rpc", {"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"open_house_design_app","arguments":{"session_id":sid}}}, host="localhost:9999", origin="http://localhost")
                    self.assertIn(b"403", port_mismatch)
                    self.assertIn(b"title", await request("GET", "/ui"))
                    self.assertIn(b"read-only", await request("POST", "/rpc", {"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"update_house_design","arguments":{}}}))
                    self.assertIn(b"404", await request("GET", "/../server.py"))
        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
