# Canon EOS R100 — CCAPI caveats

Places where this camera differs from the CCAPI reference, or from what its own
manifest implies. Every entry below was reproduced against a real body, with the
response quoted.

Companion to the generated `canon-eos-r100-fw*.json` / `.md`, which record what
the camera *advertises*. This file records what it actually *does*.

Verified on firmware **1.3.0** unless noted.

---

## Being in the manifest does not mean it works

The `/ccapi` manifest advertises 79 endpoints. Presence there means the firmware
knows the route — not that a given call on a given body in a given state will
succeed. Several below are listed in the manifest and still fail.

Treat a manifest entry as "worth trying", and handle the failure.

---

## `POST /shooting/liveview` requires BOTH parameters

Sending `liveviewsize` on its own is rejected whatever its value - including
values the camera does accept when paired.

```
POST /ccapi/ver100/shooting/liveview  {"liveviewsize": "off"}     → 400 Invalid parameter
POST /ccapi/ver100/shooting/liveview  {"liveviewsize": "small"}   → 400 Invalid parameter
POST /ccapi/ver100/shooting/liveview  {"cameradisplay": "off"}    → 400 Invalid parameter
```

What this body accepts:

| liveviewsize | cameradisplay | result |
|---|---|---|
| `off` | `on` | 200 - stops live view |
| `off` | `off` | 400 |
| `off` | `keep` | 400 |
| `small` | `off` | 200 - starts, rear screen stays dark |
| `medium` | `off` | 200 |
| `large` | `off` | 400 - size not supported |

**Stopping requires `cameradisplay: "on"`.** The camera refuses a state where
neither a client stream nor its own screen is showing the scene, so the rear
screen comes back on when live view stops. The `displayoff` timer blanks it
again shortly after.

`keep` is documented for `cameradisplay` but rejected here. `large` is likewise
not a valid size on this body.

**Impact.** This was a silent, total failure: the integration sent
`{"liveviewsize": "off"}` to stop live view before every shutter and on entity
removal, and *every one of those calls was rejected*. Live view was never
stopped by anything. The 400 was easy to misread as "it was not running" -
it is not, it means the request was malformed.

**Handling.** Always send both keys. Verify a stop actually worked by checking
`/shooting/liveview/flip`, which answers 503 `Live view not started` once the
stream is really down.

---

## `GET /event/polling` ignores a `timeout` parameter

There is no `timeout` request parameter. The reference's long-poll form is
`?continue=on`, which streams with 100-Continue. Passing `timeout=8` does not
hold the request open — the camera answers immediately from the current queue.

```
GET /ccapi/ver100/event/polling?timeout=8
→ HTTP 200 {}          (returns in ~8 ms, not 8 s)
```

**Impact.** A single post-shutter poll almost always returns empty, because the
image is not written yet. Caller then concludes "no image" and falls back to a
storage-tree walk. That cost a fixed 3 s per photo and made the walk — which can
return the *previous* image — the normal path rather than the fallback.

**Handling.** Follow the documented order: poll once to drain, fire the shutter,
then poll repeatedly. The image shows up after roughly 1 s on this body.

---

## 32 settings answer 503 while the camera is idle

Most of `/shooting/settings/*` is unreadable when the camera is sitting outside
shooting mode.

```
GET /ccapi/ver100/shooting/settings/av
→ HTTP 503
```

**Impact.** A capability dump taken with an idle camera under-reports. Do not
conclude a feature is missing from a 503 — the endpoint exists and the value is
simply not available in that state.

**Handling.** Put the camera in shooting mode before regenerating the dump if
you need those values.

---

## Reported `ability` lists differ from the reference examples

The reference's examples are illustrative, not a contract. Read `ability` from
the camera and treat any hardcoded list as a fallback only.

| Setting | Reference example | This camera |
|---|---|---|
| `stillimagequality` (jpeg) | `none, large_fine, large_normal, medium_fine, medium_normal, small` | `none, large_fine, large_normal, medium_fine, medium_normal, small1_fine, small1_normal, small2` |
| `displayoff` | `10, 20, 30, 60, 120` | `15, 30, 60, 180, 300, 600, 1800` |
| `autopoweroff` | `30, 60, 180, 300, 600, disable` | same plus `immediately` |
| `beep` | `enable, disable, disabletouch` | `enable, disabletouch, disable` |

**Impact.** A UI built from the documented lists offers values this camera
rejects, and hides values it accepts. `small2` and `1800` appear nowhere in the
reference.

---

## `stillimagequality` PUT requires both sub-values

One endpoint carries `jpeg` and `raw` together, and the PUT expects both.

```
PUT /ccapi/ver100/shooting/settings/stillimagequality
    {"value": {"raw": "none", "jpeg": "large_fine"}}
```

**Impact.** Sending only the field you are changing resets the other one.

**Handling.** Read the current object, change one key, send both back.

---

## Shooting settings do not survive a power-cycle — identity settings do

Observed once, on 2026-08-26. Before the camera lost power `stillimagequality`
was `{"raw":"none","jpeg":"small2"}` (3613 recordable shots). After it came back
it read:

```
GET /ccapi/ver100/shooting/settings/stillimagequality
    {"value":{"raw":"none","jpeg":"large_fine"}}   # 707 recordable shots
```

Nothing had written to the endpoint in between. In the same power-cycle these
values were **kept** verbatim:

```
GET /ccapi/ver100/functions/registeredname/copyright   {"copyright":"Spielerei"}
GET /ccapi/ver100/functions/registeredname/author      {"author":"Andrej Friesen"}
GET /ccapi/ver100/functions/registeredname/nickname    {"nickname":"Spielerei"}
GET /ccapi/ver100/functions/beep                       {"value":"disable"}
GET /ccapi/ver100/functions/autopoweroff               {"value":"disable"}
```

So the split is not "CCAPI writes are volatile". Camera-identity and function
settings persist; the shooting setting reverted to what the camera's own menu
holds for the active mode.

**Impact.** After any battery pull, power switch toggle or crash, images are
silently ~5× larger. Anything downstream that budgets on file size — transfer
time over Wi-Fi, an upload to an API, a per-request timeout — is quietly wrong
until someone notices.

**Handling.** Do not treat a shooting setting as configured once. Re-assert it
after every reconnect rather than reading it and trusting it. Only
`stillimagequality` has been checked; assume the rest of
`/shooting/settings/*` behaves the same until proven otherwise.

This integration does that for `stillimagequality`: a value chosen through Home
Assistant is stored on the config entry and re-asserted on the next connect
transition, logging a warning naming both the reverted and the restored value.
Deliberately only on reconnect - a change made on the camera body while it is up
is left alone rather than being fought every 16 seconds - and deliberately only
for values set through HA, so the camera's own menu is never second-guessed.

---

## The camera answers CCAPI with its screen dark

`displayoff` defaults to `15` on this body, and the `ability` list has no
"never" — `1800` (30 min) is the maximum:

```
GET /ccapi/ver100/functions/displayoff
    {"value":"15","ability":["15","30","60","180","300","600","1800"]}
```

**Impact.** Not an API bug, but it costs debugging time: 15 s after the last
interaction the body looks switched off, while `/ccapi` still answers `200` and
every endpoint works. It is easy to chase a phantom outage. It also reads as
broken to anyone standing in front of an installation.

**Handling.** Trust the network, not the panel — `GET /ccapi` is the liveness
check. Raise `displayoff` to `1800` where a dark screen would be
misread.

---

## `DELETE` on `/contents` is advertised but only works on directories

The manifest reports `"delete": true` for `/contents`. It is not true at that
node, nor at the card below it:

```
DELETE /ccapi/ver130/contents              {"message":"Method not allowed"}  405
DELETE /ccapi/ver130/contents/sd           {"message":"Method not allowed"}  405
DELETE /ccapi/ver130/contents/sd/100CANON  {}                                200
```

The tree is `/contents` -> storages -> directories -> files, and only the
directory level accepts `DELETE`. It removes every file in one call: 50 files
in **0.35 s**, the directory node itself surviving as an empty folder.

**Impact.** "Delete everything" reads like one request against the root and is
not. Trusting the manifest gets a 405 at the worst moment.

**Handling.** Walk `/contents` -> storage -> directory and `DELETE` each
directory. That is one request per DCIM folder (normally one), not per file, so
there is no reason to loop over files.

---

## `cardformat` ignores `formattype`

```
POST /ccapi/ver100/functions/cardformat
    {"name":"sd"}                            {}  200   <- formats
    {"name":"sd","formattype":"bogus"}       {}  200   <- also formats
    {}                                       {"message":"Invalid parameter"}  400
    {"card":"sd"}                            {"message":"Invalid parameter"}  400
    {"name":"card1"}                         {"message":"Invalid parameter"}  400
    {"name":"cf"}                            {"message":"Card not available"}  409
```

`name` is validated - note the camera distinguishes an unknown slot name (400)
from a known-but-absent one (409). `formattype` is not validated at all: an
arbitrary string returns 200 and the card is formatted regardless.

**Impact.** Code that passes `formattype: "fullformat"` will appear to request
a low-level format and silently get whatever the camera does by default. There
is no way to tell from the response which happened.

**Handling.** Send only `{"name": ...}`. Do not expose a format-type choice you
cannot honour. Take `name` from `/devicestatus/storage` rather than hardcoding
`"sd"`.

---

## Writing `registeredname/ownername` crashes the camera

**Do not PUT this endpoint.** The camera accepts the write, answers 200, echoes
the value back - and dies a few seconds later. It does not recover on its own;
it needs a physical power-cycle.

```
GET /ccapi/ver100/functions/registeredname/ownername   {"ownername":""}   200  0.02s
PUT /ccapi/ver100/functions/registeredname/ownername
    {"ownername":"Spielerei"}                          {"ownername":"Spielerei"}  200  0.40s
  +3s   GET /ccapi   200        still alive
  +6s   GET /ccapi   200        still alive
  +9s   GET /ccapi   timeout    gone, and stayed gone
```

Reproduced three times on fw 1.3.0: twice through Home Assistant, once with raw
curl and the integration uninvolved, so it is the firmware and not a client.
The failure is always the same shape - healthy sub-second polls right up to the
write, then a timeout, then connection-refused.

The sibling fields are fine. `copyright`, `author` and `nickname` were each
written in the same sessions with no ill effect, and they persist across a
power-cycle. `ownername` is the only one of the four that always reads back
empty, because the camera never survives long enough to store it.

**Impact.** One PUT takes the camera off the network until someone walks over
to it. In an unattended installation that is the whole system down. It is also
a nasty one to diagnose, because the 200 response points the investigation
anywhere but the actual call.

**Not the documented AVF exclusion.** The reference notes on all four
registeredname endpoints: "In case of a Canon camera that support AVF, this API
is not supported." AVF is Activation-free - cameras where CCAPI needs no
activation procedure. It does not apply here, and the reference itself supplies
the test: section 4.2.1 says `GET /ccapi` is *not* supported on AVF cameras,
which use `GET /ccapi/ver100/topurlfordev` instead. This body serves
`GET /ccapi` fine - the integration polls it every 16 s. It is not an AVF
camera. The exclusion would also have to take all four endpoints down, and the
other three work and persist. An unsupported API would answer 400/404 anyway,
not 200 with the value echoed back.

**Handling.** This integration removes the field outright: no TextEntity in
`text.py`, and no entry in `CAMERA_SETTINGS`, so it is not even polled. `GET` is
harmless, but a field nothing may write is not worth a round trip. Set the owner
name from the camera's own menu if it is needed.

---

## The camera drops off Wi-Fi on its own after a few hours

Separate from the `ownername` crash above, and not caused by anything a client
does. The body simply stops answering and needs a physical power-cycle.

Observed 2026-08-27: last activity 01:42, healthy 16 s polls throughout, then

```
04:01:03  binary_sensor.canon_eos_r100_connection -> off
          (first a timeout, then "Cannot connect to host ...:8080")
```

**The network was fine.** Across the same moment, on the same Wi-Fi:

```
sensor.od1f8ed5_last_seen   checked in every ~30 s, 04:10 through 04:24, unbroken
binary_sensor.buzzer_knopf  available
media_player.smart_audio... available
```

So it is not a router reboot, a DHCP event or an outage - every other device
stayed associated. Ruled out separately: `autopoweroff` is `disable`, the body
runs on an AC adapter (`kind: ac_adapter`), and `temperature` reads `normal`.
The camera was being actively polled every 16 s, so it was not idle-timing-out
from lack of traffic either. Earlier instances appear on 2026-08-18, 08-19,
08-24 and 08-25, i.e. before any of this integration's changes.

**Impact.** In an unattended installation the camera is simply gone after a few
hours, and only a human at the device brings it back. Polling does not keep it
alive and nothing in CCAPI recovers it.

**Handling.** Treat camera presence as something that WILL fail, not something
to assume: check the connection entity immediately before a shot and give the
caller a real error path rather than letting the request hang. Do not power the
camera on hours before it is needed. There is no software fix from this side -
if it matters, the mitigations are external (a smart plug to power-cycle it, or
cabled Ethernet/tethering instead of Wi-Fi).

---

## Adding an entry

Keep the shape: what you expected, the verbatim response, why it matters, and
how to handle it. Include the firmware version if it differs from the heading —
a caveat that is fixed in a later firmware is worth keeping, marked as such,
because people run old firmware.
