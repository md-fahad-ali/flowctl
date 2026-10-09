---
name: flowctl
description: Use when an agent or person needs to drive Google Flow from the terminal with the flowctl CLI (via OpenCLI and a signed-in Chrome): create or list Flow characters, generate images and videos with a consistent character, upload an image, and build long videos (16 to 80 seconds) by chaining 8 second clips, where the last frame of each clip becomes the first frame of the next. Also covers Gemini Omni facts for swapping characters, extending clips and keeping costs down.
---

# flowctl: drive Google Flow from the command line

flowctl automates the Google Flow web UI through OpenCLI and the user's own signed-in Chrome. Flow has no
public API. It is unofficial, one job at a time, and meant for the user's own account.

## Hard rules

- Run one flowctl command at a time. All commands share one browser session, so never run two at once.
- If a command exits with code 3 and prints `{"ok": false, "stopped": "..."}`, **stop**. Read the reason, tell the
  user, and do not loop. Never rephrase a prompt to get around Flow's policy filter, and never retry past
  "unusual activity". A retry costs credits.
- Each 8 second clip costs credits and can wait 5 to 20 minutes in Flow's queue. Plan the clip count first.
- Report only what you actually ran. The tested list below says what is verified.

## Setup (once)

```bash
opencli profile list                       # id of the Chrome profile signed in to Flow
flowctl init --profile <id> --project https://flow.google.com/project/<id>
flowctl doctor                             # expects flow_loaded true, signed_in true
```

## Tested status

| Verified through flowctl | Written, not yet run end to end |
|---|---|
| `doctor`, `character list`, `gen --character`, `download`, `lastframe`, `upload`, `fromframe` | `image`, `character create`, `extend`, `long`, `episode`, `batch`, `serve` |

If you use something from the second column, say it is untested and check the result yourself.

## Consistent character

Text descriptions drift. Use a Flow **Character** (portrait plus full body). `flowctl gen "..." --character "Name"`
attaches it. Inside Flow the attach needs the character picked in the asset picker **and** "Add to prompt"
pressed; flowctl does both and refuses to generate if no attachment thumbnail appears.

Create one: `flowctl character create --name N --describe "..."` (or `--from-asset "<image name>"`). Untested.

## Long videos: chain by last frame (the method that is verified)

One Flow clip is 8 seconds. Generating each clip fresh makes the scene, angle and light change. Instead, hand the
**last frame of clip N to clip N+1 as its first frame**. In testing the next clip's first frame differed from the
handed-in frame by about 1/255, and scene, camera and character stayed the same.

For a video of about 24 seconds (3 clips):

```bash
flowctl gen "<scene + action 1>" --character "Name" --out out/run      # clip 1
flowctl lastframe out/run/<clip1>.mp4 --out out/run/last1.png
flowctl fromframe out/run/last1.png "<what happens next>" --character "Name" --out out/run   # clip 2
flowctl lastframe out/run/<clip2>.mp4 --out out/run/last2.png
flowctl fromframe out/run/last2.png "<what happens next>" --character "Name" --out out/run   # clip 3
```

Then join with the stitch step (it drops the duplicate first frame and matches loudness):
`flowctl long spec.json` runs the whole chain from a spec in `mode: "frames"` (the default), but **`long` has not
been run end to end yet**, so prefer the manual steps above and say so if you use `long`.

Spec shape (`examples/street_sketch.json`): `name`, `target_seconds`, `mode` (`frames` | `chain` | `extend`),
`character`, `style` (shared scene, camera, light, repeated for every clip), `beats` (one action per clip).

### Writing good beats

- Repeat the same location, camera and light words in every clip. Say "one continuous locked-off shot, no cuts".
- Tell the next clip to continue from the exact moment, not to start a new scene.
- Make a tiny story: setup, escalation, payoff. Random movement is not content.
- End the previous clip on a stable pose with motion still in progress, not on a blur or a fade.

### Known limits of chaining

- Sound does not carry over (a still frame has no audio). Check levels at the joins by ear.
- Colour can drift a little per hop and compounds over many hops. Only 2 hops have been tested. Keep hops short.
- Resolution can change between clips; scale to one size before joining.
- Omni "Extend" is not available in Flow yet (only Veo 8 s clips extend, and Veo Lite does the extend). That is why
  chaining by frame is used.

## Flow UI facts that broke earlier attempts

- Typing `@` in the prompt opens the asset picker. Pick the character, then press "Add to prompt".
- Clicking an image row in the "+" ingredients picker attaches it. "Add to prompt" is only for characters.
- OpenCLI's own `upload` command cannot upload to Flow: Flow's upload input is never attached to the page and
  the file-chooser event is blocked. flowctl captures the input and hands the file to it inside the page.
- The asset grid is masonry. DOM order is not visual order, so open tiles by image src, and tell videos from
  images by the play badge.
- A changelog modal ("Get started") can silently swallow typing on a new project. flowctl dismisses it.
- Flow's layout changes (side chat panel vs bottom prompt bar). Selectors may need a patch after a UI update.

## Swapping a character or dancer in an existing video (Gemini Omni, from docs, not yet run by flowctl)

- Flow Video-to-Video edit: upload (max 60 s, trim to 30 s if longer), select up to a **10 second segment**, write a
  short prompt such as "Replace the dancer with <character>. Keep everything else the same.", add the character as
  an ingredient. Up to 3 follow-up turns keep context. A 25 s video is 3 segments (0-10, 10-20, 20-25), then stitched.
- Gemini API (paid tier only): edit inputs must be 10 s or less. Bind roles with tags such as
  `[# Sources <VIDEO_0>@Video1] [# References <IMAGE_REF_0>@Image1]`.
- Omni video extension in the API: `previous_interaction_id` plus "Extend this video: ...", 3 to 10 s per step, up to
  40 s total.

## Keeping cost and tokens down

- Draft at 360p (about a third of the API cost, half the credits in Flow), then re-render only the take you keep.
- 720p output is about $0.10 per second on the paid API. Omni video has no free tier.
- Edit one segment instead of regenerating the whole video, and end edit prompts with "Keep everything else the same."
- Do not retry failures in a loop; every retry is billed.

See `docs/OMNI_NOTES.md` in the repo for the sources behind these numbers.
