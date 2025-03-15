from celery import Celery
import datetime, io, os, logging
from pydub import AudioSegment
import requests
from dotenv import load_dotenv

load_dotenv()
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

# Set up Celery with Redis as the broker and result backend
celery_app = Celery('tasks', broker='redis://localhost:6379/0', backend='redis://localhost:6379/1')

@celery_app.task
def async_synthesize_and_save(text, voice_id, stability=0.75, similarity_boost=0.75):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json"
    }
    data = {
        "text": text,
        "voice_settings": {
            "stability": stability,
            "similarity_boost": similarity_boost
        }
    }
    try:
        response = requests.post(url, json=data, headers=headers)
        response.raise_for_status()
        audio_bytes = response.content
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"output_{timestamp}.mp3"
        audio = AudioSegment.from_file(io.BytesIO(audio_bytes), format="mp3")
        audio.export(filename, format="mp3")
        return filename
    except Exception as e:
        logging.exception("Asynchronous synthesis error: %s", e)
        return None