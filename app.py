from flask import Flask, render_template, request, send_file, jsonify
import requests
import os
from dotenv import load_dotenv
import logging
from flask_caching import Cache
from tasks import async_synthesize_and_save
from celery import states

app = Flask(__name__)

# Configure caching with Redis
cache = Cache(app, config={
    'CACHE_TYPE': 'redis',
    'CACHE_REDIS_URL': 'redis://localhost:6379/0'
})

# Setup logging
logging.basicConfig(level=logging.INFO, filename='app.log', format='%(asctime)s %(levelname)s: %(message)s')

# Load API key from .env
load_dotenv()
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

@cache.cached(timeout=300, key_prefix='voices')
def get_voices():
    url = "https://api.elevenlabs.io/v1/voices"
    headers = {"xi-api-key": ELEVENLABS_API_KEY}
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        voices = response.json().get('voices', [])
        return voices
    except Exception as e:
        logging.exception("Failed to fetch voices: %s", e)
        return []

@app.route('/', methods=['GET', 'POST'])
def home():
    voices = get_voices()
    if request.method == 'POST':
        user_text = request.form.get('lyrics', '').strip()
        selected_voice = request.form.get('voice', "21m00Tcm4TlvDq8ikWAM")
        
        if not user_text:
            return render_template('index.html', message="Please enter some text.", voices=voices)

        # Queue the task with Celery
        task = async_synthesize_and_save.delay(user_text, selected_voice)
        return render_template('index.html', message=f"Task queued! Check back soon (Task ID: {task.id})", task_id=task.id, voices=voices)

    return render_template('index.html', voices=voices, message=None)

@app.route('/task_status/<task_id>')
def task_status(task_id):
    task = async_synthesize_and_save.AsyncResult(task_id)
    if task.state == states.SUCCESS:
        return jsonify({'status': 'SUCCESS', 'result': task.result})
    elif task.state == states.FAILURE:
        return jsonify({'status': 'FAILURE'})
    else:
        return jsonify({'status': 'PENDING'})

@app.route('/audio/<filename>')
def serve_audio(filename):
    return send_file(filename, mimetype='audio/mpeg', as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True, port=5001)