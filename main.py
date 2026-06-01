from flask import Flask, request, jsonify, send_file
import subprocess, requests, base64, os, tempfile

app = Flask(__name__)

@app.route('/merge', methods=['POST'])
def merge():
    data = request.get_json(force=True, silent=True) or {}
    video_url = data.get('video_url')
    audio_base64 = data.get('audio_base64')
    
    if not video_url or not audio_base64:
        return jsonify({'error': 'eksik alan', 'received_keys': list(data.keys())}), 400
        
    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = os.path.join(tmpdir, 'video.mp4')
        r = requests.get(video_url)
        with open(video_path, 'wb') as f:
            f.write(r.content)
            
        audio_path = os.path.join(tmpdir, 'audio.mp3')
        with open(audio_path, 'wb') as f:
            f.write(base64.b64decode(audio_base64))
            
        output_path = os.path.join(tmpdir, 'output.mp4')
        
        # FFmpeg komutu: Devasa 4K videonun RAM'i çökertmesini engellemek için -c:v copy kullanıldı.
        # Sonsuz döngü yerine -stream_loop 3 ile video süresi (24sn) ses süresini (41sn) geçecek şekilde çoklandı.
        subprocess.run([
            'ffmpeg', '-y',
            '-stream_loop', '3', '-i', video_path,
            '-i', audio_path,
            '-map', '0:v', '-map', '1:a',
            '-c:v', 'copy', '-c:a', 'aac',
            '-shortest', output_path
        ], check=True)
        
        with open(output_path, 'rb') as f:
            video_b64 = base64.b64encode(f.read()).decode()
            
        return jsonify({'video': video_b64})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
