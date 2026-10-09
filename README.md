# flowctl

Drive **Google Flow** from the command line, scripts and other AI agents, through your own
logged-in Chrome using [OpenCLI](https://github.com/jackwener/opencli).

Flow has no public API, so flowctl automates the web UI: it can create Flow *Characters*, attach
them to prompts so the same face and outfit come back every time, generate images and 8 second
videos, and chain clips into longer videos (24, 35, 60, 80 seconds) with ffmpeg.

> Unofficial. It automates a consumer web product with your own account. That may be against the
> product's terms and can get an account flagged, so use it at your own risk, at a human pace, one
> job at a time. flowctl **stops with exit code 3** when Flow shows "unusual activity", a policy
> rejection or an error. It never retries around those and it does not try to hide automation.

## Requirements

- macOS or Linux, Python 3.9+ (standard library only), `ffmpeg`
- [`opencli`](https://github.com/jackwener/opencli) with its browser bridge connected to a Chrome
  profile that is signed in to Google Flow (English UI; some selectors match English labels)
- A Flow project URL (create an empty project in Flow and copy the URL)

## Setup

```bash
git clone https://github.com/md-fahad-ali/flowctl && cd flowctl
opencli profile list                       # note the profile id of the signed-in Chrome
./flowctl init --profile <id> --project https://flow.google.com/project/<your-project-id>
./flowctl doctor
```

Config is read from `config.json` next to the script, or `$FLOWCTL_HOME`, or `~/.flowctl`.
`FLOWCTL_PROFILE`, `FLOWCTL_PROJECT`, `FLOWCTL_SESSION` and `FLOWCTL_CLIP_SECONDS` override it.

## Commands

Every command prints one JSON object; logs go to stderr.

```
flowctl character create --name N --describe "..."     # or --from-asset "<image name in the project>"
flowctl character list | assets | status | doctor
flowctl gen   "prompt" [--character N] [--out DIR]       # video, downloads 720p
flowctl image "prompt" [--character N] [--out DIR]       # image, downloads 2K
flowctl extend "what happens next"                       # attach latest clip + "Extend this video:"
flowctl long    spec.json [--resume]                     # chain clips + stitch + trim to target
flowctl episode spec.json                                # create the character if missing, then long
flowctl batch   jobs.json                                # [{kind, args}, ...] one after another
flowctl download [--kind video|image]
flowctl serve [--port 8787]                              # HTTP API on 127.0.0.1 only
```

HTTP API (`serve`): `POST /jobs` with `{"kind": "gen|image|character|extend|long|episode|batch|status|download", "args": {...}}`,
then `GET /jobs/<id>`. It binds to localhost and has **no authentication**; do not expose it.

`examples/street_sketch.json` is a three beat, 24 second spec (setup, escalation, payoff).

## Why a Character, and the one gotcha

Describing a person in text gives a different face every time. Flow's Character feature stores a
portrait and a full body image. In Flow's chat, typing `@` opens the asset picker; you must pick
the character **and press "Add to prompt"**. Selecting it is not enough. flowctl does this and
refuses to generate if the attachment chip is missing.

## What has been tested

| Feature | Status |
|---|---|
| `doctor`, `character list` | Verified against a real Flow project. |
| `gen --character` | Verified end to end: attach character, approve, queue wait, download. The clip showed the same character. |
| `download` | Verified. |
| `image`, `character create`, `assets`, `extend`, `long`, `episode`, `batch`, `status`, `serve` | Written, **not yet run end to end**. Treat as untested and please report issues. |

## Known limits

- One browser session and one job at a time. A clip can sit in Flow's queue for 5 to 20 minutes.
- Flow can only "Extend" Veo clips in its UI. Other clips are chained as new generations with the
  character attached, so a visible jump between parts is possible.
- Selectors follow the current Flow UI and will break when it changes.
- The native file chooser is blocked in the automated browser, so uploads use assets already in the
  project.

## License

MIT, see `LICENSE`.
