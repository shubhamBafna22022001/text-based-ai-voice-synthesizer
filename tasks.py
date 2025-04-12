from celery import Celery
import datetime, io, os, logging
from pydub import AudioSegment
import requests
from dotenv import load_dotenv

# Load environment variables and API key
load_dotenv()
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

# Ensure the output directory exists for saving audio files
OUTPUT_DIR = "output_audio"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Configure Celery with Redis (broker and result backend)
celery_app = Celery('tasks', broker='redis://localhost:6379/0', backend='redis://localhost:6379/1')

def safe_filename(prefix="output", ext="mp3"):
    """Utility function to generate safe, timestamped filenames within the output directory."""
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(OUTPUT_DIR, f"{prefix}_{timestamp}.{ext}")

@celery_app.task(bind=True, max_retries=3)
def async_synthesize_and_save(self, text, voice_id, emotion_level="neutral", 
                              stability=0.75, similarity_boost=0.75, 
                              pitch=1.0, rate=1.0):
    """
    Asynchronous task to synthesize text using the ElevenLabs TTS API,
    now with extended parameters including pitch and rate adjustments.
    Enhanced logging and retry mechanism with exponential backoff.
    """
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json"
    }
    # Build the API payload with additional synthesis parameters.
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
    try:
        logging.info("Sending request to TTS API with payload: %s", data)
        response = requests.post(url, json=data, headers=headers, timeout=30)
        logging.info("TTS API response status: %s", response.status_code)
        response.raise_for_status()  # Raise an error for bad responses

        audio_bytes = response.content
        filename = safe_filename()
        # Process the returned audio bytes and export as MP3
        audio = AudioSegment.from_file(io.BytesIO(audio_bytes), format="mp3")
        audio.export(filename, format="mp3")
        logging.info("Audio successfully saved as %s", filename)
        return filename

    except Exception as e:
        logging.exception("Error in synthesis task: %s", e)
        # Exponential backoff for retrying the task
        raise self.retry(exc=e, countdown=5)
