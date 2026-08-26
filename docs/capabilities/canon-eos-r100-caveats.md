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

## Adding an entry

Keep the shape: what you expected, the verbatim response, why it matters, and
how to handle it. Include the firmware version if it differs from the heading —
a caveat that is fixed in a later firmware is worth keeping, marked as such,
because people run old firmware.
