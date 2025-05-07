from celery import Celery
import datetime, io, os, logging, json, uuid
from pydub import AudioSegment
import requests
from dotenv import load_dotenv

# Load environment variables and API key
load_dotenv()
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

# Directories for outputs and metadata
OUTPUT_DIR = "output_audio"
METADATA_DIR = "tasks_metadata"
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(METADATA_DIR, exist_ok=True)

# Configure Celery with Redis (broker and result backend)
celery_app = Celery('tasks', broker='redis://localhost:6379/0', backend='redis://localhost:6379/1')

# Configure logging
logging.basicConfig(level=logging.INFO, filename='app.log', format='%(asctime)s %(levelname)s: %(message)s')


def safe_filename(prefix="output", ext="mp3"):
    """Generate a timestamped, unique filename."""
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    unique_id = uuid.uuid4().hex[:8]
    return os.path.join(OUTPUT_DIR, f"{prefix}_{timestamp}_{unique_id}.{ext}")


@celery_app.task(bind=True, max_retries=3)
def async_synthesize_and_save(self, text, voice_id,
                              emotion_level="neutral",
                              stability=0.75,
                              similarity_boost=0.75,
                              pitch=1.0,
                              rate=1.0,
                              audio_format="mp3"):
    """
    Async task to synthesize text, record timing, save metadata, and support multiple formats.
    """
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json"
    }
    data = {
        "text": text,
        "voice_settings": {
            "stability": stability,
            "similarity_boost": similarity_boost,
            "emotion": emotion_level,
            "pitch": pitch,
            "rate": rate
        }
    }
    start_time = datetime.datetime.utcnow()
    try:
        logging.info("Requesting TTS API: %s", data)
        response = requests.post(url, json=data, headers=headers, timeout=30)
        response.raise_for_status()

        audio_bytes = response.content
        audio = AudioSegment.from_file(io.BytesIO(audio_bytes), format="mp3")
        filename = safe_filename(prefix=voice_id, ext=audio_format)
        audio.export(filename, format=audio_format)

        end_time = datetime.datetime.utcnow()
        duration = (end_time - start_time).total_seconds()

        # Save metadata for this task
        metadata = {
            "task_id": self.request.id,
            "text": text,
            "voice_id": voice_id,
            "format": audio_format,
            "duration_seconds": duration,
            "output_file": filename,
            "timestamp": end_time.isoformat() + 'Z'
        }
        metadata_path = os.path.join(METADATA_DIR, f"{self.request.id}.json")
        with open(metadata_path, 'w') as mf:
            json.dump(metadata, mf, indent=2)

        logging.info("Saved audio: %s and metadata: %s", filename, metadata_path)
        return filename

    except Exception as e:
        logging.exception("Error during synthesis: %s", e)
        raise self.retry(exc=e, countdown=min(60, 2 ** self.request.retries * 5))


# Flask application with real-time feedback and metrics endpoint
from flask import Flask, render_template, request, send_file, jsonify, url_for
from flask_caching import Cache
from flask_socketio import SocketIO

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, message_queue='redis://localhost:6379/0')
cache = Cache(app, config={'CACHE_TYPE': 'redis', 'CACHE_REDIS_URL': 'redis://localhost:6379/0'})

@cache.cached(timeout=300, key_prefix='voices')
def get_voices():
    url = "https://api.elevenlabs.io/v1/voices"
    headers = {"xi-api-key": ELEVENLABS_API_KEY}
    try:
        r = requests.get(url, headers=headers)
        r.raise_for_status()
        return r.json().get('voices', [])
    except Exception:
        logging.exception("Failed to fetch voices")
        return []

@app.route('/', methods=['GET', 'POST'])
def home():
    voices = get_voices()
    if request.method == 'POST':
        text = request.form.get('lyrics', '').strip()
        voice = request.form.get('voice')
        emotion = request.form.get('emotionLevel', 'neutral')
        pitch = float(request.form.get('pitch', 1.0))
        rate = float(request.form.get('rate', 1.0))
        fmt = request.form.get('audio_format', 'mp3')

        if not text:
            return render_template('index.html.j2', message="Please enter some text.", voices=voices)

        task = async_synthesize_and_save.delay(text, voice, emotion,
                                              stability=0.75,
                                              similarity_boost=0.75,
                                              pitch=pitch,
                                              rate=rate,
                                              audio_format=fmt)
        socketio.emit('task_queued', {'task_id': task.id})
        return render_template('index.html.j2', message=f"Task queued! (ID: {task.id})",
                               task_id=task.id, voices=voices)
    return render_template('index.html.j2', voices=voices, message=None)

@app.route('/task_status/<task_id>')
def task_status(task_id):
    result = async_synthesize_and_save.AsyncResult(task_id)
    if result.state == 'SUCCESS':
        return jsonify({'status': 'SUCCESS', 'result': result.result})
    if result.state == 'FAILURE':
        return jsonify({'status': 'FAILURE'})
    return jsonify({'status': 'PENDING'})

@app.route('/metrics/<task_id>')
def metrics(task_id):
    path = os.path.join(METADATA_DIR, f"{task_id}.json")
    if os.path.exists(path):
        return send_file(path, mimetype='application/json')
    return jsonify({'error': 'Metrics not found'}), 404

@app.route('/audio/<path:filename>')
def serve_audio(filename):
    return send_file(filename, mimetype='audio/mpeg', as_attachment=True)

if __name__ == '__main__':
    socketio.run(app, debug=True , port=5001)
