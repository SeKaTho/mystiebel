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
- **WebSocket reconnect loop silently dying on a connection error**
  ([#XX](link-to-issue)): observed in the wild as a `503 Invalid response
  status` from the MyStiebel cloud during `ws_connect()`, followed by zero
  further reconnect attempts or log output for 8+ hours. The background task
  running `_run()` appears to exit (most likely via an unexpected
  `CancelledError` outside of `stop()`) without restarting itself, leaving
  `self.ws` permanently `None` with no supervisor to notice.
- **API error responses silently defeating the staleness watchdog**
  ([#XX](link-to-issue)): when the MyStiebel API answers a periodic
  `getValues` poll with `{"fields": [], "errorCode": -10000}` (subscription
  stuck/rejected server-side, connection itself still "alive"), the response
  shape also satisfied `_is_initial_data()`'s check and was processed as a
  confirmed-fresh (if empty) data update. This reset the coordinator's
  `_last_confirmed_fresh` timestamp every poll cycle, which meant our own
  staleness `UpdateFailed` safety net never triggered even though no real
  data had arrived for hours — the freeze-masking bug the watchdog was built
  to catch, reintroduced by the watchdog's own success path.
- **Root cause: periodic poll responses misrouted as "initial data",
  triggering a redundant re-Subscribe every 60 seconds**
  ([#XX](link-to-issue)): `_is_initial_data()` only checked the response
  *shape* (`"fields"` present in `result`), which is identical for the
  one-time post-login data fetch and for every answer to the coordinator's
  60s `getValues` poll. As a result, every single poll cycle was treated as
  a fresh connection and re-sent a `Subscribe` message to an already-active
  channel. Repeatedly re-subscribing like this every minute appears to be
  what eventually causes the MyStiebel API to start rejecting requests with
  a persistent `errorCode` — the likely root cause behind the periodic
  freezes reported over the past weeks, pre-dating this fix branch.

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
- **Background task supervisor** (`coordinator.py` + `websocket_client.py`):
  a new `WebSocketClient.is_dead` property lets the coordinator detect, on
  every 60s poll, whether the WebSocket background task has ended
  unexpectedly (e.g. a stray `CancelledError`) instead of reconnecting on its
  own. If so, the coordinator now calls `restart()` on it automatically,
  bounding recovery time to roughly one poll interval instead of requiring a
  manual integration reload.
- **API error-response detection** (`websocket_client.py`): a new
  `_is_error_response`/`_handle_error_response` path is now checked before
  `_is_initial_data`, so a response carrying a non-zero `errorCode` is no
  longer misread as valid (if empty) data. It now logs a warning with the
  error code and forces a reconnect instead.
- **Poll-response routing fix** (`websocket_client.py`): a new
  `_is_poll_response`/`_handle_poll_response` path distinguishes the
  coordinator's periodic `getValues` poll (long-format message ID) from the
  one-time post-login initial data fetch (short-format message ID). Poll
  responses now update data without re-sending a `Subscribe` message,
  removing the redundant every-60-seconds re-subscription that appears to be
  the actual root cause of the periodic freezes.
- New constants `WEBSOCKET_DATA_TIMEOUT` (300s) and `MAX_DATA_STALENESS`
  (600s) in `const.py`, layered so the WebSocket-level reconnect and the new
  task supervisor both get a chance to self-heal before the coordinator
  escalates to `UpdateFailed`.
