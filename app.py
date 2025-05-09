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

# Add at top of app.py
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash

# Add after app initialization
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# User model
class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username
        self.tasks = []

# In-memory user storage for demo purposes
users = {
    '1': User('1', 'demo')
}
users['1'].password_hash = generate_password_hash('demo')

@login_manager.user_loader
def load_user(user_id):
    return users.get(user_id)

# Login route
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # Find user with matching username
        for user_id, user in users.items():
            if user.username == username and check_password_hash(user.password_hash, password):
                login_user(user)
                return redirect(url_for('dashboard'))
                
        return render_template('login.html', error="Invalid credentials")
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


# Add new route to app.py
@app.route('/dashboard')
@login_required
def dashboard():
    # Retrieve task history from saved metadata files
    tasks = []
    for filename in os.listdir(METADATA_DIR):
        if filename.endswith('.json'):
            try:
                with open(os.path.join(METADATA_DIR, filename), 'r') as f:
                    task_data = json.load(f)
                    tasks.append(task_data)
            except Exception as e:
                logging.error(f"Error loading task metadata: {e}")
    
    # Sort tasks by timestamp, newest first
    tasks.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    
    return render_template('dashboard.html', tasks=tasks)

# Add to app.py
# Storage for user voice presets
voice_presets = {}

@app.route('/presets', methods=['GET', 'POST'])
@login_required
def manage_presets():
    user_id = current_user.id
    if user_id not in voice_presets:
        voice_presets[user_id] = []
    
    if request.method == 'POST':
        preset_name = request.form.get('preset_name')
        voice_id = request.form.get('voice')
        emotion = request.form.get('emotionLevel', 'neutral')
        pitch = float(request.form.get('pitch', 1.0))
        rate = float(request.form.get('rate', 1.0))
        
        # Save the preset
        preset = {
            'id': str(uuid.uuid4()),
            'name': preset_name,
            'voice_id': voice_id,
            'emotion': emotion,
            'pitch': pitch,
            'rate': rate,
            'created_at': datetime.datetime.now().isoformat()
        }
        voice_presets[user_id].append(preset)
        
        return redirect(url_for('manage_presets'))
    
    return render_template('presets.html', presets=voice_presets[user_id], voices=get_voices())

@app.route('/presets/delete/<preset_id>')
@login_required
def delete_preset(preset_id):
    user_id = current_user.id
    if user_id in voice_presets:
        voice_presets[user_id] = [p for p in voice_presets[user_id] if p['id'] != preset_id]
    
    return redirect(url_for('manage_presets'))

@app.route('/presets/use/<preset_id>')
@login_required
def use_preset(preset_id):
    user_id = current_user.id
    preset = None
    
    if user_id in voice_presets:
        preset = next((p for p in voice_presets[user_id] if p['id'] == preset_id), None)
    
    if not preset:
        return redirect(url_for('home'))
    
    voices = get_voices()
    return render_template('index.html.j2', voices=voices, preset=preset)

# Add to app.py
@celery_app.task(bind=True)
def process_batch(self, text_list, voice_id, emotion_level, 
                 stability, similarity_boost, pitch, rate, audio_format):
    """Process a batch of text entries"""
    results = []
    for text in text_list:
        try:
            result = async_synthesize_and_save(
                text, voice_id, emotion_level, 
                stability, similarity_boost, pitch, rate, audio_format
            )
            results.append({"text": text, "result": result})
        except Exception as e:
            results.append({"text": text, "error": str(e)})
    
    return results

@app.route('/batch', methods=['GET', 'POST'])
@login_required
def batch_processing():
    voices = get_voices()
    
    if request.method == 'POST':
        batch_text = request.form.get('batch_text', '').strip()
        delimiter = request.form.get('delimiter', 'paragraph')
        voice = request.form.get('voice')
        emotion = request.form.get('emotionLevel', 'neutral')
        pitch = float(request.form.get('pitch', 1.0))
        rate = float(request.form.get('rate', 1.0))
        fmt = request.form.get('audio_format', 'mp3')
        
        # Split text based on selected delimiter
        if delimiter == 'paragraph':
            text_list = [t.strip() for t in batch_text.split('\n\n') if t.strip()]
        elif delimiter == 'line':
            text_list = [t.strip() for t in batch_text.split('\n') if t.strip()]
        elif delimiter == 'custom':
            custom_delim = request.form.get('custom_delimiter', '---')
            text_list = [t.strip() for t in batch_text.split(custom_delim) if t.strip()]
        else:
            text_list = [batch_text]
        
        # Start batch processing task
        task = process_batch.delay(
            text_list, voice, emotion, 0.75, 0.75, pitch, rate, fmt
        )
        
        return render_template(
            'batch.html', 
            message=f"Batch processing started! {len(text_list)} items queued (Task ID: {task.id})",
            batch_task_id=task.id,
            voices=voices
        )
    
    return render_template('batch.html', voices=voices)

@app.route('/batch_status/<task_id>')
@login_required
def batch_status(task_id):
    result = process_batch.AsyncResult(task_id)
    
    if result.state == 'SUCCESS':
        return jsonify({'status': 'SUCCESS', 'results': result.result})
    elif result.state == 'FAILURE':
        return jsonify({'status': 'FAILURE'})
    else:
        return jsonify({'status': 'PENDING'})
# Add to app.py
@app.route('/analytics')
@login_required
def analytics():
    # Load all task metadata
    tasks = []
    for filename in os.listdir(METADATA_DIR):
        if filename.endswith('.json'):
            try:
                with open(os.path.join(METADATA_DIR, filename), 'r') as f:
                    task_data = json.load(f)
                    tasks.append(task_data)
            except Exception as e:
                logging.error(f"Error loading task metadata: {e}")
    
    # Calculate analytics
    total_tasks = len(tasks)
    if total_tasks == 0:
        return render_template('analytics.html', no_data=True)
    
    formats = {}
    emotions = {}
    avg_duration = 0
    total_chars = 0
    
    for task in tasks:
        fmt = task.get('format', 'unknown')
        formats[fmt] = formats.get(fmt, 0) + 1
        
        text = task.get('text', '')
        total_chars += len(text)
        
        duration = task.get('duration_seconds', 0)
        avg_duration += duration
    
    if total_tasks > 0:
        avg_duration /= total_tasks
        avg_chars = total_chars / total_tasks
    else:
        avg_duration = 0
        avg_chars = 0
    
    analytics_data = {
        'total_tasks': total_tasks,
        'formats': formats,
        'avg_duration': round(avg_duration, 2),
        'avg_chars': round(avg_chars),
        'total_chars': total_chars
    }
    
    return render_template('analytics.html', data=analytics_data)
# Add to app.py
@celery_app.task(bind=True)
def advanced_audio_processing(self, audio_filename, effects):
    """Apply post-processing effects to an audio file"""
    try:
        audio = AudioSegment.from_file(audio_filename)
        
        # Apply volume adjustment
        if 'volume' in effects:
            volume_db = effects['volume']
            audio = audio + volume_db
        
        # Apply fade in/out
        if 'fade_in' in effects:
            fade_in_ms = effects['fade_in']
            audio = audio.fade_in(fade_in_ms)
        
        if 'fade_out' in effects:
            fade_out_ms = effects['fade_out']
            audio = audio.fade_out(fade_out_ms)
        
        # Apply speed change (without affecting pitch)
        if 'speed' in effects:
            speed_factor = effects['speed']
            audio = audio._spawn(audio.raw_data, overrides={
                "frame_rate": int(audio.frame_rate * speed_factor)
            }).set_frame_rate(audio.frame_rate)
        
        # Apply normalization
        if effects.get('normalize', False):
            audio = audio.normalize()
        
        # Save processed file
        output_filename = audio_filename.replace(".mp3", "_processed.mp3")
        audio.export(output_filename, format="mp3")
        
        return output_filename
    
    except Exception as e:
        logging.exception(f"Error processing audio: {e}")
        raise self.retry(exc=e, countdown=5)

@app.route('/process_audio/<task_id>', methods=['GET', 'POST'])
@login_required
def process_audio(task_id):
    # Get the original audio filename
    path = os.path.join(METADATA_DIR, f"{task_id}.json")
    if not os.path.exists(path):
        return jsonify({'error': 'Task metadata not found'}), 404
    
    with open(path, 'r') as f:
        metadata = json.load(f)
    
    if request.method == 'POST':
        effects = {
            'volume': float(request.form.get('volume', 0)),
            'fade_in': int(request.form.get('fade_in', 0)),
            'fade_out': int(request.form.get('fade_out', 0)),
            'normalize': request.form.get('normalize') == 'on',
        }
        
        if request.form.get('speed'):
            effects['speed'] = float(request.form.get('speed', 1.0))
        
        # Start processing task
        task = advanced_audio_processing.delay(metadata['output_file'], effects)
        
        return render_template('processing.html', 
                              audio_file=metadata['output_file'],
                              processing_task_id=task.id)
    
    return render_template('processing.html', audio_file=metadata['output_file'])

@app.route('/processing_status/<task_id>')
@login_required
def processing_status(task_id):
    result = advanced_audio_processing.AsyncResult(task_id)
    
    if result.state == 'SUCCESS':
        return jsonify({'status': 'SUCCESS', 'result': result.result})
    elif result.state == 'FAILURE':
        return jsonify({'status': 'FAILURE'})
    else:
        return jsonify({'status': 'PENDING'})

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
    socketio.run(app, debug=True, port=5001)
