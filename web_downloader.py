#!/usr/bin/env python3
from flask import Flask, render_template, request, send_file, jsonify
from flask_socketio import SocketIO, emit
import yt_dlp
import os
import tempfile
import zipfile
from threading import Thread
import uuid

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")
progress_data = {}

@app.route('/')
def index():
    return render_template('index.html')

def progress_hook(d, task_id, total_count):
    if d['status'] == 'downloading':
        speed = d.get('_speed_str', 'N/A')
        playlist_index = d.get('playlist_index', 1)
        
        progress_text = f"{playlist_index}/{total_count}"
        
        socketio.emit('progress', {
            'task_id': task_id,
            'status': 'downloading',
            'progress': progress_text,
            'speed': speed
        })
    elif d['status'] == 'finished':
        filename = os.path.basename(d.get('filename', ''))
        progress_data[task_id]['completed_count'] += 1
        completed = progress_data[task_id]['completed_count']
        
        socketio.emit('progress', {
            'task_id': task_id,
            'status': 'finished',
            'filename': filename,
            'completed': f"{completed}/{total_count}"
        })

@app.route('/start_download', methods=['POST'])
def start_download():
    data = request.json
    task_id = str(uuid.uuid4())
    
    def download_task():
        url = data['url']
        quality = data['quality']
        format_choice = data['format']
        
        temp_dir = tempfile.mkdtemp()
        url_type = 'playlist' if ('playlist' in url or 'list=' in url) else 'single'
        
        # プレイリスト情報を事前取得
        total_count = 1
        if url_type == 'playlist':
            try:
                with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if 'entries' in info:
                        total_count = len(info['entries'])
            except:
                pass
        
        progress_data[task_id] = {'total_count': total_count, 'completed_count': 0}
        
        quality_map = {"1": "best", "2": "best[height<=720]", "3": "best[height<=480]"}
        format_map = {
            "1": quality_map.get(quality, "best"),
            "2": "bestaudio/best"
        }
        
        template = '%(title)s.%(ext)s' if url_type == 'single' else '%(playlist_index)03d - %(title)s.%(ext)s'
        
        def progress_hook_with_count(d):
            progress_hook(d, task_id, total_count)
        
        ydl_opts = {
            'outtmpl': f'{temp_dir}/{template}',
            'format': format_map.get(format_choice, "best"),
            'progress_hooks': [progress_hook_with_count]
        }
        
        if format_choice == "2":
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192'
            }]
    
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            
            files = []
            for f in os.listdir(temp_dir):
                full_path = os.path.join(temp_dir, f)
                if os.path.isfile(full_path) and os.path.getsize(full_path) > 0:
                    files.append(f)
            
            progress_data[task_id] = {
                'status': 'completed',
                'files': files,
                'temp_dir': temp_dir
            }
            
            socketio.emit('progress', {
                'task_id': task_id,
                'status': 'completed',
                'file_count': len(files)
            })
            
        except Exception as e:
            socketio.emit('progress', {
                'task_id': task_id,
                'status': 'error',
                'error': str(e)
            })
    
    Thread(target=download_task).start()
    return jsonify({'task_id': task_id})

@app.route('/get_file/<task_id>')
def get_file(task_id):
    if task_id not in progress_data:
        return jsonify({'error': 'タスクが見つかりません'}), 404
    
    data = progress_data[task_id]
    files = data['files']
    temp_dir = data['temp_dir']
    
    if len(files) == 1:
        file_path = os.path.join(temp_dir, files[0])
        return send_file(file_path, as_attachment=True, download_name=files[0])
    else:
        zip_path = os.path.join(temp_dir, "download.zip")
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for filename in files:
                file_path = os.path.join(temp_dir, filename)
                if os.path.exists(file_path):
                    zipf.write(file_path, filename)
        return send_file(zip_path, as_attachment=True, download_name='download.zip')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)