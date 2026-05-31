from flask import Flask, request, jsonify, send_file
import subprocess
import requests
import base64
import os
import tempfile

app = Flask(__name__)

@app.route('/merge', methods=['POST'])
def merge():
    data = request.json
    video_url = data['video_url']
    audio_base64 = data['audio_base64']
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Video indir
        video_path = os.path.join(tmpdir, 'video.mp4')
        r = requests.get(video_url)
        with open(video_path, 'wb') as f:
            f.write(r.content)
        
        # Ses kaydet
        audio_path = os.path.join(tmpdir, 'audio.mp3')
        with open(audio_path, 'wb') as f:
            f.write(base64.b64decode(audio_base64))
        
        # FFmpeg ile birleştir
        output_path = os.path.join(tmpdir, 'output.mp4')
        subprocess.run([
            'ffmpeg', '-i', video_path, '-i', audio_path,
            '-map', '0:v', '-map', '1:a',
            '-c:v', 'copy', '-c:a', 'aac',
            '-shortest', output_path
        ], check=True)
        
        # Base64 olarak döndür
        with open(output_path, 'rb') as f:
            video_b64 = base64.b64encode(f.read()).decode()
        
        return jsonify({'video': video_b64})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)