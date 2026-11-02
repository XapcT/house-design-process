import asyncio
import base64
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server as app


class HouseAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        app.ROOT = self.root
        app.JOBS_DIR = self.root / "work" / "house-app-integration" / "jobs"
        app.CURRENT_JOB = None
        app.CURRENT_REQUEST = None
        app.PREVIEW_DATA_URL = None
        app.PREVIEW_META = None
        self.project = self.root / "work" / "mage-tower-80-test"
        (self.project / "output" / "blueprint").mkdir(parents=True)
        (self.project / "output" / "diagnostic-preview.html").write_text("<h1>viewer</h1>")
        self.write_inputs()

    def tearDown(self):
        self.tmp.cleanup()

    def write_inputs(self):
        out = self.project / "output"
        design = {"title": "Тестовая башня", "construction_sequence": ["разметка"]}
        report = {"status": "PASS", "generated_utc": "2026-11-02T00:00:00Z", "errors": [], "warnings": [], "block_voxels": 10, "prop_count": 2}
        (out / "mage-tower-80-test.json").write_text(json.dumps(design), encoding="utf-8")
        (out / "house-check-report.json").write_text(json.dumps(report), encoding="utf-8")
        files = {}
        for rel in ("output/mage-tower-80-test.json", "output/house-check-report.json"):
            files[rel] = hashlib.sha256((self.project / rel).read_bytes()).hexdigest()
        for generator in self.project.glob("gen_*.py"):
            files[generator.name] = hashlib.sha256(generator.read_bytes()).hexdigest()
        (out / "sha256-manifest.json").write_text(json.dumps({"algorithm": "SHA-256", "files": files}), encoding="utf-8")

    def test_valid_and_stale_evidence_and_export_label(self):
        p = app.project_status(self.project)
        self.assertEqual(p["evidence"], "current")
        self.assertEqual(p["checker"], "PASS")
        self.assertEqual(p["export_files_found"], 0)
        (self.project / "output" / "diagnostic-preview.html").write_text("changed")
        (self.project / "output" / "mage-tower-80-test.json").write_text('{"title":"changed"}')
        self.assertEqual(app.project_status(self.project)["evidence"], "stale")

    def test_saved_design_revision_is_available_without_diagnostic_files(self):
        report = self.project / "output" / "house-check-report.json"
        manifest = self.project / "output" / "sha256-manifest.json"
        report.unlink(); manifest.unlink()
        raw = (self.project / "output" / f"{self.project.name}.json").read_bytes()
        status = app.project_status(self.project)
        self.assertEqual(status["revision"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(status["title"], "Тестовая башня")
        self.assertEqual(status["evidence"], "unknown")

    def test_real_geometry_progress_dedup_events_and_partial_image(self):
        spec = {
            "schema": "custodian.enshrouded.house.v1", "name": self.project.name,
            "title": "Tiny operation test", "materials": {"stone": "Block_T3_Stone_CastleWall_REWARD"},
            "parts": [
                {"op": "box", "at": [0, 0, 0], "size": [2, 2, 1], "mat": "stone"},
                {"op": "box", "at": [0, 0, 1], "size": [1, 1, 1], "mat": "stone"},
            ],
        }
        design = self.project / "output" / f"{self.project.name}.json"
        design.write_text(json.dumps(spec), encoding="utf-8")
        started = app.start_geometry_job(self.project.name, "tiny-request-1")
        duplicate = app.start_geometry_job(self.project.name, "tiny-request-1")
        self.assertEqual(started["operation_id"], duplicate["operation_id"])
        import time
        limit = time.monotonic() + 10
        while time.monotonic() < limit:
            job = app.all_status()["job"]
            if job and job.get("state") in ("completed", "failed"):
                break
            time.sleep(0.02)
        self.assertEqual(job["state"], "completed", job)
        self.assertEqual((job["completed"], job["total"]), (2, 2))
        self.assertEqual(job["sequence"], 2)
        events = (app.JOBS_DIR / "events.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(sum('"event": "operation_completed"' in line for line in events), 2)
        preview = app.asyncio.run(app.call_tool("get_house_design_preview", {"operation_id": job["operation_id"]}))
        meta = preview.meta["houseDesign"]
        self.assertEqual(meta["revision"], started["revision"])
        self.assertEqual(meta["sequence"], 2)
        png = base64.b64decode(meta["previewDataUrl"].split(",", 1)[1])
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_interactive_viewer_uses_saved_revision_and_original_3d_controls(self):
        spec = {
            "schema": "custodian.enshrouded.house.v1", "name": self.project.name,
            "title": "Интерактивный просмотр", "materials": {"stone": "Block_T3_Stone_CastleWall_REWARD"},
            "parts": [{"op": "box", "at": [0, 0, 0], "size": [2, 2, 1], "mat": "stone"}],
        }
        path = self.project / "output" / f"{self.project.name}.json"
        raw = json.dumps(spec, ensure_ascii=False).encode("utf-8")
        path.write_bytes(raw)
        viewer = app._interactive_viewer(self.project.name)
        self.assertEqual(viewer["revision"], hashlib.sha256(raw).hexdigest())
        self.assertIn('id="three"', viewer["html"])
        self.assertIn("OrbitControls.js", viewer["html"])
        self.assertIn("__houseDesignOrbit", viewer["html"])
        self.assertIn('id="house-design-controls"', viewer["html"])
        self.assertIn("zoomIn()", viewer["html"])
        self.assertIn("cameraPose", viewer["html"])
        self.assertIn("тяните — вращать, колесо — масштаб", viewer["html"])
        self.assertIn(".views>section:nth-child(2)", viewer["html"])
        self.assertIn("</style><style>", viewer["html"])
        self.assertNotIn('id="cut" checked', viewer["html"])
        self.assertNotIn("diagnostic-preview", viewer["html"])
        spec["title"] = "Новая сохранённая версия"
        path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        updated = app._interactive_viewer(self.project.name)
        self.assertNotEqual(viewer["revision"], updated["revision"])
        self.assertIn("Новая сохранённая версия", updated["html"])
        spec["title"] = "</title><script>window.pwned=1</script>"
        path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        escaped = app._interactive_viewer(self.project.name)["html"]
        self.assertIn("&lt;/title&gt;&lt;script&gt;window.pwned=1&lt;/script&gt;", escaped)
        self.assertNotIn("</title><script>window.pwned=1", escaped)

    def test_mcp_design_session_begin_updates_viewer_and_session_isolation(self):
        import asyncio
        from mcp.types import CallToolResult
        begin_request_id = str(uuid.uuid4())
        other_request_id = str(uuid.uuid4())
        async def call(name, args):
            result = await app.call_tool(name, args)
            if name != "get_house_design_status":
                self.assertIsInstance(result, CallToolResult)
            return result
        base = {"schema": "custodian.enshrouded.house.v1", "name": "House", "title": "Первая версия",
                "materials": {"stone": "Block_T3_Stone_CastleWall_REWARD"},
                "parts": [{"op": "box", "at": [0, 0, 0], "size": [1, 1, 1], "mat": "stone"}]}
        async def exercise():
            begun = await call("begin_house_design_session", {"title": "House", "request_id": begin_request_id})
            self.assertFalse(begun.isError)
            session_id = begun.structuredContent["session_id"]
            self.assertEqual(begun.meta["houseDesign"]["session_id"], session_id)
            phase = await call("set_house_design_phase", {"session_id": session_id, "request_id": "phase-1", "phase": "Планирую форму"})
            self.assertEqual(phase.structuredContent["snapshot"], None)
            first = await call("update_house_design", {"session_id": session_id, "request_id": "model-1", "expected_revision": None, "phase": "Первая версия", "spec": base})
            state1 = first.structuredContent
            raw1 = app.get_snapshot(self.root, session_id, state1["revision"])
            duplicate = await call("update_house_design", {"session_id": session_id, "request_id": "model-1", "expected_revision": None, "phase": "Первая версия", "spec": base})
            self.assertEqual(duplicate.structuredContent["revision"], state1["revision"])
            second = await call("update_house_design", {"session_id": session_id, "request_id": "model-2", "expected_revision": state1["revision"], "phase": "Добавляю этаж", "append_parts": [{"op": "box", "at": [0, 0, 1], "size": [1, 1, 1], "mat": "stone"}]})
            state2 = second.structuredContent
            self.assertNotEqual(state2["revision"], state1["revision"])
            self.assertEqual(app.get_snapshot(self.root, session_id, state1["revision"]), raw1)
            viewer = await call("get_house_design_viewer", {"session_id": session_id, "snapshot_revision": state2["revision"], "event_sequence": second.structuredContent["snapshot"]["event_sequence"]})
            self.assertFalse(viewer.isError)
            self.assertEqual(viewer.meta["houseDesign"]["revision"], state2["revision"])
            self.assertEqual(viewer.meta["houseDesign"]["session_id"], session_id)
            self.assertIn("OrbitControls.js", viewer.meta["houseDesign"]["html"])
            stale = await call("update_house_design", {"session_id": session_id, "request_id": "model-stale", "expected_revision": state1["revision"], "phase": "Старое основание", "append_parts": []})
            self.assertTrue(stale.isError)
            other = await call("begin_house_design_session", {"title": "Другой дом", "request_id": other_request_id})
            other_id = other.structuredContent["session_id"]
            self.assertNotEqual(other_id, session_id)
            status = await app.call_tool("get_house_design_status", {"session_id": session_id})
            self.assertEqual(json.loads(status[0].text)["session"]["session_id"], session_id)
        asyncio.run(exercise())

    def write_tiny_spec(self):
        spec = {
            "schema": "custodian.enshrouded.house.v1", "name": self.project.name,
            "title": "Tiny lifecycle test", "materials": {"stone": "Block_T3_Stone_CastleWall_REWARD"},
            "parts": [{"op": "box", "at": [0, 0, 0], "size": [1, 1, 1], "mat": "stone"}],
        }
        (self.project / "output" / f"{self.project.name}.json").write_text(json.dumps(spec), encoding="utf-8")

    def wait_for_terminal_job(self):
        import time
        limit = time.monotonic() + 10
        while time.monotonic() < limit:
            job = app.all_status()["job"]
            if job and job.get("state") in ("completed", "failed"):
                return job
            time.sleep(0.02)
        self.fail("geometry worker did not reach a terminal state")

    def assert_new_request_completes(self, request_id):
        started = app.start_geometry_job(self.project.name, request_id)
        job = self.wait_for_terminal_job()
        self.assertEqual(job["operation_id"], started["operation_id"])
        self.assertEqual(job["state"], "completed", job)

    def test_queued_write_and_failure_log_errors_release_admission(self):
        self.write_tiny_spec()
        original = app._job_write

        def fail_initial_and_terminal_events(event):
            if event in ("queued", "failed"):
                raise OSError("storage unavailable")
            return original(event)

        app._job_write = fail_initial_and_terminal_events
        try:
            with self.assertRaises(OSError):
                app.start_geometry_job(self.project.name, "queue-failure")
            self.assertEqual(app.CURRENT_JOB["state"], "failed")
            self.assertIn("storage unavailable", app.CURRENT_JOB["error"])
        finally:
            app._job_write = original
        self.assert_new_request_completes("after-queue-failure")

    def test_thread_start_error_marks_failed_and_releases_admission(self):
        self.write_tiny_spec()
        original_start = app.threading.Thread.start

        def fail_start(_thread):
            raise RuntimeError("thread unavailable")

        app.threading.Thread.start = fail_start
        try:
            with self.assertRaisesRegex(RuntimeError, "thread unavailable"):
                app.start_geometry_job(self.project.name, "thread-failure")
            self.assertEqual(app.CURRENT_JOB["state"], "failed")
            self.assertIn("thread unavailable", app.CURRENT_JOB["error"])
        finally:
            app.threading.Thread.start = original_start
        self.assert_new_request_completes("after-thread-failure")

    def test_started_write_error_marks_worker_failed_and_releases_admission(self):
        self.write_tiny_spec()
        original = app._job_write

        def fail_started(event):
            if event == "started":
                raise OSError("started event unavailable")
            return original(event)

        app._job_write = fail_started
        try:
            app.start_geometry_job(self.project.name, "started-failure")
            job = self.wait_for_terminal_job()
            self.assertEqual(job["state"], "failed", job)
            self.assertIn("started event unavailable", job["error"])
        finally:
            app._job_write = original
        self.assert_new_request_completes("after-started-failure")

    def test_generator_dependency_and_inconsistent_pass_are_not_current(self):
        generator = self.project / "gen_mage_tower_80.py"
        generator.write_text("# changed generator", encoding="utf-8")
        self.assertEqual(app.project_status(self.project)["evidence"], "stale")
        self.write_inputs()
        report_path = self.project / "output" / "house-check-report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["errors"] = ["checker contradiction"]
        report_path.write_text(json.dumps(report), encoding="utf-8")
        manifest_path = self.project / "output" / "sha256-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"]["output/house-check-report.json"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertEqual(app.project_status(self.project)["evidence"], "unknown")

    def _legacy_test_mcp_ui_bridge_initializes_calls_tools_and_escapes_values(self):
        if not shutil.which("node"):
            self.skipTest("Node.js unavailable")
        html = (Path(__file__).with_name("index.html")).read_text(encoding="utf-8")
        script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
        harness = r'''
const vm=require('node:vm');let script='';process.stdin.setEncoding('utf8');process.stdin.on('data',x=>script+=x);process.stdin.on('end',()=>{
 const calls=[],listeners={},handlers={},els={};let resizeCb=null;for(const id of ['#project','#content','#updated','#previewToggle','#openPreview','#preview','#frame','#modelImage','#imageCaption','#jobPhase','#jobCounter','#jobProgress','#jobError','#startBuild']){const classes=new Set(id==='#preview'?['hidden']:[]);els[id]={value:'',innerHTML:'',href:'',src:'',srcdoc:'',textContent:'',disabled:false,hidden:false,max:1,classList:{add(x){classes.add(x)},remove(x){classes.delete(x)},toggle(x){classes.has(x)?classes.delete(x):classes.add(x)},contains(x){return classes.has(x)}},addEventListener(k,fn){handlers[id+':'+k]=fn}}}
 Object.defineProperty(els['#project'],'innerHTML',{set(v){this._html=v;const m=v.match(/value="([^"]+)/);if(m&&!this.value)this.value=m[1]},get(){return this._html||''}});
 let started=false;const status=()=>({projects:[{project:'p1',title:'<img src=x>',description:'desc',stage:'Этап <x>',last_activity_utc:'now',evidence:'current',checker:'PASS',block_voxels:'<svg>',prop_count:3,construction_sequence:[],preview:'/preview/p1'}],job:started?{operation_id:'op1',project_id:'p1',revision:'rev1',sequence:1,state:'running',completed:1,total:2,preview_seq:1,phase:'Сборка геометрии чертежа'}:null});
 const host={postMessage(msg,target){calls.push([msg,target]);if(msg.method==='ui/notifications/initialized')setTimeout(()=>resizeCb?.(),0);if(msg.method==='ui/initialize')setTimeout(()=>listeners.message({source:host,origin:'https://host.local',data:{jsonrpc:'2.0',id:msg.id,result:{protocolVersion:'2026-01-26'}}}),0);if(msg.method==='tools/call')setTimeout(()=>{let result;if(msg.params.name==='get_house_design_status')result={content:[{type:'text',text:JSON.stringify(status())}]};else if(msg.params.name==='start_house_design_preview'){started=true;result={content:[{type:'text',text:JSON.stringify({operation_id:'op1',project_id:'p1',revision:'rev1',state:'queued',completed:0,total:2})}]}}else result={content:[{type:'text',text:'preview ready'}],_meta:{houseDesign:{operation_id:'op1',revision:'rev1',sequence:1,previewDataUrl:'data:image/png;base64,UE5H'}}};listeners.message({source:host,origin:'https://host.local',data:{jsonrpc:'2.0',id:msg.id,result}})},0)}};
 const win={parent:host,addEventListener(k,fn){listeners[k]=fn}};const ctx={window:win,parent:host,document:{querySelector:s=>els[s],documentElement:{scrollHeight:680}},location:{protocol:'mcp:'},fetch(){throw Error('fetch must not run in MCP app')},ResizeObserver:class{constructor(cb){resizeCb=cb}observe(){}},setInterval(){},setTimeout,clearTimeout,Date,Map,Promise,Error,console};vm.runInNewContext(script,ctx);setTimeout(()=>{if(els['#preview'].classList.contains('hidden')||els['#startBuild'].hidden||els['#startBuild'].disabled)throw Error('idle MCP card is hidden or cannot start');handlers['#startBuild:click']();setTimeout(()=>{try{const methods=calls.map(x=>x[0].method);const names=calls.filter(x=>x[0].method==='tools/call').map(x=>x[0].params.name);if(methods[0]!=='ui/initialize'||calls[0][0].params.protocolVersion!=='2026-01-26')throw Error('missing initialize handshake');if(!methods.includes('ui/notifications/initialized')||!methods.includes('ui/notifications/size-changed')||!names.includes('start_house_design_preview')||!names.includes('get_house_design_preview'))throw Error('missing app job/preview/size calls');if(!els['#content'].innerHTML.includes('&lt;img src=x&gt;')||!els['#content'].innerHTML.includes('&lt;svg&gt;')||!els['#content'].innerHTML.includes('Этап &lt;x&gt;'))throw Error('rendered values were not escaped');if(els['#preview'].classList.contains('hidden')||els['#frame'].src||!els['#openPreview'].classList.contains('hidden'))throw Error('MCP card exposed a blank frame or external preview substitute');if(els['#jobProgress'].max!==2||els['#jobProgress'].value!==1||!els['#modelImage'].src.startsWith('data:image/png;base64,')||els['#modelImage'].hidden)throw Error('actual progress or intermediate image was not rendered');process.exit(0)}catch(e){console.error(e);process.exit(1)}},140)},80)
});
'''
        completed = subprocess.run(["node", "-e", harness], input=script, text=True, encoding="utf-8", capture_output=True, timeout=5)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def _legacy_test_mcp_async_results_are_correlated_to_selection_operation_and_sequence(self):
        if not shutil.which("node"):
            self.skipTest("Node.js unavailable")
        html = Path(__file__).with_name("index.html").read_text(encoding="utf-8")
        script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
        harness = r'''
const vm=require('node:vm');let source='';process.stdin.setEncoding('utf8');process.stdin.on('data',x=>source+=x);process.stdin.on('end',async()=>{
 const calls=[],listeners={},handlers={},els={},statuses=[],previews=[];let resizeCb=null,serverJob=null;
 for(const id of ['#project','#content','#updated','#previewToggle','#openPreview','#preview','#frame','#modelImage','#imageCaption','#jobPhase','#jobCounter','#jobProgress','#jobError','#startBuild']){const classes=new Set(id==='#preview'?['hidden']:[]);els[id]={value:'',innerHTML:'',href:'',src:'',textContent:'',disabled:false,hidden:false,max:1,classList:{add(x){classes.add(x)},remove(x){classes.delete(x)},toggle(x){classes.has(x)?classes.delete(x):classes.add(x)},contains(x){return classes.has(x)}},addEventListener(k,fn){handlers[id+':'+k]=fn}}}
 Object.defineProperty(els['#project'],'innerHTML',{set(v){this._html=v;const m=v.match(/value="([^\"]+)/);if(m&&!this.value)this.value=m[1]},get(){return this._html||''}});
 const host={postMessage(msg){calls.push(msg);if(msg.method==='ui/notifications/initialized')setTimeout(()=>resizeCb?.(),0);if(msg.method==='ui/initialize')setTimeout(()=>listeners.message({source:host,origin:'https://host.test',data:{jsonrpc:'2.0',id:msg.id,result:{}}}),0);if(msg.method==='tools/call'){if(msg.params.name==='get_house_design_status')statuses.push(msg);else if(msg.params.name==='get_house_design_preview')previews.push(msg);else if(msg.params.name==='start_house_design_preview'){serverJob={project_id:'A',operation_id:'new-op',revision:'new-rev',sequence:0,state:'queued',completed:0,total:2};setTimeout(()=>reply(msg,{content:[{type:'text',text:JSON.stringify(serverJob)}]}),0)}}}};
 function reply(req,result){if(Array.isArray(result?.projects))result={content:[{type:'text',text:JSON.stringify(result)}]};listeners.message({source:host,origin:'https://host.test',data:{jsonrpc:'2.0',id:req.id,result}})}function fail(req){listeners.message({source:host,origin:'https://host.test',data:{jsonrpc:'2.0',id:req.id,error:{message:'late failure'}}})}
 const wait=ms=>new Promise(r=>setTimeout(r,ms));async function ready(fn){for(let i=0;i<100;i++){if(fn())return;await wait(2)}throw Error('wait timed out')}
 const status=job=>({projects:[{project:'A',title:'Project A',stage:'A stage',construction_sequence:[]},{project:'B',title:'Project B',stage:'B stage',construction_sequence:[]}],job});
 const ctx={window:{parent:host,addEventListener(k,fn){listeners[k]=fn}},parent:host,document:{querySelector:s=>els[s],documentElement:{scrollHeight:600}},location:{protocol:'mcp:'},fetch(){throw Error('unexpected fetch')},ResizeObserver:class{constructor(cb){resizeCb=cb}observe(){}},setInterval(){},setTimeout(fn,ms){return ms>=1000?0:setTimeout(fn,ms)},clearTimeout,Date,Map,Promise,Error,console};vm.runInNewContext(source,ctx);
 try{
  await ready(()=>statuses.length===1);reply(statuses.shift(),status(null));await wait(5);
  vm.runInContext("selected='B';uiGeneration++;refresh()",ctx);await ready(()=>statuses.length===1);reply(statuses.shift(),status({project_id:'A',operation_id:'A1',revision:'ra',sequence:1,state:'running',completed:1,total:3,preview_seq:1}));await wait(5);
  if(!els['#content'].innerHTML.includes('Project B')||vm.runInContext('liveJob',ctx)!==null)throw Error('wrong-project job rendered');
  els['#project'].value='A';handlers['#project:change']();await ready(()=>statuses.length===1);reply(statuses.shift(),status({project_id:'A',operation_id:'A1',revision:'ra',sequence:1,state:'running',completed:1,total:3,preview_seq:1}));await ready(()=>previews.length===1);const oldPreview=previews.shift();
  els['#project'].value='B';handlers['#project:change']();await ready(()=>statuses.length===1);reply(statuses.shift(),status(null));await wait(5);const bCounter=els['#jobCounter'].textContent;reply(oldPreview,{content:[{type:'text',text:'ok'}],_meta:{houseDesign:{operation_id:'A1',revision:'ra',sequence:1,previewDataUrl:'data:image/png;base64,AA=='}}});await wait(5);if(els['#modelImage'].src||els['#jobCounter'].textContent!==bCounter)throw Error('late A preview changed B view');
  els['#project'].value='A';handlers['#project:change']();await ready(()=>statuses.length===1);reply(statuses.shift(),status({project_id:'A',operation_id:'A1',revision:'ra',sequence:1,state:'running',completed:1,total:3,preview_seq:1}));await ready(()=>previews.length===1);const failedPreview=previews.shift();els['#project'].value='B';handlers['#project:change']();await ready(()=>statuses.length===1);reply(statuses.shift(),status(null));await wait(5);fail(failedPreview);await wait(5);if(!els['#jobError'].classList.contains('hidden'))throw Error('stale A error leaked into B');
  els['#project'].value='A';handlers['#project:change']();await ready(()=>statuses.length===1);reply(statuses.shift(),status({project_id:'A',operation_id:'A1',revision:'ra',sequence:1,state:'running',completed:1,total:3,preview_seq:1}));await ready(()=>previews.length===1);const snap1=previews.shift();vm.runInContext('refresh()',ctx);await ready(()=>statuses.length===1);reply(statuses.shift(),status({project_id:'A',operation_id:'A1',revision:'ra',sequence:2,state:'running',completed:2,total:3,preview_seq:2}));await ready(()=>previews.length===1);const snap2=previews.shift();reply(snap2,{content:[],_meta:{houseDesign:{operation_id:'A1',revision:'ra',sequence:2,previewDataUrl:'data:image/png;base64,Ag=='}}});await wait(5);reply(snap1,{content:[],_meta:{houseDesign:{operation_id:'A1',revision:'ra',sequence:1,previewDataUrl:'data:image/png;base64,AQ=='}}});await wait(5);if(!els['#modelImage'].src.endsWith('Ag==')||!els['#imageCaption'].textContent.includes('2'))throw Error('older snapshot regressed image');
  vm.runInContext('refresh()',ctx);await ready(()=>statuses.length===1);const oldStatus=statuses.shift();handlers['#startBuild:click']();await ready(()=>calls.some(x=>x.method==='tools/call'&&x.params?.name==='start_house_design_preview'));reply(oldStatus,status({project_id:'A',operation_id:'A1',revision:'ra',sequence:3,state:'running',completed:3,total:3,preview_seq:2}));await ready(()=>statuses.length===1);reply(statuses.shift(),status(serverJob));await wait(5);if(vm.runInContext('liveJob.operation_id',ctx)!=='new-op')throw Error('late old status rolled back accepted operation');if(!calls.filter(x=>x.method==='tools/call'&&x.params?.name==='get_house_design_status').length)throw Error('queued refresh was lost');
  process.exit(0)
 }catch(e){console.error(e);process.exit(1)}
});
'''
        completed = subprocess.run(["node", "-e", harness], input=script, text=True, encoding="utf-8", capture_output=True, timeout=6)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_ordered_live_app_bootstrap_exact_ack_retry_and_stale_session(self):
        if not shutil.which("node"):
            self.skipTest("Node.js unavailable")
        html = Path(__file__).with_name("index.html").read_text(encoding="utf-8")
        script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
        harness = r'''
const vm=require('node:vm');let source='';process.stdin.setEncoding('utf8');process.stdin.on('data',x=>source+=x);process.stdin.on('end',async()=>{
 const listeners={},timers=new Map(),calls=[],statuses=[],updates=[],viewers=[],sent=[];let timerId=0;
 const frame={dataset:{},srcdoc:'',contentWindow:{postMessage:m=>sent.push(m)},handlers:{},addEventListener(k,fn){this.handlers[k]=fn}};for(const id of ['#status','#viewer','#hide-earth']){}
 const els={'#status':{textContent:'',dataset:{}},'#viewer':frame,'#hide-earth':{checked:false,addEventListener(){}}};
 Object.defineProperty(frame,'srcdoc',{get(){return this._srcdoc||''},set(v){this._srcdoc=v;this.assignments=(this.assignments||0)+1}});
 const emit=e=>(listeners.message||[]).forEach(fn=>fn(e));
 const host={postMessage(msg){calls.push(msg);if(msg.method==='ui/initialize')setTimeout(()=>emit({source:host,origin:'https://host.test',data:{jsonrpc:'2.0',id:msg.id,result:{hostContext:{displayMode:'inline',toolResult:{_meta:{houseDesign:{session_id:'s1',bind_mode:'new',cursor_sequence:0}}}}}}}),0);if(msg.method==='tools/call'){if(msg.params.name==='get_house_design_status')statuses.push(msg);else if(msg.params.name==='get_house_design_updates')updates.push(msg);else if(msg.params.name==='get_house_design_viewer')viewers.push(msg)}}};
 const reply=(req,result)=>emit({source:host,origin:'https://host.test',data:{jsonrpc:'2.0',id:req.id,result}}),wait=ms=>new Promise(r=>setTimeout(r,ms)),ready=async fn=>{for(let i=0;i<250;i++){if(fn())return;await wait(2)}throw Error('timeout waiting for expected UI call')};
 const fire=ms=>{const entry=[...timers.entries()].find(([,v])=>v.ms===ms);if(!entry)throw Error('missing timer '+ms);timers.delete(entry[0]);entry[1].fn()};
 const ctx={window:{parent:host,addEventListener(k,fn){(listeners[k]??=[]).push(fn)}},parent:host,document:{querySelector:s=>els[s],documentElement:{scrollHeight:470,scrollWidth:600,style:{setProperty(){}}}},location:{protocol:'mcp:'},setTimeout(fn,ms){const id=++timerId;timers.set(id,{fn,ms});return id},clearTimeout(id){timers.delete(id)},Date,Map,Promise,Error,console};vm.runInNewContext(source,ctx);
 const state=(phase,revision,seq,count)=>({content:[{type:'text',text:JSON.stringify({session:{session_id:'s1',title:'House',state:'designing',phase,completed_actions:count,snapshot:revision?{revision,event_sequence:seq}:null}})}]});
 const event=(type,seq,phase,revision,stage,intent)=>({event_type:type,event_sequence:seq,phase,revision,data:type==='update'?{v:[[0,0,0,1,0]],mats:['stone'],total:1}:undefined,presentation:type==='update'?{stage,intent,phase}:undefined});
 try{
  await ready(()=>statuses.length===1&&updates.length===1);const initialStatus=statuses.shift();if(initialStatus.params.arguments.session_id!=='s1')throw Error('new card lost explicit session');reply(initialStatus,state('Поздняя форма','r2',4,2));
  const begin=updates.shift();if(begin.params.arguments.after_sequence!==0)throw Error('new session did not anchor event cursor at zero');reply(begin,{content:[{type:'text',text:'{}'}],_meta:{houseDesign:{session_id:'s1',events:[event('begin',1,'Начато')],assets:{}}}});
  await ready(()=>updates.length===1);reply(updates.shift(),{content:[{type:'text',text:'{}'}],_meta:{houseDesign:{session_id:'s1',events:[event('phase',2,'Черчу эскиз')],assets:{}}}});
  await ready(()=>updates.length===1);reply(updates.shift(),{content:[{type:'text',text:'{}'}],_meta:{houseDesign:{session_id:'s1',events:[event('update',3,'Эскиз','r1','sketch','Низкое крыло')],assets:{stone_albedo:'asset'}}}});
  await ready(()=>viewers.length===1);const exact=viewers.shift(),placeholderAssignments=frame.assignments||0;if(exact.params.arguments.event_sequence!==3||exact.params.arguments.snapshot_revision!=='r1')throw Error('first author sketch was not exact bootstrap');
  reply(exact,{isError:true,content:[{type:'text',text:'{"error":"temporary bootstrap failure"}'}]});await wait(2);fire(2500);await ready(()=>viewers.length===1);const retried=viewers.shift();if(JSON.stringify(retried.params.arguments)!==JSON.stringify(exact.params.arguments))throw Error('bootstrap retry changed immutable event');
  reply(retried,{content:[{type:'text',text:'{"loaded":true}'}],_meta:{houseDesign:{session_id:'s1',revision:'r1',event_sequence:3,document_token:'doc-one',html:'<html>sketch</html>',data:{v:[[0,0,0,1,0]],mats:['stone'],total:1},presentation:{stage:'sketch',intent:'Низкое крыло',phase:'Эскиз'}}}});await wait(2);
  if(!frame.srcdoc.includes('<html>sketch</html>')||frame.assignments!==placeholderAssignments+1)throw Error('one-time sketch bootstrap missing');
  emit({source:frame.contentWindow,origin:'null',data:{type:'house-design-ready',session_id:'s1',document_token:'doc-one',identity_preserved:true}});await ready(()=>sent.length===1);const first=sent[0];if(first.event_sequence!==3||first.presentation.intent!=='Низкое крыло'||first.assets.stone_albedo!=='asset')throw Error('first authored payload incomplete');if(vm.runInContext('cursorSequence',ctx)!==2)throw Error('cursor advanced before renderer ack');
  emit({source:frame.contentWindow,origin:'null',data:{type:'house-design-applied',session_id:'s1',document_token:'doc-one',revision:'wrong',event_sequence:4,identity_preserved:true}});if(vm.runInContext('cursorSequence',ctx)!==2)throw Error('mismatched acknowledgement advanced cursor');
  fire(1600);await ready(()=>sent.length===2);if(sent[1].event_sequence!==3||sent[1].revision!=='r1')throw Error('ack retry did not resend identical snapshot');
  emit({source:frame.contentWindow,origin:'null',data:{type:'house-design-applied',session_id:'s1',document_token:'doc-one',revision:'r1',event_sequence:3,stage:'sketch',intent:'Низкое крыло',phase:'Эскиз',identity_preserved:true}});await ready(()=>updates.length===1);if(vm.runInContext('cursorSequence',ctx)!==3)throw Error('exact ACK did not advance cursor');
  reply(updates.shift(),{content:[{type:'text',text:'{}'}],_meta:{houseDesign:{session_id:'s1',events:[event('update',4,'Форма','r2','form','Уточняю вход')],assets:{}}}});await ready(()=>sent.length===3);if(sent[2].event_sequence!==4||sent[2].presentation.intent!=='Уточняю вход'||frame.assignments!==placeholderAssignments+1)throw Error('later update replaced doc or lost event caption');
  emit({source:frame.contentWindow,origin:'null',data:{type:'house-design-applied',session_id:'s1',document_token:'doc-one',revision:'r2',event_sequence:4,stage:'form',intent:'Уточняю вход',phase:'Форма',identity_preserved:true}});await ready(()=>updates.length===1);if(vm.runInContext('cursorSequence',ctx)!==4)throw Error('form ACK cursor mismatch');const oldPending=updates.shift();
  emit({source:host,origin:'https://host.test',data:{method:'ui/notifications/tool-result',params:{_meta:{houseDesign:{session_id:'s2',bind_mode:'new',cursor_sequence:0}}}}});await ready(()=>statuses.length===1&&updates.length===1);const staleAck={type:'house-design-applied',session_id:'s1',document_token:'doc-one',revision:'r2',event_sequence:4,identity_preserved:true};emit({source:frame.contentWindow,origin:'null',data:staleAck});if(vm.runInContext('activeSession',ctx)!=='s2'||vm.runInContext('cursorSequence',ctx)!==0)throw Error('stale old-session ACK crossed generation');
  const s2status=statuses.shift(),s2events=updates.shift();reply(oldPending,{content:[{type:'text',text:'{}'}],_meta:{houseDesign:{session_id:'s1',events:[],assets:{}}}});await wait(2);if(!vm.runInContext('eventFetchInFlight',ctx))throw Error('stale old fetch cleared new session in-flight guard');reply(s2status,{content:[{type:'text',text:JSON.stringify({session:{session_id:'s2',phase:'Новая сессия',state:'designing',completed_actions:0,snapshot:null}})}]});reply(s2events,{content:[{type:'text',text:'{}'}],_meta:{houseDesign:{session_id:'s2',events:[],assets:{}}}});await wait(3);if(frame.srcdoc.includes('<html>sketch</html>'))throw Error('session switch retained old document');process.exit(0)
 }catch(e){console.error(e);process.exit(1)}
});
'''
        completed = subprocess.run(["node", "-e", harness], input=script, text=True, encoding="utf-8", capture_output=True, timeout=8)
        self.assertEqual(completed.returncode, 0, completed.stderr)
    def test_stdio_design_session_transport_preserves_metadata_and_revisions(self):
        begin_request_id = str(uuid.uuid4())
        other_request_id = str(uuid.uuid4())
        async def exercise():
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            bootstrap = ("import sys,asyncio;from pathlib import Path;"
                         f"sys.path.insert(0,r'{Path(__file__).resolve().parent}');import server;"
                         f"server.ROOT=Path(r'{self.root}');asyncio.run(server.run_stdio())")
            params = StdioServerParameters(command=sys.executable, args=["-c", bootstrap], cwd=str(self.root))
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                    self.assertEqual(tools["begin_house_design_session"].meta["ui"]["resourceUri"], app.UI_URI)
                    self.assertFalse(tools["update_house_design"].annotations.readOnlyHint)
                    short_id = await session.call_tool("begin_house_design_session", {"title": "Wire tower", "request_id": "begin-wire"})
                    self.assertTrue(short_id.isError)
                    self.assertIn("Input validation error", short_id.content[0].text)
                    begun = await session.call_tool("begin_house_design_session", {"title": "Wire tower", "request_id": begin_request_id})
                    self.assertFalse(begun.isError)
                    sid = begun.structuredContent["session_id"]
                    self.assertEqual(begun.meta["houseDesign"]["session_id"], sid)
                    duplicate_begin = await session.call_tool("begin_house_design_session", {"title": "Wire tower", "request_id": begin_request_id})
                    self.assertEqual(duplicate_begin.structuredContent["session_id"], sid)
                    phase = await session.call_tool("set_house_design_phase", {"session_id": sid, "request_id": "phase-wire", "phase": "Планирую секции"})
                    self.assertEqual(phase.structuredContent["snapshot"], None)
                    spec = {"schema": "custodian.enshrouded.house.v1", "name": "Wire tower", "title": "Основание",
                            "materials": {"stone": "Block_T3_Stone_CastleWall_REWARD"},
                            "parts": [{"op": "box", "at": [0, 0, 0], "size": [2, 2, 1], "mat": "stone"}]}
                    first = await session.call_tool("update_house_design", {"session_id": sid, "request_id": "wire-model-1", "expected_revision": None, "phase": "Основание", "spec": spec})
                    self.assertFalse(first.isError, first.content)
                    rev1 = first.structuredContent["revision"]
                    viewer1 = await session.call_tool("get_house_design_viewer", {"session_id": sid, "snapshot_revision": rev1, "event_sequence": first.structuredContent["snapshot"]["event_sequence"]})
                    self.assertFalse(viewer1.isError, viewer1.content)
                    self.assertEqual(viewer1.meta["houseDesign"]["revision"], rev1)
                    self.assertEqual(viewer1.meta["houseDesign"]["session_id"], sid)
                    self.assertIn("OrbitControls.js", viewer1.meta["houseDesign"]["html"])
                    self.assertIn("Приблизить", viewer1.meta["houseDesign"]["html"])
                    second = await session.call_tool("update_house_design", {"session_id": sid, "request_id": "wire-model-2", "expected_revision": rev1, "phase": "Второй этаж", "append_parts": [{"op": "box", "at": [0, 0, 1], "size": [1, 1, 1], "mat": "stone"}]})
                    self.assertFalse(second.isError, second.content)
                    rev2 = second.structuredContent["revision"]
                    self.assertNotEqual(rev1, rev2)
                    viewer2 = await session.call_tool("get_house_design_viewer", {"session_id": sid, "snapshot_revision": rev2, "event_sequence": second.structuredContent["snapshot"]["event_sequence"]})
                    self.assertFalse(viewer2.isError, viewer2.content)
                    stale = await session.call_tool("update_house_design", {"session_id": sid, "request_id": "wire-stale", "expected_revision": rev1, "phase": "Старое основание", "append_parts": []})
                    self.assertTrue(stale.isError)
                    other = await session.call_tool("begin_house_design_session", {"title": "Isolated", "request_id": other_request_id})
                    other_id = other.structuredContent["session_id"]
                    self.assertNotEqual(other_id, sid)
                    isolated = await session.call_tool("get_house_design_status", {"session_id": other_id})
                    isolated_state = json.loads(isolated.content[0].text)["session"]
                    self.assertEqual(isolated_state["session_id"], other_id)
                    self.assertIsNone(isolated_state["snapshot"])
                    invalid = await session.call_tool("get_house_design_viewer", {"session_id": other_id, "snapshot_revision": rev1, "event_sequence": first.structuredContent["snapshot"]["event_sequence"]})
                    self.assertTrue(invalid.isError)
        asyncio.run(exercise())

    def test_malformed_manifest_and_path_traversal_unknown(self):
        manifest = self.project / "output" / "sha256-manifest.json"
        manifest.write_text("not json")
        self.assertEqual(app.project_status(self.project)["evidence"], "unknown")
        manifest.write_text(json.dumps({"files": {"../secret": "00"}}))
        self.assertEqual(app.project_status(self.project)["evidence"], "unknown")

    def test_stdio_initialize_tools_and_resource(self):
        begin_request_id = str(uuid.uuid4())
        async def exercise():
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            bootstrap = ("import sys,asyncio;from pathlib import Path;"
                         f"sys.path.insert(0,r'{Path(__file__).resolve().parent}');import server;"
                         f"server.ROOT=Path(r'{self.root}');asyncio.run(server.run_stdio())")
            params = StdioServerParameters(command=sys.executable, args=["-c", bootstrap], cwd=str(self.root))
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    names = {x.name for x in tools.tools}
                    self.assertIn("open_house_design_app", names)
                    self.assertIn("get_house_design_status", names)
                    self.assertIn("start_house_design_preview", names)
                    self.assertIn("get_house_design_preview", names)
                    self.assertIn("get_house_design_viewer", names)
                    self.assertIn("begin_house_design_session", names)
                    self.assertIn("update_house_design", names)
                    descriptors = {x.name: x for x in tools.tools}
                    self.assertEqual(descriptors["start_house_design_preview"].meta["ui"]["visibility"], ["app"])
                    self.assertEqual(descriptors["get_house_design_viewer"].meta["ui"]["visibility"], ["app"])
                    self.assertEqual(descriptors["begin_house_design_session"].meta["ui"]["resourceUri"], app.UI_URI)
                    opened = await session.call_tool("open_house_design_app", {})
                    self.assertIsNone(opened.structuredContent["session"])
                    self.assertIsNone(opened.meta)
                    begun = await session.call_tool("begin_house_design_session", {"title": "Reopen test", "request_id": begin_request_id})
                    self.assertFalse(begun.isError)
                    session_id = begun.structuredContent["session_id"]
                    reopened = await session.call_tool("open_house_design_app", {"session_id": session_id})
                    self.assertEqual(reopened.structuredContent["session_id"], session_id)
                    self.assertEqual(reopened.structuredContent["session"]["session_id"], session_id)
                    self.assertEqual(reopened.meta["houseDesign"]["session_id"], session_id)
                    listed = await session.call_tool("list_house_design_projects", {})
                    project_list = json.loads(listed.content[0].text)["projects"]
                    invalid = await session.call_tool("get_house_design_viewer", {})
                    self.assertTrue(invalid.isError)
                    self.assertIn("Input validation error", invalid.content[0].text)
                    resource = await session.read_resource(app.UI_URI)
                    self.assertIn("Домострой", resource.contents[0].text)
                    self.assertEqual(resource.contents[0].meta["openai/ui"]["availableDisplayModes"], ["inline", "fullscreen"])
        asyncio.run(exercise())

    def test_http_loopback_status_and_allowlisted_preview(self):
        async def exercise():
            server = await asyncio.start_server(app.http_client, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            async with server:
                for path, expected in (("/api/status", b"mage-tower-80-test"), ("/preview/mage-tower-80-test", b"viewer"), ("/preview/..%2Fsecret", b"Preview unavailable")):
                    reader, writer = await asyncio.open_connection("127.0.0.1", port)
                    writer.write(f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode()); await writer.drain()
                    response = await asyncio.wait_for(reader.read(), 5)
                    self.assertIn(expected, response)
                    writer.close(); await writer.wait_closed()
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.write(b"GET /api/status HTTP/1.1\r\nHost: attacker.example\r\n\r\n"); await writer.drain()
                self.assertIn(b"403", await asyncio.wait_for(reader.read(), 5))
                writer.close(); await writer.wait_closed()
            with self.assertRaises(ValueError):
                await app.serve("0.0.0.0", 0)
        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
