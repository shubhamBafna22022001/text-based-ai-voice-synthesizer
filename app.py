from flask import Flask, render_template, request
import requests
import os
from dotenv import load_dotenv
from pydub import AudioSegment
import io

app = Flask(__name__)

# Load API key from .env
load_dotenv()
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

def synthesize_text(text):
    url = "https://api.elevenlabs.io/v1/text-to-speech/21m00Tcm4TlvDq8ikWAM"
    headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    data = {
        "text": text,
        "voice_settings": {"stability": 0.75, "similarity_boost": 0.75}
    }
    response = requests.post(url, json=data, headers=headers)
    if response.status_code == 200:
        return response.content
    else:
        return None

def save_audio(audio_bytes):
    audio = AudioSegment.from_file(io.BytesIO(audio_bytes), format="mp3")
    audio.export("output.mp3", format="mp3")
    return "output.mp3"

@app.route('/', methods=['GET', 'POST'])
def home():
    if request.method == 'POST':
        user_text = request.form['lyrics']
        audio_bytes = synthesize_text(user_text)
        if audio_bytes:
            output_file = save_audio(audio_bytes)
            return render_template('index.html', message=f"Audio generated! Saved as {output_file}")
        else:
            return render_template('index.html', message="Error generating audio. Check API key or limits.")
    return render_template('index.html', message=None)

if __name__ == '__main__':
    app.run(debug=True)