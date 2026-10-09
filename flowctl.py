#!/usr/bin/env python3
"""flowctl: drive Google Flow through your own logged-in Chrome via OpenCLI.

Flow has no public API, so this is a thin harness over `opencli browser`.
It is meant for your own account and your own work: one job at a time,
no retry loops, and it STOPS (exit code 3) on Flow's "unusual activity",
policy rejections or generic failures instead of trying to get around them.

Commands (all print one JSON object to stdout, logs go to stderr):
  flowctl doctor
  flowctl characters
  flowctl gen "prompt" [--character "MyCharacter"] [--out DIR]
  flowctl extend "what happens next" [--out DIR]       # attach latest video + 'Extend this video:'
  flowctl long spec.json [--resume]                    # 35 / 60 / 80 s: chain segments, stitch
  flowctl image "prompt" [--character NAME]            # image, downloads 2K
  flowctl character create --name N --describe "..."   # or --from-asset "<image name>"; adds portrait + full body
  flowctl character list | assets | status | download [--kind video|image]
  flowctl episode spec.json                            # create the character if missing, then build the long video
  flowctl batch jobs.json                              # list of {kind, args}, one after another
  flowctl serve [--port 8787]                          # local HTTP API for other agents

Env: FLOWCTL_PROFILE (opencli Chrome profile, default from config.json),
     FLOWCTL_PROJECT (Flow project URL), FLOWCTL_SESSION (opencli session name).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

def _home():
    """Data dir: $FLOWCTL_HOME, else the repo folder if it holds a config.json, else ~/.flowctl."""
    if os.environ.get("FLOWCTL_HOME"):
        return Path(os.environ["FLOWCTL_HOME"]).expanduser()
    here = Path(__file__).resolve().parent
    return here if (here / "config.json").exists() else Path.home() / ".flowctl"


HOME = _home()
HOME.mkdir(parents=True, exist_ok=True)
CONFIG = json.loads((HOME / "config.json").read_text()) if (HOME / "config.json").exists() else {}
PROFILE = os.environ.get("FLOWCTL_PROFILE") or CONFIG.get("profile", "")
PROJECT = os.environ.get("FLOWCTL_PROJECT") or CONFIG.get("project", "")
SESSION = os.environ.get("FLOWCTL_SESSION") or "flowctl"
DOWNLOADS = Path.home() / "Downloads"
CLIP_SECONDS = int(os.environ.get("FLOWCTL_CLIP_SECONDS", "8"))  # length of one Flow clip

STOP_PHRASES = {
    "unusual activity": "flow flagged the browser session (unusual activity)",
    "prominent people": "flow policy filter rejected the prompt (prominent people)",
    "violate our policies": "flow policy filter rejected the prompt",
    "Something went wrong": "flow reported a generic error",
    "failed to generate": "flow reported the generation failed",
}


class FlowError(Exception):
    pass


def log(*a):
    print("[flowctl]", *a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- opencli ---
def oc(*args, timeout=90):
    env = dict(os.environ)
    if PROFILE:
        env["OPENCLI_PROFILE"] = PROFILE
    p = subprocess.run(["opencli", "browser", SESSION, *args], capture_output=True,
                       text=True, env=env, timeout=timeout)
    return (p.stdout or "") + (p.stderr or "")


def ev(js, timeout=60):
    out = oc("eval", js, timeout=timeout)
    lines = [l for l in out.splitlines() if not l.startswith(("  Update available", "  Run: npm"))]
    return "\n".join(lines).strip()


def find(css=None, text=None):
    args = ["find"] + (["--css", css] if css else ["--text", text])
    out = oc(*args)
    try:
        return json.loads(out[out.index("{"): out.rindex("}") + 1]).get("entries", [])
    except Exception:
        return []


def click_ref(ref):
    return oc("click", str(ref))


def click_css(css, text=None):
    for e in find(css=css):
        if text is None or (e.get("text") or "").strip() == text:
            click_ref(e["ref"])
            return True
    return False


def click_text(text, exact=True):
    for e in find(text=text):
        t = (e.get("text") or "").strip()
        if (t == text) if exact else (text in t):
            click_ref(e["ref"])
            return True
    return False


def js_click_text(text, scope="document"):
    js = ("(()=>{const els=[...%s.querySelectorAll('button,[role=button],[role=option],[role=menuitem],"
          "mat-option,div,span')].filter(e=>(e.innerText||'').trim()===%s&&e.children.length<4);"
          "if(!els.length)return 'none';els[els.length-1].click();return 'ok'})()") % (scope, json.dumps(text))
    return ev(js) == "ok"


# ------------------------------------------------------------- flow basics ---
def check_config():
    if not PROJECT:
        raise FlowError("no project URL: run `flowctl init --project <flow project url>`, or set FLOWCTL_PROJECT")


def body_tail(n=600):
    return ev("document.body.innerText.replace(/\\n/g,' ').slice(-%d)" % n)


def check_stop(text=None):
    text = text if text is not None else body_tail(900)
    for phrase, why in STOP_PHRASES.items():
        if phrase in text:
            raise FlowError(why)


def open_project():
    check_config()
    oc("open", PROJECT)
    for _ in range(20):
        time.sleep(1.5)
        if "Start new session" in ev("document.body.innerHTML.includes('Start new session')?'Start new session':''"):
            break
    ev("[...document.querySelectorAll('button')].filter(b=>b.innerText.trim()==='Get started').forEach(b=>b.click());'ok'")


def new_session():
    open_project()
    click_css("button[aria-label='Start new session']")
    time.sleep(1.5)


def tile_srcs():
    raw = ev("JSON.stringify([...document.querySelectorAll('img.thumbnail,img.image')].map(i=>i.currentSrc||i.src))")
    try:
        return json.loads(raw)
    except Exception:
        return []


def focus_input():
    r = ev("(()=>{const t=document.querySelector('[contenteditable=true]');if(!t)return 'none';t.focus();return 'ok'})()")
    if r != "ok":
        raise FlowError("chat input not found (changelog modal or page not loaded?)")


def insert(text):
    ev("(()=>{const t=document.querySelector('[contenteditable=true]');t.focus();"
       "document.execCommand('insertText',false,%s);return 1})()" % json.dumps(text))


def mention(name):
    """'@' opens the asset picker. Pick the saved Flow character, then 'Add to prompt' (without this it is NOT attached)."""
    focus_input()
    insert("@")
    time.sleep(1.5)
    ok = ev("(()=>{const els=[...document.querySelectorAll('[role=option],mat-option')].filter(e=>(e.innerText||'').trim().split('\\n')[0]===%s&&/Character/.test(e.innerText));"
            "if(!els.length)return 'none';els[0].click();return 'ok'})()" % json.dumps(name))
    if ok != "ok":
        raise FlowError("character '%s' not found in the @ list (create it in Flow > Characters first)" % name)
    time.sleep(1.5)
    if not js_click_text("Add to prompt"):
        raise FlowError("could not press 'Add to prompt' for character '%s'" % name)
    time.sleep(1.5)
    # drop the stray '@' text; the attachment chip is separate from the text
    ev("(()=>{const t=document.querySelector('[contenteditable=true]');t.focus();document.execCommand('selectAll');document.execCommand('delete');return 1})()")
    chip = ev("(()=>{const t=document.querySelector('[contenteditable=true]');let c=t;for(let i=0;i<6&&c;i++){c=c.parentElement;"
              "if(c&&c.querySelector('img'))return 'chip'}return 'none'})()")
    if chip != "chip":
        raise FlowError("character '%s' was not attached (no chip in the chat input)" % name)


def attach_latest(kind="Videos"):
    """Open the chat '+' asset picker, pick the newest item, 'Add to prompt'."""
    ev("(()=>{const b=[...document.querySelectorAll('button')].filter(x=>(x.innerText||'').trim()==='add').pop();"
       "if(!b)return 'none';b.click();return 'ok'})()")
    time.sleep(2)
    # picker: first row is the most recent asset
    ev("(()=>{const r=document.querySelectorAll('[role=dialog] [role=option],[role=dialog] button,.cdk-overlay-pane div');"
       "return r.length})()")
    if kind:
        js_click_text(kind)
        time.sleep(1)
    got = ev("(()=>{const rows=[...document.querySelectorAll('.cdk-overlay-pane *')].filter(e=>/\\bVideo\\b/.test(e.innerText||'')&&e.children.length<5&&e.getBoundingClientRect().height>40);"
             "if(!rows.length)return 'none';rows[0].click();return 'ok'})()")
    time.sleep(1)
    if not js_click_text("Add to prompt"):
        raise FlowError("could not attach the latest video (picker changed?)")
    time.sleep(1)


def submit_and_approve(allow_approve=True):
    oc("keys", "Enter")
    deadline = time.time() + 40
    approved = False
    while time.time() < deadline:
        time.sleep(3)
        check_stop()
        if allow_approve and not approved:
            for e in find(text="Approve"):
                if (e.get("text") or "").strip() == "checkApprove":
                    click_ref(e["ref"])
                    approved = True
                    log("approved one generation")
                    break
        tail = body_tail(500)
        if approved or "queue" in tail or re.search(r"\d+%", tail) or "scheduled" in tail:
            return


def wait_new_asset(before, kind, timeout=1500):
    """Poll the project grid (reload each minute) until an unseen tile appears."""
    start = time.time()
    last_reload = 0
    while time.time() - start < timeout:
        time.sleep(15)
        check_stop()
        if time.time() - last_reload > 60:
            oc("open", PROJECT)
            time.sleep(6)
            last_reload = time.time()
        new = [s for s in tile_srcs() if s not in before]
        busy = bool(re.search(r"\d+%", body_tail(3000)))
        if new and not busy:
            return new[0]
        log("waiting... %ds, new=%d busy=%s" % (time.time() - start, len(new), busy))
    raise FlowError("timed out waiting for %s" % kind)


def open_tile(src=None, kind=None):
    """Open a tile in the project grid: by its image src, or the on-screen top-left tile of the given kind.
    The grid is masonry, so DOM order is not visual order: sort by position."""
    js = ("(()=>{const imgs=[...document.querySelectorAll('img.thumbnail,img.image')];let t=null;"
          "const src=%s,kind=%s;"
          "const isVid=i=>{let c=i;for(let k=0;k<4&&c;k++){c=c.parentElement;if(c&&/play_circle/.test(c.innerText||''))return true}return false};"
          "if(src){t=imgs.find(i=>(i.currentSrc||i.src)===src)}"
          "else{const c=imgs.filter(i=>kind==='video'?isVid(i):!isVid(i)).filter(i=>{const r=i.getBoundingClientRect();return r.width>60&&r.bottom>0});"
          "c.sort((a,b)=>{const x=a.getBoundingClientRect(),y=b.getBoundingClientRect();return (x.top-y.top)||(x.left-y.left)});t=c[0]}"
          "if(!t)return 'none';t.click();return 'ok'})()") % (json.dumps(src), json.dumps(kind))
    if ev(js) != "ok":
        raise FlowError("could not open the %s tile in the grid" % (kind or "requested"))


def download_first_tile(out_dir, name, option, src=None, kind=None):
    """Open the new tile (by src, else newest of kind), use the UI download menu, move the file to out_dir."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    marker = time.time()
    open_tile(src, kind)
    time.sleep(5)
    ev("(()=>{const i=[...document.querySelectorAll('mat-icon')].find(x=>x.innerText.trim()==='download');"
       "if(!i)return 'none';(i.closest('button')||i).click();return 'ok'})()")
    time.sleep(2)
    if not js_click_text(option):
        raise FlowError("download option '%s' not found (wrong tile opened?)" % option)
    for _ in range(40):
        time.sleep(2)
        cand = [p for p in DOWNLOADS.glob("*") if p.is_file() and p.stat().st_mtime > marker
                and not p.name.startswith(".") and p.suffix.lower() in (".mp4", ".jpg", ".png", ".webp")]
        if cand:
            src_f = max(cand, key=lambda p: p.stat().st_mtime)
            time.sleep(1.5)
            dst = out_dir / (name + src_f.suffix.lower())
            shutil.move(str(src_f), dst)
            return str(dst)
    raise FlowError("download did not arrive in ~/Downloads")


# ------------------------------------------------------------------- jobs ---
def last_frame(video, out_png):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-sseof", "-0.1", "-i", video,
                    "-frames:v", "1", out_png], check=True)
    return out_png


def job_gen(prompt, character=None, out="out", name=None, kind="video"):
    """kind: 'video' (downloads 720p) or 'image' (downloads 2K)."""
    new_session()
    before = tile_srcs()
    focus_input()
    if character:
        mention(character)
    insert(("Generate an image: " if kind == "image" else "") + prompt)
    submit_and_approve()
    new_src = wait_new_asset(before, kind)
    option = "2K" if kind == "image" else "720p"
    path = download_first_tile(out, name or ("%s_%d" % (kind, int(time.time()))), option, src=new_src, kind=kind)
    return {"file": path, "kind": kind}


def job_extend(prompt, out="out", name=None):
    new_session()
    before = tile_srcs()
    attach_latest("Videos")
    focus_input()
    insert("Extend this video: " + prompt)
    submit_and_approve()
    tail = body_tail(700)
    if "not able to extend" in tail:
        raise FlowError("flow agent refused to extend in chat (known to be flaky); chain by new generation instead")
    new_src = wait_new_asset(before, "video")
    path = download_first_tile(out, name or ("ext_%d" % int(time.time())), "720p", src=new_src, kind="video")
    return {"file": path}


def plan_segments(target):
    n = max(1, -(-int(target) // CLIP_SECONDS))
    return n


def stitch(files, target, out_file):
    lst = Path(out_file).with_suffix(".txt")
    lst.write_text("".join("file '%s'\n" % Path(f).resolve() for f in files))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-t", str(target), "-c:v", "libx264", "-crf", "18", "-c:a", "aac", str(out_file)], check=True)
    return str(out_file)


def job_long(spec, resume=False):
    """spec: {name, target_seconds, character, style, beats:[...], mode: 'extend'|'chain'}"""
    out = Path(spec.get("out", "out")) / spec["name"]
    out.mkdir(parents=True, exist_ok=True)
    state_f = out / "state.json"
    state = json.loads(state_f.read_text()) if (resume and state_f.exists()) else {"segments": []}
    target = int(spec.get("target_seconds", 35))
    n = plan_segments(target)
    beats = spec["beats"]
    if len(beats) < n:
        raise FlowError("need %d beats for %ds (8 s per clip), spec has %d" % (n, target, len(beats)))
    mode = spec.get("mode", "extend")
    for i in range(len(state["segments"]), n):
        log("segment %d/%d" % (i + 1, n))
        if i == 0 or mode == "chain":
            prompt = spec["style"].strip() + "\n\nACTION: " + beats[i].strip()
            res = job_gen(prompt, spec.get("character"), str(out), "seg%02d" % (i + 1))
        else:
            res = job_extend(beats[i].strip(), str(out), "seg%02d" % (i + 1))
        state["segments"].append(res["file"])
        state_f.write_text(json.dumps(state, indent=1))
    final = stitch(state["segments"], target, str(out / ("%s_%ds.mp4" % (spec["name"], target))))
    return {"file": final, "segments": state["segments"]}


def list_mentions():
    new_session()
    focus_input()
    insert("@")
    time.sleep(2)
    raw = ev("JSON.stringify([...document.querySelectorAll('[role=option],mat-option')]"
             ".map(e=>(e.innerText||'').trim()).filter(Boolean))")
    ev("(()=>{const t=document.querySelector('[contenteditable=true]');t.focus();document.execCommand('selectAll');"
       "document.execCommand('delete');return 1})()")
    try:
        return [x.replace("\n", " | ") for x in json.loads(raw)]
    except Exception:
        return []


def wait_url(part, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        check_stop()
        if part in ev("location.href"):
            return True
        time.sleep(3)
    raise FlowError("timed out waiting for page '%s'" % part)


def set_native(selector, value):
    ev("(()=>{const el=document.querySelector(%s);if(!el)return 'none';el.focus();"
       "const proto=el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;"
       "Object.getOwnPropertyDescriptor(proto,'value').set.call(el,%s);"
       "el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));return 'ok'})()"
       % (json.dumps(selector), json.dumps(value)))


def character_create(name, describe=None, from_asset=None, info=None, body="", make_body=True):
    """Create a Flow Character: portrait from a description or an existing project asset, then name/info/body."""
    if not (describe or from_asset):
        raise FlowError("give describe=... or from_asset=<asset name in this project>")
    if describe and name.lower() in describe.lower() and len(describe) < 5:
        raise FlowError("description too short")
    open_project()
    if not click_css("button[aria-label='Add media menu']"):
        raise FlowError("Add media menu not found")
    time.sleep(1.5)
    if not click_text("Create character", exact=False):
        raise FlowError("'Create character' not found in the menu")
    time.sleep(3)
    if from_asset:
        if not click_text("Add from project"):
            raise FlowError("'Add from project' not found")
        time.sleep(2)
        ev("(()=>{const i=document.querySelector('input[placeholder*=Search]');if(!i)return 'none';i.focus();"
           "document.execCommand('insertText',false,%s);return 'ok'})()" % json.dumps(from_asset))
        time.sleep(2)
        ev("(()=>{const rows=[...document.querySelectorAll('.cdk-overlay-pane *')].filter(e=>/Image/.test(e.innerText||'')&&e.children.length<5&&e.getBoundingClientRect().height>40);"
           "if(!rows.length)return 'none';rows[0].click();return 'ok'})()")
        time.sleep(1)
        if not click_text("Add media"):
            raise FlowError("'Add media' not found in the picker (no asset matched '%s'?)" % from_asset)
    else:
        focus_input()
        insert(describe)
        oc("keys", "Enter")
    wait_url("/character/", 420)
    time.sleep(5)
    ev("(()=>{const i=[...document.querySelectorAll('mat-icon')].find(x=>x.innerText.trim()==='edit');if(!i)return 'none';(i.closest('button')||i).click();return 'ok'})()")
    time.sleep(1)
    ents = [e for e in find(css="input") if e.get("visible")]
    if ents:
        oc("type", str(ents[0]["ref"]), name)
        oc("keys", "Enter")
    time.sleep(2)
    if info:
        set_native("textarea", info)
        time.sleep(1)
    url = ev("location.href")
    if make_body:
        if not click_text("Create body", exact=False):
            raise FlowError("'Create body' not found")
        time.sleep(3)
        focus_input()
        insert("Full body, standing straight, head to toe, facing camera, plain light-grey studio background. "
               "Keep the face, hair and outfit exactly as in the portrait. " + (body or ""))
        oc("keys", "Enter")
        t0 = time.time()
        while time.time() - t0 < 420:
            check_stop()
            time.sleep(6)
            txt = ev("document.body.innerText.replace(/\\n/g,' ')")
            if re.search(r"\bBody\b", txt) and "Create body" not in txt and not re.search(r"\d+%", txt):
                break
    click_text("Done")
    time.sleep(3)
    return {"character": name, "url": url}


def list_characters():
    new_session()
    focus_input()
    insert("@")
    time.sleep(2)
    raw = ev("JSON.stringify([...document.querySelectorAll('[role=listbox] [role=option],mat-option,[role=option]')]"
             ".map(e=>(e.innerText||'').trim()).filter(Boolean))")
    ev("(()=>{const t=document.querySelector('[contenteditable=true]');t.focus();document.execCommand('selectAll');"
       "document.execCommand('delete');return 1})()")
    try:
        return [x.split("\n")[0] for x in json.loads(raw) if x.endswith("\nCharacter")]
    except Exception:
        return []


def init_config(profile, project):
    cfg = {"profile": profile or "", "project": project}
    (HOME / "config.json").write_text(json.dumps(cfg, indent=2) + "\n")
    return {"config": str(HOME / "config.json"), **cfg}


def doctor():
    res = {"profile": PROFILE or None, "project": PROJECT or None}
    res["opencli"] = bool(shutil.which("opencli"))
    res["ffmpeg"] = bool(shutil.which("ffmpeg"))
    try:
        open_project()
        res["flow_loaded"] = "flow.google.com" in ev("location.href")
        res["signed_in"] = "PRO" in ev("document.body.innerText") or "Start new session" in ev("document.body.innerHTML")
    except Exception as e:  # noqa: BLE001
        res["error"] = str(e)[:200]
    return res


def status():
    """Read-only: what is Flow showing right now (progress, queue, errors)."""
    tail = body_tail(900)
    res = {"url": ev("location.href"), "busy": bool(re.search(r"\d+%", tail)),
           "percent": re.findall(r"\d+%", tail)[:4], "queued": "queue" in tail}
    for phrase, why in STOP_PHRASES.items():
        if phrase in tail:
            res["problem"] = why
    return res


def job_download(kind="video", out="out", name=None):
    """Download the newest tile in the project (video 720p / image 2K) without generating anything."""
    open_project()
    option = "2K" if kind == "image" else "720p"
    return {"file": download_first_tile(out, name or ("%s_%d" % (kind, int(time.time()))), option, kind=kind), "kind": kind}


def job_episode(spec):
    """End to end: make the character if it is missing, then build the long video."""
    cs = spec.get("character_spec")
    if cs:
        existing = [c.lower() for c in list_characters()]
        if cs["name"].lower() not in existing:
            log("character '%s' missing, creating" % cs["name"])
            character_create(cs["name"], cs.get("describe"), cs.get("from_asset"), cs.get("info"),
                             cs.get("body", ""), cs.get("make_body", True))
        spec = dict(spec, character=cs["name"])
    return job_long(spec)


def job_batch(jobs, out="out"):
    """Run a list of {kind, args} jobs one after another; stop at the first problem."""
    results = []
    for i, j in enumerate(jobs):
        k, a = j["kind"], j.get("args", {})
        a.setdefault("out", out)
        log("batch %d/%d: %s" % (i + 1, len(jobs), k))
        if k == "gen":
            r = job_gen(a["prompt"], a.get("character"), a["out"], a.get("name"))
        elif k == "image":
            r = job_gen(a["prompt"], a.get("character"), a["out"], a.get("name"), kind="image")
        elif k == "character":
            r = character_create(a["name"], a.get("describe"), a.get("from_asset"), a.get("info"),
                                 a.get("body", ""), a.get("make_body", True))
        elif k == "extend":
            r = job_extend(a["prompt"], a["out"], a.get("name"))
        elif k == "long":
            r = job_long(a)
        elif k == "episode":
            r = job_episode(a)
        else:
            raise FlowError("unknown batch kind '%s'" % k)
        results.append({"kind": k, **r})
    return {"results": results}


# -------------------------------------------------------------------- http ---
JOBS, LOCK, QUEUE = {}, threading.Lock(), []


def worker():
    while True:
        with LOCK:
            jid = next((j for j in QUEUE if JOBS[j]["status"] == "queued"), None)
        if not jid:
            time.sleep(1)
            continue
        job = JOBS[jid]
        job["status"] = "running"
        try:
            kind, a = job["kind"], job["args"]
            if kind == "gen":
                job["result"] = job_gen(a["prompt"], a.get("character"), a.get("out", str(HOME / "out")))
            elif kind == "image":
                job["result"] = job_gen(a["prompt"], a.get("character"), a.get("out", str(HOME / "out")), kind="image")
            elif kind == "character":
                job["result"] = character_create(a["name"], a.get("describe"), a.get("from_asset"),
                                                  a.get("info"), a.get("body", ""), a.get("make_body", True))
            elif kind == "episode":
                a.setdefault("out", str(HOME / "out"))
                job["result"] = job_episode(a)
            elif kind == "batch":
                job["result"] = job_batch(a["jobs"], a.get("out", str(HOME / "out")))
            elif kind == "status":
                job["result"] = status()
            elif kind == "download":
                job["result"] = job_download(a.get("kind", "video"), a.get("out", str(HOME / "out")))
            elif kind == "extend":
                job["result"] = job_extend(a["prompt"], a.get("out", str(HOME / "out")))
            elif kind == "long":
                a.setdefault("out", str(HOME / "out"))
                job["result"] = job_long(a)
            else:
                raise FlowError("unknown job kind")
            job["status"] = "done"
        except Exception as e:  # noqa: BLE001
            job["status"], job["error"] = "stopped", str(e)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):  # noqa: N802
        if self.path == "/jobs":
            return self._send(200, JOBS)
        m = re.match(r"/jobs/([\w-]+)$", self.path)
        if m and m.group(1) in JOBS:
            return self._send(200, JOBS[m.group(1)])
        self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if self.path != "/jobs":
            return self._send(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
            assert body.get("kind") in ("gen", "image", "character", "extend", "long", "episode", "batch", "status", "download")
        except Exception:
            return self._send(400, {"error": "body must be {kind: gen|image|character|extend|long|episode|batch|status|download, args: {...}}"})
        jid = uuid.uuid4().hex[:8]
        JOBS[jid] = {"id": jid, "kind": body["kind"], "args": body.get("args", {}), "status": "queued"}
        QUEUE.append(jid)
        self._send(202, JOBS[jid])

    def log_message(self, *a):
        pass


def serve(port):
    threading.Thread(target=worker, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)  # localhost only, no auth by design
    log("listening on http://127.0.0.1:%d  (POST /jobs, GET /jobs/<id>)" % port)
    srv.serve_forever()


def main():
    ap = argparse.ArgumentParser(prog="flowctl")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ini = sub.add_parser("init", help="write config.json (opencli profile id + Flow project URL)")
    ini.add_argument("--project", required=True)
    ini.add_argument("--profile", default="")
    sub.add_parser("doctor")
    sub.add_parser("characters")
    g = sub.add_parser("gen")
    g.add_argument("prompt")
    g.add_argument("--character")
    g.add_argument("--out", default=str(HOME / "out"))
    e = sub.add_parser("extend")
    e.add_argument("prompt")
    e.add_argument("--out", default=str(HOME / "out"))
    lg = sub.add_parser("long")
    lg.add_argument("spec")
    lg.add_argument("--resume", action="store_true")
    im = sub.add_parser("image")
    im.add_argument("prompt")
    im.add_argument("--character")
    im.add_argument("--out", default=str(HOME / "out"))
    ch = sub.add_parser("character")
    ch.add_argument("action", choices=["create", "list"])
    ch.add_argument("--name")
    ch.add_argument("--describe")
    ch.add_argument("--from-asset")
    ch.add_argument("--info")
    ch.add_argument("--body", default="")
    ch.add_argument("--no-body", action="store_true")
    sub.add_parser("assets")
    sub.add_parser("status")
    dl = sub.add_parser("download")
    dl.add_argument("--kind", choices=["video", "image"], default="video")
    dl.add_argument("--out", default=str(HOME / "out"))
    ep = sub.add_parser("episode")
    ep.add_argument("spec")
    bt = sub.add_parser("batch")
    bt.add_argument("jobs")
    bt.add_argument("--out", default=str(HOME / "out"))
    sv = sub.add_parser("serve")
    sv.add_argument("--port", type=int, default=8787)
    a = ap.parse_args()
    try:
        if a.cmd == "init":
            r = init_config(a.profile, a.project)
        elif a.cmd == "doctor":
            r = doctor()
        elif a.cmd == "characters":
            r = {"characters": list_characters()}
        elif a.cmd == "gen":
            r = job_gen(a.prompt, a.character, a.out)
        elif a.cmd == "image":
            r = job_gen(a.prompt, a.character, a.out, kind="image")
        elif a.cmd == "status":
            r = status()
        elif a.cmd == "download":
            r = job_download(a.kind, a.out)
        elif a.cmd == "episode":
            r = job_episode(json.loads(Path(a.spec).read_text()))
        elif a.cmd == "batch":
            r = job_batch(json.loads(Path(a.jobs).read_text()), a.out)
        elif a.cmd == "assets":
            r = {"items": list_mentions()}
        elif a.cmd == "character":
            if a.action == "list":
                r = {"characters": list_characters()}
            else:
                if not a.name:
                    raise FlowError("--name is required")
                r = character_create(a.name, a.describe, a.from_asset, a.info, a.body, not a.no_body)
        elif a.cmd == "extend":
            r = job_extend(a.prompt, a.out)
        elif a.cmd == "long":
            r = job_long(json.loads(Path(a.spec).read_text()), a.resume)
        else:
            serve(a.port)
            return
        print(json.dumps({"ok": True, **r}))
    except FlowError as err:
        print(json.dumps({"ok": False, "stopped": str(err)}))
        sys.exit(3)


if __name__ == "__main__":
    main()
