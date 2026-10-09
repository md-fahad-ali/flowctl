# Gemini Omni 1.1 Flash: notes for long videos, swaps and cheap iteration

Collected from Google's own docs (Gemini API Omni docs and prompt guide, the Omni 1.1 Flash developer
blog, Google Flow help pages, the Gemini cookbook notebook) in October 2026. Features change quickly,
so re-check the linked pages before relying on a number.

## Long videos

- **API, scene extension:** call again with `previous_interaction_id` and a short prompt such as
  `Extend this video: ...`. Each step adds a 3 to 10 second continuation, up to 40 seconds in total.
  The model reads up to 10 seconds of earlier footage, and edits the last frames so the join is
  seamless. If you use timecodes in an extension prompt, `0s` is the start of the new part.
- **Flow app:** Omni "Extend videos" is listed as *coming soon*. Only Veo 3.1 clips can be extended
  (8 second clips, and the extend step must use Veo 3.1 Lite). This is why Extend is greyed out on
  Omni clips in Flow.
- **Omni clip lengths in Flow:** 4, 6, 8 or 10 seconds. A 25 second video is therefore 3 clips.
- **First and last frame** (`<FIRST_FRAME>` / `<LAST_FRAME>`, or Flow "Frames"): use the last frame of
  clip N as the first frame of clip N+1 to chain clips with a stable pose and background. Using the
  same image as both gives a seamless loop.
- **Single take:** Omni likes to add cuts by default. Say "in a single unbroken scene", "one
  continuous shot, no scene cuts", and "static / locked off" for the camera.
- **Timecodes:** `[0-3s] ... [3-6s] ... [6-10s] ...` give beat by beat control inside one clip.

## Swapping a character or a dancer in an existing video

- **Flow, Video to Video edit (Omni):** upload a video (up to 60 s, 1 GB; over 30 s must be trimmed
  to 30 s), open it, select **up to a 10 second segment** in the trimming window, write a short
  prompt and add the character as an ingredient. Up to 3 follow-up turns keep the context.
  A 25 second video is edited as three segments: 0 to 10, 10 to 20 and 20 to 25, then stitched.
- **API:** uploaded videos for editing must be **10 seconds or less**, so slice first
  (`ffmpeg -ss 0 -t 10`, `-ss 10 -t 10`, `-ss 20 -t 5`). Bind roles with tags:
  `[# Sources <VIDEO_0>@Video1] [# References <IMAGE_REF_0>@Image1]` and a prompt like
  `Replace the dancer with the character in <IMAGE_REF_0>. Keep everything else the same.`
- **Video references** are for likeness and motion: up to 3 clips of up to 3 seconds each, audio
  ignored, and mixing several videos in one prompt degrades quality. Google's own example swaps three
  dancers for a dog, an octopus and a bear that each perform the reference dance.
- Not supported: editing recognisable real people in some cases, minors in the EEA/UK/CH, voice
  editing, YouTube URLs as a source, uploaded-video editing in the EEA/UK/CH.

## Using fewer tokens and credits

- **Draft at 360p** (`resolution: "360p"`): about a third of the cost and up to 60% faster on the API;
  in Flow it is half the credit cost of 720p. Upscale or re-render only the take you keep.
- **API price (paid tier only, no free tier):** input $1.50 per 1M tokens (text, image, video, audio);
  output $17.50 per 1M video tokens at 5,792 tokens per second of 720p video, about **$0.10 per second**.
  A 25 second 720p video is about $2.50, a 360p draft about a third of that.
- **Edit, do not regenerate:** a surgical edit of one 10 second segment is cheaper than redoing 25 s.
  Keep edit prompts short and end with `Keep everything else the same.`
- Prefer image references over video references; video input also costs input tokens.
- Do not loop on failures. Each retry is billed output.

## Zero shot, one shot, few shot

- **Zero shot:** text only. Fast, but the face and outfit drift between clips.
- **One shot (single reference):** one character image with `<IMAGE_REF_0>` or a Flow Character
  attached. This is the sweet spot for consistency per clip.
- **Few shot / multi reference:** several images for character, prop and style
  (`in the style of <IMAGE_REF_0>, the woman <IMAGE_REF_1> ...`), up to a handful per prompt.
- **One take for a whole story:** ask for the timecoded beats in one 10 second clip, then extend.

## Sources

- https://ai.google.dev/gemini-api/docs/omni
- https://deepmind.google/models/gemini-omni/prompt-guide/
- https://blog.google/innovation-and-ai/technology/developers-tools/build-with-gemini-omni-1-1-flash/
- https://support.google.com/labs/answer/16935718 (Flow: edit videos and build scenes)
- https://support.google.com/flow/answer/16352836 (Flow: models and supported features)
- https://ai.google.dev/gemini-api/docs/pricing
- https://github.com/google-gemini/cookbook/blob/main/quickstarts/Get_started_Omni.ipynb
