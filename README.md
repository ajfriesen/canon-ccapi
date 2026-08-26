# Home Assistant Integration for Canon CCAPI 

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ajfriesen&repository=canon-ccapi)

## Entities

| Entity | Platform | Notes |
|---|---|---|
| Connection | `binary_sensor` | `on` means `/ccapi` answered within 5 s. It does **not** mean the camera can shoot — live view being busy, a full card or an AF failure all still report `on`. |
| Battery / Temperature | `sensor` | Polled every 10 s. Battery reads `unknown` on mains power - `kind` is then `ac_adapter` and the camera reports no level. |
| Storage Free / Used / Used Percent / Files On Card | `sensor` | All from one `/devicestatus/storage` poll. Storage Free carries the card name, path and `readwrite` access as attributes. |
| Storage Total | `sensor` | Card capacity. |
| Lens | `sensor` | Attached lens name from `/devicestatus/lens`, `none` when nothing is mounted. Refreshed on the slow cadence - glass is not swapped every ten seconds. |
| Copyright / Author / Owner Name / Nickname | `text` | `/functions/registeredname/*`, embedded into the EXIF of every shot. Note the field is **author**, not `artist` as the CCAPI reference calls it. |
| Recordable Shots | `sensor` | From `/shooting/information/recordable`. Already accounts for the current image quality, so it answers "how many more shots" without arithmetic. |
| Live View | `switch` | Explicit control. The camera entity starts live view lazily whenever anything asks for a frame, so this makes that visible and stoppable. |
| Take Photo | `button` | Same path as the `canon_ccapi.take_photo` action. |
| Live View | `camera` | Starts live view on demand and stops it again. |
| Display off after | `select` | Seconds of inactivity before the rear screen sleeps. |
| Auto power off after | `select` | Seconds before the camera powers down, or `disable`. |
| Beep | `select` | `enable` / `disable` / `disabletouch`. |

### Camera settings (`select`)

These map to the writable CCAPI endpoints `/functions/displayoff`,
`/functions/autopoweroff` and `/functions/beep`. Each one is a `GET` returning
`{"value": ..., "ability": [...]}` and a `PUT` taking `{"value": ...}`, so the
dropdown options come from the camera's own reported `ability` list.

The values are persistent camera settings — set them once and they stay.

**Display off is a timer, not an on/off switch.** The shortest value the camera
offers (typically `10`) makes the rear screen sleep 10 seconds after the last
interaction; there is no CCAPI call that blanks the screen immediately.

> **Running an unattended booth?** Set *Display off after* short **and** leave
> *Auto power off after* on `disable`. Short display-off with auto-power-off
> enabled will put the whole camera to sleep between shots, and the next
> request then fails on the connection check instead of taking a photo. You
> want the screen asleep and the camera awake.

Because these settings only change when somebody changes them, they are read on
the first update and then roughly once a minute, rather than on every 10 s poll
— three extra serial requests per poll would slow down a cycle that shooting
depends on being quick. Changing one from Home Assistant applies immediately
and forces a re-read.

## Camera capabilities and caveats

`docs/capabilities/` holds, per camera model:

- a generated dump of what the body advertises - every endpoint in its `/ccapi`
  manifest and the allowed values for every readable setting. Regenerate with
  `just capabilities`.
- a hand-written caveats file: endpoints that are advertised and still fail,
  request parameters that are silently ignored, and `ability` lists that differ
  from the CCAPI reference.

The second file exists because the first cannot answer "will this call work".
Read the caveats before assuming an endpoint behaves as documented, and add to
them when a camera surprises you.

## Actions

### `canon_ccapi.take_photo`

Triggers the shutter and downloads the image. Returns `{"path": ..., "filename": ...}`.

Raises `HomeAssistantError` on any failure — it never returns an error result —
so callers that must survive a flaky camera should set `continue_on_error: true`
and check that `path` is non-empty.

## Development

The repo ships a Nix flake with a Python 3.13 dev shell. With direnv:

```bash
direnv allow
```

Without direnv:

```bash
nix develop
```

Entering the shell runs `uv sync`, which builds `.venv` from `pyproject.toml`
(`pytest-homeassistant-custom-component` pulls in a matching Home Assistant),
and activates it. Then:

```bash
pytest        # tests/
ruff check .  # lint
```

## Links

- https://my.home-assistant.io/redirect/hacs_repository/?owner=ajfriesen&repository=canon-ccapi
- Canon Camera Control API reference (developer registration required): https://developers.canon-europe.com/developers/s/camera
