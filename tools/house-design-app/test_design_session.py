import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import design_session as session
VIEWER_SCRIPTS = Path("D:/Projects/castle-viewer/src/scripts")
if VIEWER_SCRIPTS.is_dir():
    sys.path.insert(0, str(VIEWER_SCRIPTS))
    import enshrouded_house as house_core


class DesignSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.begin_ids = {}

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def spec(parts=None):
        return {"schema": session.SCHEMA, "title": "Дом", "altar": {"at": [0, 0, 0], "level": 2},
                "ground": True, "materials": {"stone": "Block_T3_Stone_CastleWall_REWARD"}, "parts": list(parts or [])}

    def begin(self, req="begin", title="Башня"):
        if req not in self.begin_ids:
            self.begin_ids[req] = str(uuid.uuid4())
        return session.begin_design(self.root, title, self.begin_ids[req])

    def test_begin_rejects_guessable_request_id_and_uuid4_dedup_is_stable(self):
        with self.assertRaisesRegex(ValueError, "canonical UUIDv4"):
            session.begin_design(self.root, "Private session", "begin-1")
        first_id = str(uuid.uuid4())
        first = session.begin_design(self.root, "Private session", first_id)
        self.assertEqual(session.begin_design(self.root, "Private session", first_id), first)
        with self.assertRaisesRegex(ValueError, "canonical UUIDv4"):
            session.begin_design(self.root, "Private session", str(uuid.uuid1()))

    def test_full_then_append_phase_snapshot_and_finished_session(self):
        initial = self.begin()
        self.assertRegex(initial["session_id"], r"^[0-9a-f-]{36}$")
        self.assertIsNone(initial["revision"])
        self.assertIsNone(initial["snapshot"])
        full = self.spec()
        result = session.update_design(self.root, initial["session_id"], "full", None, "Основание", spec=full)
        raw = json.dumps(full, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        revision = hashlib.sha256(raw).hexdigest()
        self.assertEqual(result["revision"], revision)
        self.assertEqual(session.get_snapshot(self.root, initial["session_id"], revision), raw)
        phase = session.set_phase(self.root, initial["session_id"], "phase", "Стены", state="waiting")
        self.assertEqual(phase["sequence"], result["sequence"] + 1)
        resumed = session.set_phase(self.root, initial["session_id"], "resume", "Продолжаю", state="designing")
        part = {"op": "box", "at": [0, 0, 0], "size": [1, 1, 1], "mat": "stone"}
        appended = session.update_design(self.root, initial["session_id"], "append", revision, "Первый этаж", append_parts=[part])
        self.assertEqual(len(json.loads(session.get_snapshot(self.root, initial["session_id"], appended["revision"]))["parts"]), 1)
        ended = session.finish_design(self.root, initial["session_id"], "end")
        self.assertEqual(ended["state"], "finished")
        with self.assertRaisesRegex(ValueError, "finished"):
            session.update_design(self.root, initial["session_id"], "late", appended["revision"], "Поздно", append_parts=[part])

    def test_request_dedup_and_conflicting_request_payload(self):
        state = self.begin("same-begin", "Дом")
        self.assertEqual(self.begin("same-begin", "Дом"), state)
        with self.assertRaisesRegex(ValueError, "different payload"):
            self.begin("same-begin", "Другой")
        accepted = session.update_design(self.root, state["session_id"], "edit", None, "Старт", spec=self.spec())
        phase = session.set_phase(self.root, state["session_id"], "phase2", "Следующий шаг")
        self.assertEqual(session.update_design(self.root, state["session_id"], "edit", None, "Старт", spec=self.spec()), accepted)
        self.assertEqual(session.get_state(self.root, state["session_id"]), phase)
        with self.assertRaisesRegex(ValueError, "different payload"):
            session.update_design(self.root, state["session_id"], "edit", None, "Иной шаг", spec=self.spec())

    def test_ordered_updates_drain_immutable_snapshots_across_non_model_events(self):
        state = self.begin()
        first = session.update_design(self.root, state["session_id"], "full", None, "Эскиз", spec=self.spec())
        part = {"op": "box", "at": [0, 0, 0], "size": [1, 1, 1], "mat": "stone"}
        second = session.update_design(self.root, state["session_id"], "second", first["revision"], "Форма", append_parts=[part])
        session.set_phase(self.root, state["session_id"], "note", "Уточняю вход")
        third = session.update_design(self.root, state["session_id"], "third", second["revision"], "Материалы", append_parts=[part])
        a = session.get_updates(self.root, state["session_id"], 1, 1)
        b = session.get_updates(self.root, state["session_id"], a[-1]["event_sequence"], 1)
        c = session.get_updates(self.root, state["session_id"], b[-1]["event_sequence"], 32)
        events = a + b + c
        self.assertEqual([x["event_type"] for x in events], ["update", "update", "phase", "update"])
        self.assertEqual([x["revision"] for x in events if x["event_type"] == "update"], [first["revision"], second["revision"], third["revision"]])
        self.assertEqual([x["event_sequence"] for x in events], sorted(x["event_sequence"] for x in events))
        self.assertTrue(all(hashlib.sha256(x["raw"]).hexdigest() == x["revision"] for x in events if x["event_type"] == "update"))

    def test_stale_revision_and_malformed_specs_preserve_state(self):
        state = self.begin()
        with self.assertRaisesRegex(ValueError, "first design update"):
            session.update_design(self.root, state["session_id"], "append-first", None, "Нет", append_parts=[])
        full = session.update_design(self.root, state["session_id"], "full", None, "Основание", spec=self.spec())
        with self.assertRaisesRegex(ValueError, "revision changed"):
            session.update_design(self.root, state["session_id"], "stale", None, "Старое", append_parts=[])
        with self.assertRaisesRegex(ValueError, "house schema"):
            session.update_design(self.root, state["session_id"], "schema", full["revision"], "Нет", spec={"schema": "bad", "parts": []})
        with self.assertRaisesRegex(ValueError, "operation object"):
            session.update_design(self.root, state["session_id"], "part", full["revision"], "Нет", spec=self.spec(["bad"]))
        with self.assertRaisesRegex(ValueError, "at most"):
            session.update_design(self.root, state["session_id"], "too-many", full["revision"], "Нет",
                                  spec=self.spec([{"op": "box"}] * (session.MAX_PARTS + 1)))
        self.assertEqual(session.get_state(self.root, state["session_id"]), full)

    def test_sessions_are_isolated_and_concurrent_updates_use_revision_guard(self):
        a, b = self.begin("begin-a", "A"), self.begin("begin-b", "B")
        self.assertNotEqual(a["session_id"], b["session_id"])
        sa = session.update_design(self.root, a["session_id"], "a-full", None, "A base", spec=self.spec())
        sb = session.update_design(self.root, b["session_id"], "b-full", None, "B base", spec=self.spec())
        barrier, outcomes = threading.Barrier(3), []
        part = {"op": "box", "at": [0, 0, 0], "size": [1, 1, 1], "mat": "stone"}
        def update(request_id):
            barrier.wait()
            try:
                outcomes.append(session.update_design(self.root, a["session_id"], request_id, sa["revision"], request_id, append_parts=[part]))
            except Exception as exc:
                outcomes.append(exc)
        workers = [threading.Thread(target=update, args=(x,)) for x in ("race-a", "race-b")]
        for w in workers: w.start()
        barrier.wait()
        for w in workers: w.join(5)
        self.assertTrue(all(not w.is_alive() for w in workers))
        self.assertEqual(sum(isinstance(x, dict) for x in outcomes), 1)
        self.assertEqual(sum(isinstance(x, ValueError) for x in outcomes), 1)
        self.assertEqual(session.get_state(self.root, b["session_id"]), sb)

    def test_fragment_paths_confined_traversal_and_symlink_escape(self):
        state = self.begin()
        safe = self.root / "work" / "part.json"
        fragment = {"schema": session.SCENE_SCHEMA, "materials": {"1": "Block_T3_Stone_CastleWall_REWARD"},
                    "runs": [[0, 0, 0, 1, 1]]}
        safe.write_text(json.dumps(fragment), encoding="utf-8")
        s1 = session.update_design(self.root, state["session_id"], "safe", None, "Фрагмент", spec=self.spec([{"op": "fragment", "file": str(safe)}]))
        outside = self.root.parent / (self.root.name + "-outside.json")
        outside.write_text("{}", encoding="utf-8")
        try:
            with self.assertRaises(ValueError):
                session.update_design(self.root, state["session_id"], "outside", s1["revision"], "Нет", spec=self.spec([{"op": "fragment", "file": str(outside)}]))
            with self.assertRaises(ValueError):
                session.update_design(self.root, state["session_id"], "traversal", s1["revision"], "Нет", spec=self.spec([{"op": "fragment", "file": "../outside.json"}]))
            link = self.root / "work" / "escape.json"
            try:
                link.symlink_to(outside)
            except OSError:
                return
            with self.assertRaises(ValueError):
                session.update_design(self.root, state["session_id"], "symlink", s1["revision"], "Нет", spec=self.spec([{"op": "fragment", "file": str(link)}]))
        finally:
            outside.unlink(missing_ok=True)

    def test_fragment_copy_freezes_rendered_geometry_without_mutating_request(self):
        if not VIEWER_SCRIPTS.is_dir():
            self.skipTest("Original Enshrouded house engine is unavailable")
        state = self.begin()
        source = self.root / "work" / "live-fragment.json"
        original = {"schema": session.SCENE_SCHEMA, "materials": {"1": "Block_T3_Stone_CastleWall_REWARD"},
                    "runs": [[0, 0, 0, 1, 1]]}
        source.write_text(json.dumps(original), encoding="utf-8")
        part = {"op": "fragment", "file": str(source)}
        spec = self.spec([part])
        published = session.update_design(self.root, state["session_id"], "publish", None, "Фрагмент сохранён", spec=spec)
        self.assertEqual(part["file"], str(source), "caller request object must remain unchanged")
        snapshot = json.loads(session.get_snapshot(self.root, state["session_id"], published["revision"]))
        frozen = Path(snapshot["parts"][0]["file"])
        frozen_bytes = frozen.read_bytes()
        self.assertEqual(json.loads(frozen_bytes), original)
        source.write_text(json.dumps({**original, "runs": [[9, 0, 0, 1, 1]]}), encoding="utf-8")
        self.assertEqual(frozen.read_bytes(), frozen_bytes)
        voxels, _props, _extra = house_core.load_fragment(frozen)
        self.assertIn((0, 0, 0), voxels)
        self.assertNotIn((9, 0, 0), voxels)


if __name__ == "__main__":
    unittest.main()
