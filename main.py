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
    # "file" = streamed binary (memory-safe). "base64" = legacy JSON (heavy).
    resp_format = (data.get("format") or "base64").lower()

    if not video_url or (not audio_url and not audio_base64):
        return jsonify({
            "error": "eksik alan: 'video_url' ve ('audio_url' veya 'audio_base64') gerekli",
            "received_keys": list(data.keys()),
        }), 400

    tmpdir = tempfile.mkdtemp()
    video_path = os.path.join(tmpdir, "video.mp4")
    audio_path = os.path.join(tmpdir, "audio.input")
    output_path = os.path.join(tmpdir, "output.mp4")

    def cleanup():
        shutil.rmtree(tmpdir, ignore_errors=True)

    try:
        # 1) Inputs -> disk (streamed; no full file held in RAM)
        try:
            stream_download(video_url, video_path)
        except Exception as e:
            cleanup()
            return jsonify({"error": f"video indirilemedi: {e}"}), 502

        if audio_url:
            try:
                stream_download(audio_url, audio_path)
            except Exception as e:
                cleanup()
                return jsonify({"error": f"ses indirilemedi: {e}"}), 502
        else:
            try:
                with open(audio_path, "wb") as f:
                    f.write(base64.b64decode(audio_base64))
            except Exception as e:
                cleanup()
                return jsonify({"error": f"audio_base64 cozulemedi: {e}"}), 400

        # 2) Merge. -stream_loop -1 loops the (short) video forever; -shortest
        #    stops at the end of the audio, so the video always covers the audio
        #    regardless of durations. -c:v copy => no re-encode => low CPU/RAM.
        proc = subprocess.run(
            ["ffmpeg", "-y",
             "-stream_loop", "-1", "-i", video_path,
             "-i", audio_path,
             "-map", "0:v:0", "-map", "1:a:0",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
             "-movflags", "+faststart",
             "-shortest", output_path],
            capture_output=True, text=True,
        )
        if proc.returncode != 0 or not os.path.exists(output_path):
            tail = (proc.stderr or "")[-1500:]
            cleanup()
            return jsonify({"error": "ffmpeg basarisiz", "ffmpeg_stderr": tail}), 500

        # 3) Return
        if resp_format == "base64":
            # Legacy path: holds whole file + base64 in RAM (memory-heavy).
            with open(output_path, "rb") as f:
                video_b64 = base64.b64encode(f.read()).decode()
            cleanup()
            return jsonify({"video": video_b64})

        # Default-recommended: stream the file back as binary (no base64, no whole-file-in-RAM).
        resp = send_file(output_path, mimetype="video/mp4",
                         as_attachment=True, download_name="output.mp4")
        resp.call_on_close(cleanup)
        return resp

    except Exception as e:
        cleanup()
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
