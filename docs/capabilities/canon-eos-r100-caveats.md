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

## `POST /shooting/liveview` returns 400 when live view is not running

Stopping live view that is already stopped is rejected, not treated as a no-op.

```
POST /ccapi/ver100/shooting/liveview   {"liveviewsize": "off"}
→ HTTP 400
```

**Impact.** Code that stops live view defensively before the shutter must not
treat the response as meaningful, and must not pay a settle delay afterwards
when it failed. We were sleeping 0.5 s after this call on every single photo for
a teardown that never happened.

**Handling.** Only wait if the call returned 200.

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
