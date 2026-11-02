# House Design App

This app follows only the design session explicitly opened by `begin_house_design_session` in the same chat. The begin tool returns a session ID and opens this MCP App. The app binds to that ID from the tool result or initialization context. Reopen a specific session with `open_house_design_app(session_id=...)`; opening without an ID shows an empty state. The app never chooses a global latest session or a saved design as a substitute.

The model is assembled in memory through `update_house_design`, using either a full typed specification or appended parts with an expected revision. `set_house_design_phase` reports real work before the first model snapshot. `finish_house_design_session` records the terminal state. Each update stores an immutable, revision-addressed snapshot. The app bootstraps the original `castle-viewer/src/scripts/enshrouded_view.py` viewer once, then sends revision-guarded JSON updates into its live scene. The document, renderer, camera, and OrbitControls stay alive while the viewer replaces the design-owned scene root. `spec.presentation.stage` accepts `sketch`, `form`, or `materials`; omitted legacy metadata means `materials`. These stages change rendering of actual authored voxel data and do not advance automatically. Original OrbitControls handle mouse orbit, zoom, and pan; compact accessible buttons provide orbit, zoom, and reset controls.

Session data is stored by the local `design_session.py` module in task-owned app storage. These tools do not launch a model, write a project design file, or interact with the game, server, or saves. The existing saved-project status and geometry-preview tools remain separate from the authoring session.

## Tests

```powershell
python -m unittest discover -s tools/house-design-app -p "test_*.py" -v
```
