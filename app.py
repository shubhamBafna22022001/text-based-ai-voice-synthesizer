from flask import Flask, render_template, request, send_file, jsonify, url_for
import requests, os, logging
from dotenv import load_dotenv
from flask_caching import Cache
from tasks import async_synthesize_and_save
from celery import states

# Additional import for real-time updates using Socket.IO
from flask_socketio import SocketIO, emit

# Initialize Flask app and SocketIO
app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, message_queue='redis://localhost:6379/0')

# Configure caching with Redis
cache = Cache(app, config={
    'CACHE_TYPE': 'redis',
    'CACHE_REDIS_URL': 'redis://localhost:6379/0'
})

# Setup logging
logging.basicConfig(level=logging.INFO, filename='app.log', 
                    format='%(asctime)s %(levelname)s: %(message)s')

# Load API key from .env
load_dotenv()
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

@cache.cached(timeout=300, key_prefix='voices')
def get_voices():
    """Retrieve the list of available voices from the ElevenLabs API."""
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
        emotion_level = request.form.get('emotionLevel', 'neutral')
        pitch = float(request.form.get('pitch', 1.0))
        rate = float(request.form.get('rate', 1.0))

        if not user_text:
            return render_template('index.html.j2', message="Please enter some text.", voices=voices)

        # Queue the task with all synthesis parameters
        task = async_synthesize_and_save.delay(user_text, selected_voice, emotion_level, 
                                                 stability=0.75, similarity_boost=0.75, 
                                                 pitch=pitch, rate=rate)
        # Emit a Socket.IO event to update the client immediately.
        socketio.emit('task_queued', {'task_id': task.id})
        return render_template('index.html.j2', message=f"Task queued! (Task ID: {task.id})", task_id=task.id, voices=voices)

    return render_template('index.html.j2', voices=voices, message=None)

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
    # Using SocketIO's run to enable real-time communication.
    socketio.run(app, debug=True, port=5001)
