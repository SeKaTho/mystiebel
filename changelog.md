## [Unreleased]

### Fixed
- **Entities silently freezing after a few days** ([#XX](link-to-issue)): The
  WebSocket client only relied on aiohttp's transport-level heartbeat
  (ping/pong), which cannot detect the case where the MyStiebel cloud stops
  pushing `valuesChanged` events or answering `getValues` polls while the
  underlying connection still appears healthy. Combined with the coordinator
  unconditionally re-publishing its last cached snapshot every update cycle,
  this caused entities to keep reporting stale values indefinitely — with no
  errors logged and no `unavailable` state — until the integration was
  manually reloaded.

### Added
- **Data-level WebSocket watchdog** (`websocket_client.py`): `_listen_to_messages`
  now uses `ws.receive(timeout=WEBSOCKET_DATA_TIMEOUT)` instead of a blind
  `async for` loop. If no message of any kind arrives within
  `WEBSOCKET_DATA_TIMEOUT` (default 300s), the connection is force-closed and
  reconnected automatically.
- **Coordinator staleness tracking** (`coordinator.py`): a new
  `_last_confirmed_fresh` timestamp is updated whenever real data is received
  (push update or polled response). If no fresh data has been confirmed for
  longer than `MAX_DATA_STALENESS` (default 600s), `_async_update_data` now
  raises `UpdateFailed` instead of silently re-publishing cached data, so
  entities correctly reflect connectivity loss.
- New constants `WEBSOCKET_DATA_TIMEOUT` (300s) and `MAX_DATA_STALENESS` (600s)
  in `const.py`, layered so the WebSocket-level reconnect gets a chance to
  self-heal before the coordinator escalates to `UpdateFailed`.
