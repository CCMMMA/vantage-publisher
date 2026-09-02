# Integration protocols and state transitions

[Documentation index](README.md)

## MQTT connection and completion

The publisher initializes Paho with callback API version 2, configures optional username/password authentication, starts an asynchronous connection, and runs the network loop in a background thread. The configured `uuid` is always the publication topic. Messages are submitted with `retain=False`; this program does not maintain a retained latest observation for new subscribers.

The queue record is committed before its payload is offered to Paho. A successful return code records an in-memory pending result; subsequent cycles inspect completion. Only completed records are deleted by the delivery path. Age and capacity pruning can remove records independently of that path. According to the [Paho client documentation](https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html), publish completion represents transmission at QoS 0 and acknowledgment processing at higher QoS levels. It does not establish that a subscriber has stored or acted upon the record.

An unsuccessful connection callback leaves the online flag false. Publish failures retain the SQLite record for later attempts. A reconnect does not itself synchronously drain the queue; main-loop advancement handles replay. No application-level receipt protocol or subscriber confirmation exists.

The code does not configure MQTT TLS, client certificates, a fixed client identifier, a Last Will, or persistent broker-session options. Setting the port to a conventional TLS port does not enable TLS. Deploy against an appropriately isolated broker or provide a separately managed protected transport boundary.

## Signal K endpoint derivation

Given `wss://host/signalk/v1/stream`, the access manager derives `https://host/signalk/v1` for HTTP operations; `ws` similarly becomes `http`. A path prefix preceding `/signalk/v1/stream` is preserved. Query strings are removed when constructing the HTTP base. Use a stream URL whose routing is compatible with this transformation.

The websocket publisher appends the token as a `token` query parameter and sends serialized deltas. The implementation concatenates the token directly, so it assumes a query-safe token and does not replace an existing token parameter. Prefer a stream URL without a pre-existing token query.

## Authentication lifecycle

The access manager uses several HTTP responses to infer whether security is supported. `GET access/requests` responses 200, 202, 401, and 403 are interpreted as security enabled. Responses 404, 405, and 501 are interpreted as disabled or unsupported. Other responses or network errors leave the previous state unchanged. This is an implementation heuristic, not an authoritative security discovery mechanism; a reverse proxy or a server with different routes can invalidate the inference.

When security is not classified as disabled, an existing token is checked through `POST auth/validate` with a Bearer authorization header. A 200 response is accepted, and a replacement token may be extracted. A 401 or 403 invalidates the current token; other failures generally leave validation uncertain. An uncertain result can still permit websocket attempts when a token remains available.

If necessary, the manager submits `POST access/requests` with a client ID of `vantage-<uuid>` and a station description. It follows a returned `href` to poll for approval and extracts tokens from `token`, `jwt`, `accessToken`, or `validate.token`. Fully qualified and root-relative request URLs are the intended forms. Relative path handling is basic and should be checked behind a reverse proxy.

Checks normally occur every sixty seconds; request submission is eligible every three hundred seconds. A request may be submitted again while approval is pending. Other enabled outputs continue between checks, but each HTTP request is synchronous and may extend the main cycle. The program has no separate worker devoted to access negotiation.

## Token persistence and publication failures

An acquired or refreshed token is written back into the configured JSON file. The helper reads the current object, changes `signalkToken`, and rewrites the file in place. A write failure is logged and the in-memory token can still be used. Persistence is not atomic, and simultaneous manual editing can conflict with the rewrite. Keep a protected backup and provide write access only when automatic persistence is desired.

A websocket send failure closes the connection; a later eligible packet triggers another attempt. Errors containing authorization-related text clear the in-memory token and schedule acquisition attempts. Signal K output has no persistent queue and no application acknowledgment tracking. A failed delta is not subsequently replayed by this publisher.

## WeatherLink access and caching

AirLink enrichment calls the current-conditions endpoint with a ten-second request timeout. Empty credentials, HTTP errors, or JSON parsing failures generally return an empty dictionary. A completed call updates the refresh timestamp even if its result is empty, so subsequent attempts wait for the configured interval. Unexpected exceptions caught in the main loop do not update that timestamp, which can cause another attempt on the next processed packet.

A successful refresh replaces the previous cache. A handled failure returning an empty dictionary also replaces it, so earlier AirLink fields may disappear until a later successful refresh. Conversely, an unexpected exception can leave the previous cache available. These differences are relevant when diagnosing intermittent enrichment.

## Embedded HTTP service

The service uses `ThreadingHTTPServer` and `SimpleHTTPRequestHandler`. GET and HEAD requests require Basic authentication only when `httpUser` is nonempty. The server exposes ordinary directory listings and file retrieval rather than a station-data REST API. It provides no built-in HTTPS configuration or record-level access control.

Keep `httpRoot` focused on files intended for download. If it is also the MQTT spool directory, the database and its sidecar files can be exposed. If it is the process working directory, configuration files and other project files can be exposed. Filesystem layout is therefore part of the access boundary, not merely a storage preference.

Implementation basis: [MQTT client, Signal K classes, HTTP handler](../vantage-publisher.py), and [AirLink client](../airlink.py).
