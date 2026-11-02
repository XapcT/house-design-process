"""Read-only local MCP App and preview server for house-design evidence."""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
from datetime import datetime, timezone
import hashlib
import html
import json
import re
import sys
import threading
import uuid
from pathlib import Path
from urllib.parse import urlparse
import jsonschema

from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, CallToolResult, ToolAnnotations

ROOT = Path(__file__).resolve().parents[2]
APP = Path(__file__).resolve().parent
UI_URI = "ui://house-design-app/index.html"
PROJECT_PREFIX = "mage-tower-80-"
VIEWER_SCRIPTS = Path("D:/Projects/castle-viewer/src/scripts")
if str(VIEWER_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(VIEWER_SCRIPTS))
import enshrouded_house as house_core  # trusted existing source, read-only
import enshrouded_house_render as house_render  # trusted existing geometry renderer, read-only
import enshrouded_view as house_view  # original game-asset 3D viewer, read-only

try:
    from design_session import (begin_design, update_design, set_phase, finish_design,
                                get_state, get_snapshot, get_snapshot_event, get_updates)
except ImportError:
    # Keep unrelated read-only project status usable while the session module is staged.
    begin_design = update_design = set_phase = finish_design = None
    get_state = get_snapshot = get_snapshot_event = get_updates = None

JOBS_DIR = ROOT / "work" / "house-app-integration" / "jobs"
JOB_LOCK = threading.RLock()
CURRENT_JOB: dict | None = None
CURRENT_REQUEST: str | None = None
PREVIEW_DATA_URL: str | None = None
PREVIEW_META: dict | None = None
MAX_PARTS = 100000


def projects() -> list[Path]:
    work = ROOT / "work"
    if not work.is_dir():
        return []
    return sorted((p for p in work.glob("mage-tower-80-*" ) if p.is_dir() and not p.is_symlink()), key=lambda p: p.name, reverse=True)


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None


def project_status(project: Path) -> dict:
    allowed = {p.name: p for p in projects()}
    if project.name not in allowed or allowed[project.name] != project or project.is_symlink():
        return {"project": project.name, "evidence": "unknown", "reason": "Проект не найден в разрешённом списке."}
    output = project / "output"
    design_path, report_path, manifest_path = (output / n for n in (f"{project.name}.json", "house-check-report.json", "sha256-manifest.json"))
    try:
        design_bytes = design_path.read_bytes()
        design_revision = hashlib.sha256(design_bytes).hexdigest()
        design = json.loads(design_bytes)
    except (OSError, ValueError, UnicodeError):
        design_bytes, design_revision, design = b"", None, None
    identity = {"project": project.name, "revision": design_revision,
                "title": (design.get("title") or project.name) if isinstance(design, dict) else project.name}
    report, manifest = _read(report_path), _read(manifest_path)
    if not all(isinstance(v, dict) for v in (design, report, manifest)):
        return {**identity, "evidence": "unknown", "reason": "Не хватает корректного design, checker report или SHA-256 manifest."}
    files = manifest.get("files")
    if not isinstance(files, dict):
        return {**identity, "evidence": "unknown", "reason": "Некорректная карта хешей в manifest."}
    mismatches = []
    newer = []
    for rel, expected in files.items():
        if not isinstance(rel, str) or not isinstance(expected, str) or Path(rel).is_absolute() or ".." in Path(rel).parts:
            return {**identity, "evidence": "unknown", "reason": "Manifest содержит недопустимый путь."}
        candidate = (project / Path(rel)).resolve()
        if project.resolve() not in candidate.parents or not candidate.is_file():
            mismatches.append(rel); continue
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest().upper()
        if digest != expected.upper():
            mismatches.append(rel)
        if candidate.stat().st_mtime_ns > manifest_path.stat().st_mtime_ns:
            newer.append(rel)
    # The design and checker report are evidence dependencies even if a stale manifest omits them.
    for path in (design_path, report_path):
        if path.stat().st_mtime_ns > manifest_path.stat().st_mtime_ns:
            newer.append(str(path.relative_to(project)))
    required = {str(design_path.relative_to(project)), str(report_path.relative_to(project))}
    generators = list(project.glob("gen_*.py"))
    required.update(str(p.relative_to(project)) for p in generators)
    if not required.issubset({str(Path(x)) for x in files}):
        mismatches.append("manifest missing design/checker/generator input")
    for path in generators:
        if path.stat().st_mtime_ns > manifest_path.stat().st_mtime_ns:
            newer.append(str(path.relative_to(project)))
    report_shape_ok = (report.get("status") in ("PASS", "FAIL") and isinstance(report.get("errors"), list)
                       and isinstance(report.get("warnings"), list)
                       and all(isinstance(report.get(key), int) and report[key] >= 0 for key in ("block_voxels", "prop_count"))
                       and not (report.get("status") == "PASS" and report.get("errors")))
    valid = not mismatches and not newer and report_shape_ok
    check = report.get("status") if valid else "STALE / UNKNOWN"
    activity_inputs = [design_path, *generators]
    activity = max((p.stat().st_mtime for p in activity_inputs if p.is_file()), default=None)
    if newer or any("generator" in x for x in mismatches):
        stage = "Проект дорабатывается; нужна новая проверка"
    elif valid and report.get("status") == "FAIL":
        stage = "Проверка выявила ошибки"
    elif valid:
        stage = "Локальная проверка пройдена; визуальная приёмка не подтверждена"
    elif report_shape_ok and mismatches:
        stage = "Проект изменён; нужна новая проверка"
    else:
        stage = "Ожидание данных"
    blueprint = output / "blueprint"
    export_files = sorted(blueprint.glob("*.sfbp")) if blueprint.is_dir() else []
    return {
        "project": project.name,
        "revision": design_revision,
        "title": design.get("title") or project.name,
        "description": design.get("description", ""),
        "evidence": "current" if valid else "stale" if mismatches or newer else "unknown",
        "checker": check,
        "stage": stage,
        "last_activity_utc": datetime.fromtimestamp(activity, timezone.utc).isoformat() if activity is not None else None,
        "generated_utc": report.get("generated_utc"),
        "errors": report.get("errors") if isinstance(report.get("errors"), list) else None,
        "warnings": report.get("warnings") if isinstance(report.get("warnings"), list) else None,
        "block_voxels": report.get("block_voxels"), "prop_count": report.get("prop_count"),
        "export_files_found": len(export_files), "export_validation": "Файлы экспорта найдены; игровой импорт и содержимое не проверены." if export_files else "Файлы экспорта не найдены.",
        "construction_sequence": report.get("construction_sequence") if isinstance(report.get("construction_sequence"), list) else design.get("construction_sequence") if isinstance(design.get("construction_sequence"), list) else [],
        "preview": f"/preview/{project.name}", "mismatches": mismatches[:20], "newer_inputs": sorted(set(newer))[:20],
        "preview_fingerprint": _preview_fingerprint(output / "diagnostic-preview.html"),
        "preview_freshness": "Файл viewer не входит в manifest; соответствие текущей модели не подтверждено.",
        "reason": None if valid else "PASS не считается текущим: хеши не совпали или inputs новее manifest." if mismatches or newer else "Отчёт не содержит допустимого статуса PASS/FAIL.",
    }


def all_status(session_id: str | None = None) -> dict:
    if session_id:
        if not get_state:
            raise RuntimeError("Design session module is unavailable")
        return {"projects": [], "job": None, "session": get_state(ROOT, session_id)}
    result = []
    for project in projects():
        try:
            result.append(project_status(project))
        except (OSError, ValueError, TypeError):
            result.append({"project": project.name, "evidence": "unknown", "checker": "UNKNOWN", "reason": "Файлы проекта изменяются или недоступны; обновите данные позже."})
    with JOB_LOCK:
        job = dict(CURRENT_JOB) if CURRENT_JOB else _read(JOBS_DIR / "job.json")
        if job and not CURRENT_JOB and job.get("state") in ("queued", "running"):
            job["state"] = "interrupted"
            job["phase"] = "Процесс перезапущен; сборка остановлена"
    return {"projects": result, "job": job, "session": None}


def _job_write(event: str | None = None) -> None:
    """Persist one bounded current job plus an append-only operation event log."""
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    assert CURRENT_JOB is not None
    CURRENT_JOB["updated_utc"] = datetime.now(timezone.utc).isoformat()
    tmp = JOBS_DIR / "job.tmp"
    tmp.write_text(json.dumps(CURRENT_JOB, ensure_ascii=False), encoding="utf-8")
    tmp.replace(JOBS_DIR / "job.json")
    if event is not None:
        with (JOBS_DIR / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"operation_id": CURRENT_JOB["operation_id"], "event": event, **CURRENT_JOB}, ensure_ascii=False) + "\n")


def _append_job_event(event: str) -> None:
    with JOB_LOCK:
        _job_write(event)


def _viewer_snapshot(spec: dict) -> bytes:
    """Render actual partial geometry through the repository's existing isometric renderer."""
    image = house_render.render(spec, view="ne", scale=5)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _persist_preview(image_bytes: bytes, job: dict, seq: int) -> None:
    global PREVIEW_DATA_URL, PREVIEW_META
    with JOB_LOCK:
        if not CURRENT_JOB or CURRENT_JOB["operation_id"] != job["operation_id"]:
            return
        PREVIEW_DATA_URL = "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")
        PREVIEW_META = {"operation_id": job["operation_id"], "revision": job["revision"], "sequence": seq}
        snapshot = JOBS_DIR / "preview.tmp"
        snapshot.write_bytes(image_bytes)
        snapshot.replace(JOBS_DIR / "preview.png")
        CURRENT_JOB["preview_seq"] = seq
        CURRENT_JOB["preview_error"] = None
        _job_write("preview_ready")


def _run_geometry_job(job: dict, source_bytes: bytes, spec: dict) -> None:
    global CURRENT_JOB
    parts = spec.get("parts") or []
    total = len(parts)
    interval = max(1, total // 8)

    class ProgressHouse(house_core.House):
        def _apply(self, part):
            super()._apply(part)
            with JOB_LOCK:
                CURRENT_JOB["completed"] += 1
                CURRENT_JOB["sequence"] = CURRENT_JOB["completed"]
                CURRENT_JOB["phase"] = "Сборка геометрии чертежа"
                _job_write("operation_completed")
                done = CURRENT_JOB["completed"]
            if done == 1 or done % interval == 0 or done == total:
                partial = dict(spec)
                partial["parts"] = parts[:done]
                try:
                    snapshot = _viewer_snapshot(partial)
                    _persist_preview(snapshot, job, done)
                except Exception as exc:
                    with JOB_LOCK:
                        if CURRENT_JOB and CURRENT_JOB["operation_id"] == job["operation_id"]:
                            CURRENT_JOB["preview_error"] = f"Промежуточный вид недоступен: {type(exc).__name__}"
                            _job_write("preview_failed")

    try:
        with JOB_LOCK:
            job["state"] = "running"
            job["phase"] = "Сборка геометрии чертежа"
            job["total"] = total
            CURRENT_JOB = job
            _job_write("started")
        ProgressHouse(spec)
        with JOB_LOCK:
            if CURRENT_JOB and CURRENT_JOB["operation_id"] == job["operation_id"]:
                CURRENT_JOB["state"] = "completed"
                CURRENT_JOB["phase"] = "Сборка геометрии чертежа завершена"
                CURRENT_JOB["completed"] = total
                CURRENT_JOB["sequence"] = total
                _job_write("completed")
    except Exception as exc:
        with JOB_LOCK:
            if CURRENT_JOB and CURRENT_JOB["operation_id"] == job["operation_id"]:
                CURRENT_JOB["state"] = "failed"
                CURRENT_JOB["phase"] = "Сборка геометрии чертежа остановлена"
                CURRENT_JOB["error"] = f"{type(exc).__name__}: {exc}"
                try:
                    _job_write("failed")
                except OSError:
                    pass  # In-memory terminal state still releases admission.


def start_geometry_job(project_id: str, request_id: str) -> dict:
    global CURRENT_JOB, CURRENT_REQUEST, PREVIEW_DATA_URL, PREVIEW_META
    if not isinstance(project_id, str) or not re.fullmatch(r"mage-tower-80-[a-zA-Z0-9_-]{1,60}", project_id):
        raise ValueError("Недопустимый project_id")
    if not isinstance(request_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", request_id):
        raise ValueError("Недопустимый request_id")
    project = next((p for p in projects() if p.name == project_id), None)
    if project is None:
        raise ValueError("Проект отсутствует в allowlist")
    design_path = project / "output" / f"{project_id}.json"
    raw = design_path.read_bytes()
    revision = hashlib.sha256(raw).hexdigest()
    spec = json.loads(raw)
    if not isinstance(spec, dict) or spec.get("schema") != house_core.SCHEMA:
        raise ValueError("Недопустимая схема чертежа")
    parts = spec.get("parts")
    if not isinstance(parts, list) or not parts or len(parts) > MAX_PARTS:
        raise ValueError(f"Число операций должно быть от 1 до {MAX_PARTS}")
    with JOB_LOCK:
        if CURRENT_JOB and CURRENT_REQUEST == request_id:
            return dict(CURRENT_JOB)
        if CURRENT_JOB and CURRENT_JOB.get("state") in ("queued", "running"):
            raise RuntimeError("Сборка геометрии уже выполняется")
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        (JOBS_DIR / "events.jsonl").write_text("", encoding="utf-8")
        PREVIEW_DATA_URL = None
        PREVIEW_META = None
        job = {"operation_id": str(uuid.uuid4()), "project_id": project_id, "request_id": request_id,
               "revision": revision, "sequence": 0, "state": "queued", "phase": "Подготовка чертежа",
               "completed": 0, "total": len(parts), "error": None, "preview_seq": None,
               "preview_error": None, "updated_utc": datetime.now(timezone.utc).isoformat()}
        CURRENT_JOB = job
        CURRENT_REQUEST = request_id
        try:
            _job_write("queued")
            thread = threading.Thread(target=_run_geometry_job, args=(job, raw, spec), name=f"house-{job['operation_id'][:8]}", daemon=True)
            thread.start()
        except Exception as exc:
            job["state"] = "failed"
            job["phase"] = "Сборка геометрии чертежа остановлена"
            job["error"] = f"{type(exc).__name__}: {exc}"
            try:
                _job_write("failed")
            except OSError:
                pass  # Preserve the failure in memory when storage is unavailable.
            raise
        return dict(job)


def _preview_fingerprint(path: Path) -> str | None:
    try:
        stat = path.stat()
        return f"{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        return None


def _viewer_from_bytes(project_id: str, raw: bytes, session_id: str | None = None,
                       document_token: str | None = None) -> dict:
    """Build the original interactive viewer from immutable JSON bytes."""
    revision = hashlib.sha256(raw).hexdigest()
    spec = json.loads(raw)
    if not isinstance(spec, dict) or spec.get("schema") != house_core.SCHEMA:
        raise ValueError("Недопустимая схема чертежа")
    spec = dict(spec)
    spec["name"] = project_id
    data, names = house_view.build_data(spec)
    data.pop("report", None)
    head = ("<script>window.ASSETS=" + json.dumps(house_view.embedded(names)) + ";</script>\n<script>window.FORTS="
            + house_view._js({project_id: data}) + ";window.BUILDINGS="
            + house_view._js([{"id": project_id, "title": data.get("title") or project_id}])
            + ";window.CURRENT=" + house_view._js(project_id) + ";window.FORT=window.FORTS[window.CURRENT];"
            + "window.SWITCH=function(){};document.title=window.FORT.title||document.title;</script>\n")
    page = house_view._page(html.escape(str(data.get("title") or "Постройка"), quote=True), head, None)
    compact_view = "<style>html,body{margin:0!important;padding:0!important}.wrap{max-width:none!important;gap:0!important}.wrap>header,.wrap>.controls,.views>section:nth-child(2),.wrap>.panel,.wrap>.facts,.wrap>footer{display:none!important}.views{display:block!important}.views>section:first-child{border:0!important;border-radius:0!important;min-height:72vh}#three{min-height:72vh!important;height:72vh!important}.ph{padding:5px 10px!important}.ph .hint{display:none!important}</style>"
    page = page.replace("</style>", "</style>" + compact_view, 1)
    page = page.replace("function place(file, cxv, cyv, z, rotY, fallbackSize) {", "function place(file, cxv, cyv, z, rotY, fallbackSize) { const __gd=window.__houseDesignGeneration||0;")
    page = page.replace("function placeExact(file, pos, q) {", "function placeExact(file, pos, q) { const __gd=window.__houseDesignGeneration||0;")
    page = page.replace("return loadModel(file).then(src => {", "return loadModel(file).then(src => { if(__gd!==window.__houseDesignGeneration)return;")
    page = page.replace("const obj = src.clone(true);", "const obj = src.clone(true);obj.traverse(x=>x.userData.houseAssetShared=true);")
    page = page.replace("const vox = [], solid = new Set(), doors = new Set();", "const vox = [], solid = new Set(), doors = new Set();")
    page = page.replace("const [[X0, Y0, Z0], [X1, Y1, Z1]] = D.bbox;", "let [[X0, Y0, Z0], [X1, Y1, Z1]] = D.bbox;")
    page = page.replace("const cx = (X0 + X1) / 2, cy = (Y0 + Y1) / 2;", "let cx = (X0 + X1) / 2, cy = (Y0 + Y1) / 2;")
    page = page.replace("const doorList = D.furn.filter", "let doorList = D.furn.filter")
    page = page.replace(
        "const controls = new THREE.OrbitControls(camera, renderer.domElement);",
        "const controls = new THREE.OrbitControls(camera, renderer.domElement);"
        + "window.__houseDesignOrbit={zoomIn(){const o=camera.position.clone().sub(controls.target).multiplyScalar(.8);camera.position.copy(controls.target).add(o);controls.update()},zoomOut(){const o=camera.position.clone().sub(controls.target).multiplyScalar(1.25);camera.position.copy(controls.target).add(o);controls.update()},rotateLeft(){camera.position.sub(controls.target).applyAxisAngle(new THREE.Vector3(0,1,0),.18).add(controls.target);controls.update()},rotateRight(){camera.position.sub(controls.target).applyAxisAngle(new THREE.Vector3(0,1,0),-.18).add(controls.target);controls.update()},reset(){camera.position.set(cx-70,95,-(cy-120));controls.target.set(cx,4,-cy);controls.update()}};"
        + "const __housePose=()=>document.documentElement.dataset.cameraPose=[camera.position.x,camera.position.y,camera.position.z].map(v=>v.toFixed(2)).join(',');controls.addEventListener('change',__housePose);__housePose();",
        1,
    )
    page = page.replace('id="cut" checked', 'id="cut"', 1)
    controls_html = ('<div id="house-design-controls" role="group" aria-label="Управление камерой">'
                     '<button type="button" aria-label="Повернуть влево" onclick="window.__houseDesignOrbit.rotateLeft()">↶</button>'
                     '<button type="button" aria-label="Повернуть вправо" onclick="window.__houseDesignOrbit.rotateRight()">↷</button>'
                     '<button type="button" aria-label="Приблизить" onclick="window.__houseDesignOrbit.zoomIn()">+</button>'
                     '<button type="button" aria-label="Отдалить" onclick="window.__houseDesignOrbit.zoomOut()">−</button>'
                     '<button type="button" aria-label="Сбросить вид" onclick="window.__houseDesignOrbit.reset()">↺</button></div>'
                     '<style>#house-design-controls{position:fixed;z-index:30;right:12px;top:12px;display:flex;gap:4px;background:#1b241ed9;padding:5px;border-radius:8px}#house-design-controls button{width:32px;height:30px;border:1px solid #607060;border-radius:5px;background:#27352b;color:#fff;font:18px system-ui;cursor:pointer}#house-design-controls button:focus-visible{outline:2px solid #a9d18e}</style>')
    page += controls_html
    # Keep the original viewer's document, renderer, camera and controls alive.
    # The app updates only a design-owned voxel root using its original material loader.
    live_script = r'''<script>
(()=>{
  const BASE=sessionStorage.getItem("houseDesignSession")||"";
  const materialCache=new Map(); let root=null, lastRevision="", lastSequence=-1, liveGeneration=0;
  const DOCUMENT_TOKEN=__DOCUMENT_TOKEN__;
  const runtimeIds={document:"doc-"+Math.random().toString(36).slice(2),renderer:"renderer-"+Math.random().toString(36).slice(2),scene:"scene-"+Math.random().toString(36).slice(2),camera:"camera-"+Math.random().toString(36).slice(2),controls:"controls-"+Math.random().toString(36).slice(2)};
  const baseNodes=new Set([...scene.children].filter(o=>o.isLight||o===ground||o===camera));
  function disposeTree(tree){tree.traverse(o=>{if(o.geometry&&o.geometry!==unitBox&&!o.userData.houseAssetShared)o.geometry.dispose();if(o.material&&!o.userData.houseAssetShared){for(const m of(Array.isArray(o.material)?o.material:[o.material]))if(m&&!m.userData.houseAssetShared&&!m.userData.houseDesignShared){for(const k of["map","normalMap","emissiveMap","roughnessMap","metalnessMap","aoMap","alphaMap"])m[k]?.dispose?.();m.dispose()}}})}
  function clearRoot(){if(!root)return;scene.remove(root);disposeTree(root);root.clear();root=null}
    // Untextured form grid follows voxel dimensions; game textures remain unmodified.
    // A shader overlay avoids allocating line geometry for every block in large projects.
    function readableSurface(material){
      material.onBeforeCompile=shader=>{
        shader.vertexShader="varying vec3 hdGridPosition;varying vec3 hdGridNormal;\n"+shader.vertexShader;
        shader.vertexShader=shader.vertexShader.replace('#include <begin_vertex>',`#include <begin_vertex>
          vec4 hdPosition=vec4(position,1.0);
          #ifdef USE_INSTANCING
          hdPosition=instanceMatrix*hdPosition;
          #endif
          hdGridPosition=(modelMatrix*hdPosition).xyz;
          hdGridNormal=normal;`);
        shader.fragmentShader="varying vec3 hdGridPosition;varying vec3 hdGridNormal;\n"+shader.fragmentShader;
        shader.fragmentShader=shader.fragmentShader.replace('#include <dithering_fragment>',`#include <dithering_fragment>
          vec3 hdGrid=abs(fract(hdGridPosition-0.5)-0.5)/max(fwidth(hdGridPosition),vec3(0.0001));
          vec3 hdNormal=abs(hdGridNormal);
          float hdDistance=hdNormal.y>0.5?min(hdGrid.x,hdGrid.z):(hdNormal.x>0.5?min(hdGrid.y,hdGrid.z):min(hdGrid.x,hdGrid.y));
          float hdPixelSpan=hdNormal.y>0.5?max(fwidth(hdGridPosition.x),fwidth(hdGridPosition.z)):(hdNormal.x>0.5?max(fwidth(hdGridPosition.y),fwidth(hdGridPosition.z)):max(fwidth(hdGridPosition.x),fwidth(hdGridPosition.y)));
          float hdInk=(1.0-smoothstep(0.35,0.85,hdDistance))*0.38*(1.0-smoothstep(0.15,0.45,hdPixelSpan));
          gl_FragColor.rgb=mix(gl_FragColor.rgb,vec3(0.12,0.17,0.20),hdInk);`);
      };
      material.customProgramCacheKey=()=>"house-design-surface-grid-v1";
      return material;
    }
  function acknowledge(msg){parent.postMessage({type:"house-design-applied",session_id:BASE,document_token:DOCUMENT_TOKEN,revision:msg.revision,event_sequence:msg.event_sequence,canvas_id:window.__houseDesignCanvasId,document_id:runtimeIds.document,renderer_id:runtimeIds.renderer,scene_id:runtimeIds.scene,camera_id:runtimeIds.camera,controls_id:runtimeIds.controls,stage:msg.presentation?.stage||"materials",intent:msg.presentation?.intent||"",phase:msg.presentation?.phase||"",identity_preserved:document===runtimeRefs.document&&renderer===runtimeRefs.renderer&&scene===runtimeRefs.scene&&camera===runtimeRefs.camera&&controls===runtimeRefs.controls&&renderer.domElement===runtimeRefs.canvas},"*")}
  function fail(msg,error){parent.postMessage({type:"house-design-failed",session_id:BASE,document_token:DOCUMENT_TOKEN,revision:msg?.revision,event_sequence:msg?.event_sequence,error:String(error?.message||error||"invalid update")},"*")}
  function apply(msg){
    if(!msg||msg.type!=="house-design-update"||msg.session_id!==BASE||msg.document_token!==DOCUMENT_TOKEN)return;
    if(!/^([a-f0-9]{64})$/.test(msg.revision||"")||!Number.isSafeInteger(msg.event_sequence)){fail(msg,"invalid revision or event sequence");return}
    if(msg.event_sequence===lastSequence){if(msg.revision===lastRevision)acknowledge(msg);else fail(msg,"event sequence is already bound to another revision");return}
    if(msg.event_sequence<lastSequence){fail(msg,"stale event sequence");return}
    const d=msg.data,stage=msg.presentation?.stage||"materials";
    if(!d||!Array.isArray(d.v)||!Array.isArray(d.mats)||!Array.isArray(d.bbox)||!['sketch','form','materials'].includes(stage)){fail(msg,"invalid viewer data or presentation");return}
    try {
    if(msg.assets&&typeof msg.assets==="object"&&!Array.isArray(msg.assets)){for(const [name,value] of Object.entries(msg.assets)){if(/^[A-Za-z0-9._-]{1,180}$/.test(name)&&typeof value==="string"&&value.length<25000000){ASSETS[name]=value;HAVE.add(name)}}}
    // First update removes the viewer's initial snapshot content while retaining scene lighting and earth.
    liveGeneration++;window.__houseDesignGeneration=liveGeneration;if(walker)setWalking(false);walkBtn.disabled=true;
    Object.assign(D,{v:d.v,d:d.d||[],furn:d.furn||[],openings:d.openings||[],altar:d.altar,bbox:d.bbox,mats:d.mats,matNames:d.matNames,matColor:d.matColor});
    vox.splice(0);solid.clear();doors.clear();colMin.clear();for(const [x,y,z,n,m] of D.v)for(let i=0;i<n;i++){vox.push([x+i,y,z,m]);solid.add(key(x+i,y,z));const k=(x+i)+","+y;if(!colMin.has(k)||z<colMin.get(k))colMin.set(k,z)}for(const [x,y,z,n] of D.d)for(let i=0;i<n;i++)doors.add(key(x+i,y,z));vox.sort((a,b)=>a[2]-b[2]);
    [[X0,Y0,Z0],[X1,Y1,Z1]]=D.bbox;cx=(X0+X1)/2;cy=(Y0+Y1)/2;zIn.min=Z0;zIn.max=Z1;if(+zIn.value<Z0||+zIn.value>Z1)zIn.value=Z1;
    blockMeshes.length=0;models.length=0;plainBoxes.length=0;if(!root){for(const o of [...scene.children])if(!baseNodes.has(o)){scene.remove(o);disposeTree(o)}}else clearRoot();
    root=new THREE.Group();root.name="house-design-live-root";scene.add(root);
    const cellsByMat=new Map();
    for(const [x,y,z,n,materialIndex] of d.v)for(let i=0;i<n;i++){let a=cellsByMat.get(materialIndex);if(!a)cellsByMat.set(materialIndex,a=[]);a.push([x+i,y,z])}
    let expectedInstances=0,actualInstances=0;
    for(let materialIndex=0;materialIndex<d.mats.length;materialIndex++){const alias=d.mats[materialIndex],cells=cellsByMat.get(materialIndex)||[];if(!cells.length)continue;
      expectedInstances+=cells.length;
      const materialKey=stage+":"+alias+":"+d.matNames[alias]+":"+d.matColor[alias];let mats=materialCache.get(materialKey);if(!mats){
        const name=d.matNames[alias],colour=d.matColor[alias];
        if(stage==="materials"){const side=blockMaterial(name,"side",colour),top=blockMaterial(name,"top",colour);mats=[side,side,top,top,side,side]}
        else mats=stage==="sketch"?new THREE.MeshBasicMaterial({color:0x53655a,wireframe:true}):new THREE.MeshLambertMaterial({color:0x9daeb5});
        if(stage==="form"&&!/^Terrain_/.test(d.matNames[alias]))for(const mat of new Set(Array.isArray(mats)?mats:[mats]))readableSurface(mat);
        for(const mat of(Array.isArray(mats)?mats:[mats]))mat.userData.houseDesignShared=true;for(const [k,old] of materialCache){if(k.startsWith(stage+":"+alias+":")){for(const mat of new Set(Array.isArray(old)?old:[old])){mat.map?.dispose();mat.normalMap?.dispose();mat.dispose()}materialCache.delete(k)}}materialCache.set(materialKey,mats);
      }
      const mesh=new THREE.InstancedMesh(unitBox,mats,cells.length),mx=new THREE.Matrix4();
      cells.sort((a,b)=>a[2]-b[2]);cells.forEach((c,i)=>{mx.makeTranslation(c[0]+.5,c[2]+.5,-(c[1]+.5));mesh.setMatrixAt(i,mx)});
      mesh.userData.zs=cells.map(c=>c[2]);mesh.userData.terrain=/^Terrain_/.test(d.matNames[alias]);mesh.instanceMatrix.needsUpdate=true;root.add(mesh);blockMeshes.push(mesh);actualInstances+=mesh.count;
    }
    if(expectedInstances!==Number(d.total)||actualInstances!==expectedInstances||!root.children.some(o=>o.isInstancedMesh&&o.count>0))throw Error("viewer geometry instance count mismatch: expected "+d.total+", built "+actualInstances);
    // Reuse the original viewer's GLTF placement helpers for authored openings, altar and props.
    const adopt=(fn)=>{const before=new Set(scene.children),result=fn();for(const o of [...scene.children])if(!before.has(o)){scene.remove(o);root.add(o)}return result};
    const [abx,aby]=d.altar.box;if(d.altar.present)adopt(()=>place(d.altar.model,abx/2,aby/2,-d.altar.box[2],0,[8,8,6]));
    const hole={door_frame_2m:[2,0,"door_wooden.glb"],door_frame_4m:[4,0,"door_wooden.glb"],window_frame_2m:[2,2,"window_wooden_frame.glb"],window_frame_4m:[4,2,"window_wooden_frame.glb"]};
    for(const o of d.openings||[]){if(o.t==="cut_window"){const [ox,oy,oz]=o.at,[sx,sy]=o.size;adopt(()=>place("window_wooden_frame.glb",ox+sx/2,oy+sy/2,oz,sx<sy?0:Math.PI/2));continue}const [along,up,file]=hole[o.t]||[];if(!file)continue;const [ox,oy,oz]=o.at,turned=o.rot===90||o.rot===270;adopt(()=>place(file,turned?ox+.5:ox+along,turned?oy+along:oy+.5,oz+up,turned?Math.PI/2:0));}
    for(const f of d.furn||[]){const [fx,fy,fz]=f.at,[sx,sy,sz]=f.size;if(f.model&&f.pos){adopt(()=>placeExact(f.model,f.pos,f.rot_xyzw));models[models.length-1].userData.furn=true;models[models.length-1].userData.item=f}else if(f.model){adopt(()=>place(f.model,fx+sx/2,fy+sy/2,fz,(f.rot||0)*Math.PI/180,[sx,sy,sz]));models[models.length-1].userData.furn=true;models[models.length-1].userData.item=f}else{const box=new THREE.Mesh(new THREE.BoxGeometry(sx-.1,sz-.1,sy-.1),new THREE.MeshLambertMaterial({color:0x7d6a52}));box.position.set(fx+sx/2,fz+sz/2,-(fy+sy/2));box.userData.z=fz;box.userData.furn=true;root.add(box)}}
    doorList=D.furn.filter(f=>f.group==="door"||/door/i.test(f.prop||f.model||""));doorList.forEach(f=>{f._door=true});for(const f of doorList)for(const g of models)if(g.userData.item===f)f._group=g;
    applyCut();walkBtn.disabled=false;
    const [[x0,y0,z0],[x1,y1,z1]]=d.bbox;root.userData.bounds=[x0,y0,z0,x1,y1,z1];
    document.documentElement.dataset.designStage=stage;document.documentElement.dataset.designRevision=msg.revision;
    Object.assign(document.documentElement.dataset,{houseDesignDocumentId:runtimeIds.document,houseDesignRendererId:runtimeIds.renderer,houseDesignSceneId:runtimeIds.scene,houseDesignCameraId:runtimeIds.camera,houseDesignControlsId:runtimeIds.controls});
    document.documentElement.dataset.designEventSequence=String(msg.event_sequence);
    document.documentElement.dataset.cameraPose=[...camera.position.toArray(),...controls.target.toArray()].map(v=>v.toFixed(2)).join(",");
    renderer.domElement.dataset.houseDesignCanvasId=window.__houseDesignCanvasId;renderer.domElement.dataset.houseDesignRendererId=runtimeIds.renderer;
    const hint=document.getElementById("ph");if(hint)hint.textContent=msg.presentation?.intent||({sketch:"Эскиз и примерная масса",form:"Уточнение формы",materials:"Материалы игры"}[stage]);
    lastRevision=msg.revision;lastSequence=msg.event_sequence;acknowledge(msg);
    } catch(error) { fail(msg,error); }
  }
    window.__houseDesignCanvasId=window.__houseDesignCanvasId||("canvas-"+Math.random().toString(36).slice(2));
  const runtimeRefs={document,renderer,scene,camera,controls,canvas:renderer.domElement};
  addEventListener("message",e=>{if(e.source===parent)apply(e.data)});
  parent.postMessage({type:"house-design-ready",session_id:BASE,document_token:DOCUMENT_TOKEN,identity_preserved:document===runtimeRefs.document&&renderer===runtimeRefs.renderer&&scene===runtimeRefs.scene&&camera===runtimeRefs.camera&&controls===runtimeRefs.controls&&renderer.domElement===runtimeRefs.canvas},"*");
  window.__houseDesignCameraState=()=>({position:camera.position.toArray(),target:controls.target.toArray()});
})();
</script>'''.replace('const BASE=sessionStorage.getItem("houseDesignSession")||"";',
                  "const BASE=" + json.dumps(session_id or project_id) + ";").replace(
                      "const DOCUMENT_TOKEN=__DOCUMENT_TOKEN__;", "const DOCUMENT_TOKEN=" + json.dumps(document_token or uuid.uuid4().hex) + ";")
    page += live_script
    return {"project_id": project_id, "revision": revision, "html": page, "data": data}


def _interactive_viewer(project_id: str) -> dict:
    """Build the existing interactive viewer from one immutable saved-design snapshot."""
    if not isinstance(project_id, str) or not re.fullmatch(r"mage-tower-80-[a-zA-Z0-9_-]{1,60}", project_id):
        raise ValueError("Недопустимый project_id")
    project = next((p for p in projects() if p.name == project_id), None)
    if project is None:
        raise ValueError("Проект отсутствует в allowlist")
    return _viewer_from_bytes(project_id, (project / "output" / f"{project_id}.json").read_bytes())


def _session_viewer(session_id: str, revision: str, event_sequence: int) -> dict:
    state = get_state(ROOT, session_id) if get_state else None
    if not state or state.get("session_id") != session_id:
        raise ValueError("Сессия проектирования не найдена")
    raw = get_snapshot_event(ROOT, session_id, event_sequence, revision) if get_snapshot_event else None
    if not raw:
        raise ValueError("Событие не принадлежит указанному снимку этой сессии")
    document_token = uuid.uuid4().hex
    viewer = _viewer_from_bytes("house-session-" + re.sub(r"[^a-zA-Z0-9_-]", "", session_id), raw, session_id, document_token)
    presentation = dict(json.loads(raw).get("presentation") or {"stage": "materials"})
    events = get_updates(ROOT, session_id, event_sequence - 1, 1)
    if not events or events[0]["event_sequence"] != event_sequence or events[0]["event_type"] != "update" or events[0]["revision"] != revision:
        raise ValueError("Событие изменилось при построении точного снимка")
    event = events[0]
    presentation["phase"] = event.get("phase", "")
    return {**viewer, "session_id": session_id, "event_sequence": event_sequence,
            "document_token": document_token,
            "presentation": presentation}


def _session_updates(session_id: str, after_sequence: int, limit: int = 8) -> dict:
    """Build ordered viewer updates from immutable session events, not the latest-state poll."""
    if get_updates is None:
        raise RuntimeError("Ordered design update storage is unavailable")
    state = get_state(ROOT, session_id) if get_state else None
    if not state or state.get("session_id") != session_id:
        raise ValueError("Сессия проектирования не найдена")
    rows = get_updates(ROOT, session_id, after_sequence, limit)
    events, asset_names = [], set()
    for row in rows:
        if row["event_type"] != "update":
            events.append({"event_type": row["event_type"], "event_sequence": row["event_sequence"],
                           "phase": row["phase"]})
            continue
        spec = json.loads(row["raw"])
        spec = dict(spec); spec["name"] = "house-session-" + re.sub(r"[^a-zA-Z0-9_-]", "", session_id)
        data, names = house_view.build_data(spec)
        data.pop("report", None)
        asset_names.update(names)
        presentation = dict(spec.get("presentation") or {"stage": "materials"})
        presentation["phase"] = row["phase"]
        events.append({"event_type": "update", "revision": row["revision"], "event_sequence": row["event_sequence"],
                       "phase": row["phase"], "data": data, "presentation": presentation})
    assets = house_view.embedded(asset_names) if asset_names else {}
    return {"session_id": session_id, "events": events, "assets": assets,
            "after_sequence": after_sequence, "has_more": len(rows) == limit}


def _json_response(status: int, payload: object) -> bytes:
    body = json.dumps(payload, ensure_ascii=False).encode()
    return f"HTTP/1.1 {status} {'OK' if status == 200 else 'Not Found'}\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: {len(body)}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n".encode() + body


async def http_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
        line = await asyncio.wait_for(reader.readline(), 3)
        parts = line.decode("ascii", "replace").strip().split()
        headers = {}
        while True:
            raw = await asyncio.wait_for(reader.readline(), 3)
            if raw in (b"\r\n", b"\n", b""): break
            key, sep, value = raw.decode("ascii", "replace").partition(":")
            if sep: headers[key.strip().lower()] = value.strip()
        host = (urlparse("//" + headers.get("host", "")).hostname or "").lower()
        origin = headers.get("origin", "")
        parsed_origin = urlparse(origin) if origin else None
        if host not in ("127.0.0.1", "localhost", "::1") or (parsed_origin and (parsed_origin.scheme != "http" or parsed_origin.hostname not in ("127.0.0.1", "localhost", "::1") or parsed_origin.username or parsed_origin.password)):
            response = _json_response(403, {"error": "Loopback Host/Origin required"})
            writer.write(response); await writer.drain(); return
        path = urlparse(parts[1]).path if len(parts) >= 2 and parts[0] == "GET" else ""
        if path == "/api/status":
            response = _json_response(200, all_status())
        elif path == "/":
            body = (APP / "index.html").read_bytes()
            response = b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: " + str(len(body)).encode() + b"\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n" + body
        elif path.startswith("/preview/"):
            name = path.removeprefix("/preview/")
            p = next((p for p in projects() if p.name == name), None)
            f = (p / "output" / "diagnostic-preview.html") if p else None
            if not f or not f.is_file() or f.resolve().parent != (p / "output").resolve():
                response = _json_response(404, {"error": "Preview unavailable"})
            else:
                body = f.read_bytes()
                response = b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Security-Policy: default-src 'self' data: blob:; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' data: https://fonts.gstatic.com; script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net/npm/three@0.147.0/build/three.min.js https://cdn.jsdelivr.net/npm/three@0.147.0/examples/js/controls/OrbitControls.js https://cdn.jsdelivr.net/npm/three@0.147.0/examples/js/loaders/GLTFLoader.js; connect-src 'none'; frame-ancestors 'self'\r\nX-Content-Type-Options: nosniff\r\nContent-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body
        else:
            response = _json_response(404, {"error": "Not found"})
        writer.write(response); await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()
        try: await writer.wait_closed()
        except Exception: pass


async def serve(host: str = "127.0.0.1", port: int = 8765):
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("House Design App can bind only to loopback.")
    server = await asyncio.start_server(http_client, host, port)
    async with server:
        await server.serve_forever()


server = Server("house-design-app", version="0.2.0", instructions="In-memory house design sessions and read-only saved-design evidence. Session edits never write to the game world, server, saves, or project files.")


@server.list_tools()
async def list_tools():
    return [
        Tool(name="open_house_design_app", description="Открыть карточку сессии проектирования. Передайте session_id для повторного открытия конкретной сессии; без ID показывается пустое состояние.", inputSchema={"type": "object", "properties": {"session_id": {"type": "string"}}}, _meta={"ui": {"resourceUri": UI_URI, "visibility": ["app", "model"]}}),
        Tool(name="begin_house_design_session", description="Начать новую сессию проектирования дома в памяти; вызывает карточку app в текущем чате.", inputSchema={"type": "object", "properties": {"title": {"type": "string", "minLength": 1}, "request_id": {"type": "string", "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"}}, "required": ["title", "request_id"]}, annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False), _meta={"ui": {"resourceUri": UI_URI, "visibility": ["app", "model"]}}),
        Tool(name="update_house_design", description="Обновить текущую in-memory модель целевой сессии точной спецификацией или добавлением частей.", inputSchema={"type": "object", "properties": {"session_id": {"type": "string"}, "request_id": {"type": "string"}, "expected_revision": {"type": ["string", "null"]}, "phase": {"type": "string"}, "spec": {"type": "object"}, "append_parts": {"type": "array", "items": {"type": "object"}}}, "required": ["session_id", "request_id", "expected_revision", "phase"], "oneOf": [{"required": ["spec"]}, {"required": ["append_parts"]}]}, annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)),
        Tool(name="set_house_design_phase", description="Обновить фактический этап текущей сессии без изменения модели.", inputSchema={"type": "object", "properties": {"session_id": {"type": "string"}, "request_id": {"type": "string"}, "phase": {"type": "string"}, "state": {"type": "string", "enum": ["designing", "waiting", "blocked"]}}, "required": ["session_id", "request_id", "phase"]}, annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)),
        Tool(name="finish_house_design_session", description="Завершить текущую сессию проектирования с указанным фактическим статусом.", inputSchema={"type": "object", "properties": {"session_id": {"type": "string"}, "request_id": {"type": "string"}, "status": {"type": "string", "enum": ["finished", "failed", "cancelled", "interrupted"]}}, "required": ["session_id", "request_id"]}, annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)),
        Tool(name="get_house_design_status", description="Read saved-project evidence and, when supplied, this session's actual design state.", inputSchema={"type": "object", "properties": {"session_id": {"type": "string"}}}, annotations=ToolAnnotations(readOnlyHint=True), _meta={"ui": {"visibility": ["app"]}}),
        Tool(name="start_house_design_preview", description="Start a background geometry build from a known saved design; records real completed operations and intermediate views. This does not build in game.", inputSchema={"type": "object", "properties": {"project_id": {"type": "string"}, "request_id": {"type": "string"}}, "required": ["project_id", "request_id"]}, _meta={"ui": {"visibility": ["app"]}}),
        Tool(name="get_house_design_preview", description="Return the latest intermediate view for the current geometry operation to the app only.", inputSchema={"type": "object", "properties": {"operation_id": {"type": "string"}}, "required": ["operation_id"]}, annotations=ToolAnnotations(readOnlyHint=True), _meta={"ui": {"visibility": ["app"]}}),
        Tool(name="get_house_design_viewer", description="Bootstrap the original interactive viewer from one exact immutable session event.", inputSchema={"type": "object", "properties": {"session_id": {"type": "string"}, "snapshot_revision": {"type": "string"}, "event_sequence": {"type": "integer", "minimum": 1}}, "required": ["session_id", "snapshot_revision", "event_sequence"]}, annotations=ToolAnnotations(readOnlyHint=True), _meta={"ui": {"visibility": ["app"]}}),
        Tool(name="get_house_design_updates", description="Read the next ordered immutable model update after an app cursor.", inputSchema={"type": "object", "properties": {"session_id": {"type": "string"}, "after_sequence": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "const": 1}}, "required": ["session_id", "after_sequence"]}, annotations=ToolAnnotations(readOnlyHint=True), _meta={"ui": {"visibility": ["app"]}}),
        Tool(name="list_house_design_projects", description="List bounded known house design projects.", inputSchema={"type": "object", "properties": {}}),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name in {"begin_house_design_session", "update_house_design", "set_house_design_phase", "finish_house_design_session"}:
        action = {"begin_house_design_session": begin_design, "update_house_design": update_design,
                  "set_house_design_phase": set_phase, "finish_house_design_session": finish_design}[name]
        if action is None:
            return CallToolResult(content=[TextContent(type="text", text="Design session module is unavailable")], isError=True)
        try:
            if name == "begin_house_design_session":
                state = await asyncio.to_thread(action, ROOT, arguments.get("title"), arguments.get("request_id"))
            elif name == "update_house_design":
                state = await asyncio.to_thread(action, ROOT, arguments.get("session_id"), arguments.get("request_id"),
                                                arguments.get("expected_revision"), arguments.get("phase"),
                                                spec=arguments.get("spec"), append_parts=arguments.get("append_parts"))
            elif name == "set_house_design_phase":
                state = await asyncio.to_thread(action, ROOT, arguments.get("session_id"), arguments.get("request_id"),
                                                arguments.get("phase"), arguments.get("state", "designing"))
            else:
                state = await asyncio.to_thread(action, ROOT, arguments.get("session_id"), arguments.get("request_id"),
                                                arguments.get("status", "finished"))
        except (ValueError, RuntimeError, OSError, TypeError, KeyError) as exc:
            return CallToolResult(content=[TextContent(type="text", text=json.dumps({"error": str(exc)}, ensure_ascii=False))], isError=True)
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(state, ensure_ascii=False))],
                              structuredContent=state,
                              _meta={"houseDesign": {"session_id": state.get("session_id"), "bind_mode": "new", "cursor_sequence": 0}} if name == "begin_house_design_session" else None)
    if name == "get_house_design_status":
        try:
            return [TextContent(type="text", text=json.dumps(all_status(arguments.get("session_id")), ensure_ascii=False))]
        except (ValueError, RuntimeError, OSError, TypeError, KeyError) as exc:
            return CallToolResult(content=[TextContent(type="text", text=json.dumps({"error": str(exc)}, ensure_ascii=False))], isError=True)
    if name == "start_house_design_preview":
        try:
            job = await asyncio.to_thread(start_geometry_job, arguments.get("project_id"), arguments.get("request_id"))
        except (ValueError, RuntimeError, OSError) as exc:
            return [TextContent(type="text", text=json.dumps({"error": str(exc)}, ensure_ascii=False))]
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(job, ensure_ascii=False))])
    if name == "get_house_design_preview":
        operation_id = arguments.get("operation_id")
        with JOB_LOCK:
            job = dict(CURRENT_JOB) if CURRENT_JOB else None
            image_url = PREVIEW_DATA_URL
            meta = dict(PREVIEW_META) if PREVIEW_META else None
        if not job:
            job = _read(JOBS_DIR / "job.json")
        if job and job.get("preview_seq") and not meta:
            image_path = JOBS_DIR / "preview.png"
            if image_path.is_file():
                image_url = "data:image/png;base64," + base64.b64encode(image_path.read_bytes()).decode("ascii")
                meta = {"operation_id": job["operation_id"], "revision": job["revision"], "sequence": job["preview_seq"]}
        if not job or job.get("operation_id") != operation_id:
            return [TextContent(type="text", text="Нет снимка для этой операции.")]
        if not image_url or not meta:
            return [TextContent(type="text", text=json.dumps({"preview_pending": True, "job": job}, ensure_ascii=False))]
        return CallToolResult(content=[TextContent(type="text", text=json.dumps({"preview_ready": True, "sequence": meta["sequence"], "operation_id": operation_id}))], _meta={"houseDesign": {**meta, "previewDataUrl": image_url}})
    if name == "get_house_design_viewer":
        try:
            viewer = await asyncio.to_thread(_session_viewer, arguments.get("session_id"), arguments.get("snapshot_revision"), arguments.get("event_sequence"))
        except (ValueError, OSError, TypeError, KeyError) as exc:
            return CallToolResult(content=[TextContent(type="text", text=json.dumps({"error": str(exc)}, ensure_ascii=False))], isError=True)
        return CallToolResult(content=[TextContent(type="text", text=json.dumps({"loaded": True, "session_id": viewer["session_id"], "revision": viewer["revision"]}, ensure_ascii=False))], _meta={"houseDesign": viewer})
    if name == "get_house_design_updates":
        try:
            updates = await asyncio.to_thread(_session_updates, arguments.get("session_id"), arguments.get("after_sequence"), arguments.get("limit", 8))
        except (ValueError, RuntimeError, OSError, TypeError, KeyError) as exc:
            return CallToolResult(content=[TextContent(type="text", text=json.dumps({"error": str(exc)}, ensure_ascii=False))], isError=True)
        return CallToolResult(content=[TextContent(type="text", text=json.dumps({"session_id": updates["session_id"], "count": len(updates["events"]), "after_sequence": updates["after_sequence"]}, ensure_ascii=False))], _meta={"houseDesign": updates})
    if name == "list_house_design_projects":
        return [TextContent(type="text", text=json.dumps({"projects": [{"id": p.name, "title": (project_status(p).get("title") or p.name)} for p in projects()]}, ensure_ascii=False))]
    if name == "open_house_design_app":
        session_id = arguments.get("session_id")
        try:
            state = get_state(ROOT, session_id) if session_id and get_state else None
            payload = {"session_id": session_id, "session": state} if state else {"session": None}
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
                                  structuredContent=payload,
                                  _meta={"houseDesign": {"session_id": session_id, "bind_mode": "resume_latest", "cursor_sequence": state.get("sequence", 0), "snapshot": state.get("snapshot")}} if state else None)
        except (ValueError, RuntimeError, OSError, TypeError, KeyError) as exc:
            return CallToolResult(content=[TextContent(type="text", text=json.dumps({"error": str(exc)}, ensure_ascii=False))], isError=True)
    raise ValueError("Unknown tool")


@server.list_resources()
async def list_resources():
    from mcp.types import Resource
    return [Resource(uri=UI_URI, name="House Design App", mimeType="text/html;profile=mcp-app", _meta={"ui": {"prefersBorder": True}, "openai/ui": {"availableDisplayModes": ["inline", "fullscreen"]}})]


@server.read_resource()
async def read_resource(uri):
    if str(uri) != UI_URI:
        raise ValueError("Unknown resource")
    return [ReadResourceContents(content=(APP / "index.html").read_text(encoding="utf-8"), mime_type="text/html;profile=mcp-app")]


# This SDK version's ReadResourceContents helper omits per-content _meta. Override its
# registered low-level handler to expose the documented inline display mode on the resource.
from mcp import types as mcp_types


async def _read_app_resource_with_meta(req):
    if str(req.params.uri) != UI_URI:
        raise ValueError("Unknown resource")
    content = mcp_types.TextResourceContents(
        uri=UI_URI, mimeType="text/html;profile=mcp-app",
        text=(APP / "index.html").read_text(encoding="utf-8"),
        _meta={"ui": {"prefersBorder": True, "csp": {"resourceDomains": ["https://cdn.jsdelivr.net", "https://fonts.googleapis.com", "https://fonts.gstatic.com"]}},
               "openai/ui": {"availableDisplayModes": ["inline", "fullscreen"]}},
    )
    return mcp_types.ServerResult(mcp_types.ReadResourceResult(contents=[content]))


server.request_handlers[mcp_types.ReadResourceRequest] = _read_app_resource_with_meta


async def _call_tool_preserving_app_metadata(req):
    """Mirror the installed SDK wrapper while preserving CallToolResult._meta."""
    try:
        name = req.params.name
        arguments = req.params.arguments or {}
        tool = await server._get_cached_tool_definition(name)
        if tool:
            try:
                jsonschema.validate(instance=arguments, schema=tool.inputSchema)
            except jsonschema.ValidationError as exc:
                return server._make_error_result(f"Input validation error: {exc.message}")
        result = await call_tool(name, arguments)
        if isinstance(result, mcp_types.CallToolResult):
            if tool and tool.outputSchema is not None:
                if result.structuredContent is None:
                    return server._make_error_result("Output validation error: outputSchema defined but no structured output returned")
                try:
                    jsonschema.validate(instance=result.structuredContent, schema=tool.outputSchema)
                except jsonschema.ValidationError as exc:
                    return server._make_error_result(f"Output validation error: {exc.message}")
            return mcp_types.ServerResult(result)
        if isinstance(result, tuple) and len(result) == 2:
            content, structured = result
        elif isinstance(result, dict):
            structured = result
            content = [mcp_types.TextContent(type="text", text=json.dumps(result, indent=2))]
        elif hasattr(result, "__iter__"):
            content, structured = result, None
        else:
            return server._make_error_result(f"Unexpected return type from tool: {type(result).__name__}")
        if tool and tool.outputSchema is not None:
            if structured is None:
                return server._make_error_result("Output validation error: outputSchema defined but no structured output returned")
            try:
                jsonschema.validate(instance=structured, schema=tool.outputSchema)
            except jsonschema.ValidationError as exc:
                return server._make_error_result(f"Output validation error: {exc.message}")
        return mcp_types.ServerResult(mcp_types.CallToolResult(content=list(content), structuredContent=structured, isError=False))
    except Exception as exc:
        return server._make_error_result(str(exc))


server.request_handlers[mcp_types.CallToolRequest] = _call_tool_preserving_app_metadata


async def run_stdio():
    async with stdio_server() as (r, w):
        await server.run(r, w, server.create_initialization_options())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true", help="serve dashboard over loopback")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    asyncio.run(serve(args.host, args.port) if args.serve else run_stdio())
