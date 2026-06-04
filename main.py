from flask import Flask, request, jsonify, send_file
import subprocess, requests, base64, os, tempfile, shutil

app = Flask(__name__)

CHUNK = 1024 * 1024  # 1 MB
DOWNLOAD_TIMEOUT = 180  # seconds


def stream_download(url, dest_path):
    """Download a URL to disk in chunks so the whole file never sits in RAM."""
    with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT) as r:
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=CHUNK):
                if chunk:
                    f.write(chunk)


def probe_duration(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True)
        return float(out.stdout.strip())
    except Exception:
        return 0.0


def probe_dims(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", path],
            capture_output=True, text=True)
        w, h = out.stdout.strip().split("x")
        return int(w), int(h)
    except Exception:
        return 1080, 1920


def _ass_time(t):
    if t < 0:
        t = 0
    cs = int(round(t * 100))
    h = cs // 360000; cs %= 360000
    m = cs // 6000;   cs %= 6000
    s = cs // 100;    cs %= 100
    return "%d:%02d:%02d.%02d" % (h, m, s, cs)


def build_ass(text, dur, w, h, words_per_cue=3):
    """Build an .ass subtitle string, spreading the text across the duration."""
    text = " ".join(str(text).split()).replace("{", "(").replace("}", ")")
    words = [x for x in text.split(" ") if x]
    cues = [" ".join(words[i:i + words_per_cue]) for i in range(0, len(words), words_per_cue)]
    if not cues:
        cues = [""]
    if dur <= 0:
        dur = 30.0
    total = sum(max(1, len(c)) for c in cues)
    fs = max(30, int(h * 0.046))
    outline = max(2, int(h * 0.0045))
    marginv = max(40, int(h * 0.13))
    head = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: %d\nPlayResY: %d\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, "
        "Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Def,DejaVu Sans,%d,&H00FFFFFF,&H00000000,&H80000000,1,0,1,%d,2,2,60,60,%d,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, Effect, Text\n"
        % (w, h, fs, outline, marginv)
    )
    lines, t = [], 0.0
    for c in cues:
        d = dur * (max(1, len(c)) / total)
        st, en = t, t + d
        t = en
        lines.append("Dialogue: 0,%s,%s,Def,,0,0,0,,%s" % (_ass_time(st), _ass_time(en), c))
    return head + "\n".join(lines) + "\n"


@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/merge", methods=["POST"])
def merge():
    data = request.get_json(force=True, silent=True) or {}
    video_url = data.get("video_url")
    audio_url = data.get("audio_url")
    audio_base64 = data.get("audio_base64")
    subtitle_text = data.get("subtitle_text")  # optional: burn captions
    resp_format = (data.get("format") or "base64").lower()

    if not video_url or (not audio_url and not audio_base64):
        return jsonify({
            "error": "eksik alan: 'video_url' ve ('audio_url' veya 'audio_base64') gerekli",
            "received_keys": list(data.keys()),
        }), 400

    tmpdir = tempfile.mkdtemp()
    video_path = os.path.join(tmpdir, "video.mp4")
    audio_path = os.path.join(tmpdir, "audio.input")
    subs_path = os.path.join(tmpdir, "subs.ass")
    output_path = os.path.join(tmpdir, "output.mp4")

    def cleanup():
        shutil.rmtree(tmpdir, ignore_errors=True)

    try:
        # 1) Inputs -> disk (streamed)
        try:
            stream_download(video_url, video_path)
        except Exception as e:
            cleanup(); return jsonify({"error": f"video indirilemedi: {e}"}), 502
        if audio_url:
            try:
                stream_download(audio_url, audio_path)
            except Exception as e:
                cleanup(); return jsonify({"error": f"ses indirilemedi: {e}"}), 502
        else:
            try:
                with open(audio_path, "wb") as f:
                    f.write(base64.b64decode(audio_base64))
            except Exception as e:
                cleanup(); return jsonify({"error": f"audio_base64 cozulemedi: {e}"}), 400

        # 2) Build the ffmpeg command
        if subtitle_text:
            # Burn captions -> must re-encode the video
            dur = probe_duration(audio_path)
            w, h = probe_dims(video_path)
            with open(subs_path, "w", encoding="utf-8") as f:
                f.write(build_ass(subtitle_text, dur, w, h))
            cmd = ["ffmpeg", "-y",
                   "-stream_loop", "-1", "-i", video_path,
                   "-i", audio_path,
                   "-filter_complex", "[0:v]ass=%s[v]" % subs_path,
                   "-map", "[v]", "-map", "1:a:0",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                   "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-b:a", "128k",
                   "-movflags", "+faststart", "-shortest", output_path]
        else:
            # No captions -> fast stream copy (no re-encode)
            cmd = ["ffmpeg", "-y",
                   "-stream_loop", "-1", "-i", video_path,
                   "-i", audio_path,
                   "-map", "0:v:0", "-map", "1:a:0",
                   "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
                   "-movflags", "+faststart", "-shortest", output_path]

        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 or not os.path.exists(output_path):
            tail = (proc.stderr or "")[-1500:]
            cleanup(); return jsonify({"error": "ffmpeg basarisiz", "ffmpeg_stderr": tail}), 500

        # 3) Return
        if resp_format == "base64":
            with open(output_path, "rb") as f:
                video_b64 = base64.b64encode(f.read()).decode()
            cleanup()
            return jsonify({"video": video_b64})

        resp = send_file(output_path, mimetype="video/mp4",
                         as_attachment=True, download_name="output.mp4")
        resp.call_on_close(cleanup)
        return resp

    except Exception as e:
        cleanup()
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
