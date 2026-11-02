# House Design App

This portable package includes the original house builder, renderer, bundle/export helpers, assistant, game-asset viewer, catalogs and viewer assets in the parent checkout. The MCP app and browser WebUI import those local `scripts/` and `docs/enshrouded-houses/` paths; neither needs the original development checkout.

## Native app

Run `server.py` over MCP stdio to use the chat app. Authoring remains available through the typed `begin_house_design_session`, `update_house_design`, `set_house_design_phase` and `finish_house_design_session` tools. Updates use the existing SQLite session store at `work/house-app-integration/design-sessions/` and immutable snapshots/events. The app opens the original bundled 3D viewer, keeps its document, renderer, scene, camera and controls alive, and sends the exact ordered revision to it. Browser access is read-only; authoring stays on native MCP.

## Browser WebUI

Run `python tools/house-design-app/server.py --serve` from the checkout to host on loopback (default `127.0.0.1:8765`). The browser page wraps the same `index.html` app in an iframe and speaks its JSON-RPC UI bridge. Choose a session from the selector; without an explicit selection it stays empty and never binds to a global latest session. The selector lists durable sessions from the same SQLite database as MCP. Browser RPC only allows status, ordered updates, exact viewer bootstrap and explicit session reopen. POST requests require same-origin `Origin`; the host rejects non-loopback hosts and does not expose arbitrary file paths.

## Checks

```powershell
python -m unittest discover -s tools/house-design-app -p "test_*.py" -v
```

The tests cover native stdio and session behavior as well as the browser bridge, shared SQLite state, viewer bootstrap, read-only allowlist, loopback origin and route isolation.
