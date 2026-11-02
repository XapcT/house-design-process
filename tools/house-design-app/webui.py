"""Loopback browser host for the existing MCP App UI and read-only tools."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

APP = Path(__file__).resolve().parent
MAX_BODY = 1024 * 1024
READ_TOOLS = {"get_house_design_status", "get_house_design_updates", "get_house_design_viewer", "open_house_design_app"}


def _plain(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True, exclude_none=True)
    if isinstance(value, (list, tuple)):
        return {"content": [_plain(item) for item in value]}
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return value

HOST_HTML = r'''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Домострой</title>
<style>body{margin:0;background:#111713;color:#edf2e9;font:14px system-ui}header{height:42px;display:flex;align-items:center;gap:8px;padding:0 10px;background:#1b241e}select{max-width:70vw;background:#111713;color:inherit;border:1px solid #526151;border-radius:5px;padding:5px}iframe{display:block;width:100%;height:calc(100vh - 42px);border:0}</style>
<header><label for="sessions">Сессия</label><select id="sessions"><option value="">Выберите авторскую сессию…</option></select></header><iframe id="app" title="Домострой" src="/ui"></iframe>
<script>
const sel=document.querySelector('#sessions'),frame=document.querySelector('#app'),readTools=new Set(['get_house_design_status','get_house_design_updates','get_house_design_viewer','open_house_design_app']);let rid=0;
async function load(){const r=await fetch('/api/sessions',{cache:'no-store'});if(!r.ok)return;const d=await r.json(),wanted=new URLSearchParams(location.search).get('session_id')||'';for(const s of d.sessions){const o=document.createElement('option');o.value=s.session_id;o.textContent=(s.title||s.session_id)+' · '+s.state;sel.append(o)}sel.value=wanted;if(wanted&&!d.sessions.some(s=>s.session_id===wanted))sel.value='';frame.src='/ui'+(sel.value?'?session_id='+encodeURIComponent(sel.value):'')}
sel.addEventListener('change',()=>{const u=new URL(location.href);if(sel.value)u.searchParams.set('session_id',sel.value);else u.searchParams.delete('session_id');history.replaceState(null,'',u);frame.src='/ui'+(sel.value?'?session_id='+encodeURIComponent(sel.value):'')});
window.addEventListener('message',async e=>{if(e.source!==frame.contentWindow||e.origin!==location.origin||!e.data||typeof e.data!=='object')return;const m=e.data;if(m.jsonrpc!=='2.0'||!Number.isSafeInteger(m.id)||typeof m.method!=='string')return;let result,error;try{if(m.method==='ui/initialize'){const id=new URL(frame.src).searchParams.get('session_id');let toolResult=null;if(id){const r=await fetch('/rpc',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({jsonrpc:'2.0',id:++rid,method:'tools/call',params:{name:'open_house_design_app',arguments:{session_id:id}}})});const x=await r.json();toolResult=x.result}result={protocolVersion:'2026-01-26',capabilities:{},serverInfo:{name:'house-design-app-browser-host',version:'0.2.0'},hostContext:{displayMode:'fullscreen',...(toolResult?{toolResult}: {})}}}else if(m.method==='tools/call'){const n=m.params?.name;if(!readTools.has(n))throw Error('Browser host permits read-only session tools only');const r=await fetch('/rpc',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(m)});const x=await r.json();if(x.error)throw Error(x.error.message);result=x.result}else if(m.method==='ui/notifications/initialized'||m.method==='ui/notifications/size-changed'){return}else throw Error('Unsupported browser host method')}catch(x){error={code:-32000,message:String(x.message||x)}}frame.contentWindow.postMessage({jsonrpc:'2.0',id:m.id,...(error?{error}:{result})},location.origin)});
load();setInterval(async()=>{const selected=sel.value;const r=await fetch('/api/sessions',{cache:'no-store'});if(r.ok){const d=await r.json(),known=new Set([...sel.options].map(o=>o.value));for(const s of d.sessions)if(!known.has(s.session_id)){const o=document.createElement('option');o.value=s.session_id;o.textContent=(s.title||s.session_id)+' · '+s.state;sel.append(o)}if(selected)sel.value=selected}},3000);
</script></html>'''


def _response(status: int, body: bytes, content_type: str = "application/json; charset=utf-8") -> bytes:
    label = {200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed", 413: "Payload Too Large", 500: "Internal Server Error"}.get(status, "Error")
    return (f"HTTP/1.1 {status} {label}\r\nContent-Type: {content_type}\r\nContent-Length: {len(body)}\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nConnection: close\r\n\r\n".encode("ascii") + body)


async def http_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
        line = await asyncio.wait_for(reader.readline(), 3)
        parts = line.decode("ascii", "replace").strip().split()
        if len(parts) != 3:
            return
        headers = {}
        while True:
            raw = await asyncio.wait_for(reader.readline(), 3)
            if raw in (b"\r\n", b"\n", b""): break
            key, sep, value = raw.decode("ascii", "replace").partition(":")
            if sep: headers[key.strip().lower()] = value.strip()
        from server import ROOT, call_tool
        from design_session import list_sessions
        authority = headers.get("host", "")
        parsed_host = urlsplit("//" + authority)
        host = parsed_host.hostname
        try: port = parsed_host.port
        except ValueError: port = -1
        origin = headers.get("origin")
        expected_origin = f"http://{headers.get('host', '')}"
        if (host not in {"127.0.0.1", "localhost", "::1"} or parsed_host.username or parsed_host.password
                or port == -1 or any(x in authority for x in "/\\?#@")
                or (origin and origin != expected_origin) or (parts[0] == "POST" and origin != expected_origin)):
            writer.write(_response(403, b'{"error":"Loopback same-origin Host/Origin required"}')); await writer.drain(); return
        method, target = parts[0], urlsplit(parts[1]); path = target.path
        if method == "GET" and path == "/":
            body, ctype = HOST_HTML.encode(), "text/html; charset=utf-8"
            response = _response(200, body, ctype).replace(b"Content-Type: application/json; charset=utf-8", b"Content-Type: text/html; charset=utf-8")
        elif method == "GET" and path == "/ui":
            body = (APP / "index.html").read_bytes(); response = _response(200, body, "text/html; charset=utf-8")
        elif method == "GET" and path == "/api/sessions":
            body = json.dumps({"sessions": list_sessions(ROOT, 100)}, ensure_ascii=False).encode(); response = _response(200, body)
        elif method == "POST" and path == "/rpc":
            if headers.get("content-type", "").split(";")[0].lower() != "application/json":
                response = _response(400, b'{"error":"JSON required"}')
            else:
                try: length = int(headers.get("content-length", "-1"))
                except ValueError: length = -1
                if not 0 <= length <= MAX_BODY: response = _response(413 if length > MAX_BODY else 400, b'{"error":"Invalid bounded body"}')
                else:
                    raw = await asyncio.wait_for(reader.readexactly(length), 5)
                    request = json.loads(raw)
                    if (not isinstance(request, dict) or request.get("jsonrpc") != "2.0"
                            or (type(request.get("id")) is not int and not isinstance(request.get("id"), str))
                            or len(str(request.get("id"))) > 64 or request.get("method") != "tools/call"):
                        raise ValueError("Invalid JSON-RPC request")
                    params = request.get("params", {})
                    if not isinstance(params, dict): raise ValueError("params must be an object")
                    name = params.get("name")
                    arguments = params.get("arguments", {})
                    if not isinstance(name, str) or not isinstance(arguments, dict): raise ValueError("tool name and arguments must be valid")
                    if name not in READ_TOOLS: raise PermissionError("Browser host permits read-only session tools only")
                    result = await call_tool(name, arguments)
                    result = _plain(result)
                    payload = {"jsonrpc": "2.0", "id": request["id"], "result": result}
                    response = _response(200, json.dumps(payload, ensure_ascii=False, default=str).encode())
        else:
            response = _response(405 if method not in {"GET", "POST"} else 404, b'{"error":"Not found"}')
        writer.write(response); await writer.drain()
    except PermissionError as exc:
        writer.write(_response(403, json.dumps({"error":{"code":-32000,"message":str(exc)}}).encode())); await writer.drain()
    except (ValueError, json.JSONDecodeError, asyncio.IncompleteReadError) as exc:
        writer.write(_response(400, json.dumps({"error":{"code":-32600,"message":str(exc)}}).encode())); await writer.drain()
    except Exception:
        writer.write(_response(500, b'{"error":"Request failed"}')); await writer.drain()
    finally:
        writer.close()
        try: await writer.wait_closed()
        except Exception: pass


async def serve(host: str = "127.0.0.1", port: int = 8765):
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("House Design App can bind only to loopback.")
    server = await asyncio.start_server(http_client, host, port, limit=MAX_BODY + 65536)
    async with server: await server.serve_forever()
