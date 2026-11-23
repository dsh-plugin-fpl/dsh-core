# Public isolated service fixture

`server.py --root <new-empty-directory> --port <unused-loopback-port> [--web-root ...] [--mock-models] [--mock-model-delay seconds] [--mock-external]` starts the actual owned Core service. It writes a private `fixture-launch.txt` and a synthetic bridge descriptor under that root. It never adopts production state.

The explicit fixture also provides `POST /_fixture/launch` to the authenticated initial account with its Core CSRF header, returning a one-use launch descriptor for exercising application CLI setup. `GET /_fixture/external` exposes only synthetic Feishu document metadata and write counters; POST configures timeout-after-append for unknown-result recovery tests. These routes exist only in this fixture process and are not production service APIs.

The workspace starts this fixture through its documented executable; it must not import Core private implementation or modify a Core database. Models and Feishu responses are deterministic; real HTTP, WebSocket, task persistence and resource transfer remain active.

`--mock-model-delay` sets the interval between deterministic chat chunks (default 0.05 seconds). Workspace streaming acceptance uses 1 second so the first HTTP event must arrive while the persisted Core task is still running, then verifies a disconnected consumer cancels that task. It never delays or replaces real production providers.
