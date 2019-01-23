from os.path import dirname, join, splitext

from flask import Flask, render_template, request
from twilio.twiml.voice_response import VoiceResponse

import config_secrets

app = Flask(__name__)


class SavedResponse:
    """Keeps a response in sync with disk, lazily loaded."""
    FOLDER = join(dirname(__file__), 'static')
    TEXT_PATH = join(FOLDER, 'response.txt')
    AUDIO_PATH = join(FOLDER, 'audio.mp3')

    @staticmethod
    def audio_url():
        return '/static/audio.mp3'

    def __init__(self):
        self._text = self._audio = None
        self.use_text = True

    @property
    def text(self):
        if self._text is None:
            with open(self.TEXT_PATH) as f:
                self._text = f.read()
        return self._text

    @text.setter
    def text(self, value):
        with open(self.TEXT_PATH, 'w') as f:
            f.write(value)
        self._text = value


RESPONSE = SavedResponse()


@app.route('/answer', methods=['GET', 'POST'])
def voice():
    """Respond to incoming phone calls."""
    resp = VoiceResponse()
    if RESPONSE.use_text:
        resp.say(RESPONSE.text)
    else:
        resp.play(url=RESPONSE.audio_url())
    return str(resp)


@app.route('/', methods=['GET', 'POST'])
def edit_message():
    error = ''
    success = ''
    if request.method == 'POST':
        if request.values.get('pw', '') == config_secrets.password and 'mess' in request.values:
            if request.values.get('use-audio'):
                if 'audio-file' not in request.files:
                    error = 'No file provided.'
                else:
                    file = request.files['audio-file']
                    if not file.filename:
                        error = 'Empty file.'
                    elif not splitext(file.filename)[1].lower() == '.mp3':
                        error = 'Invalid file type. Only MP3 is supported.'
                    else:
                        file.save(RESPONSE.AUDIO_PATH)
                        success = 'The new audio message has been set.'
                        RESPONSE.use_text = False
            else:
                new_response = request.values['mess']
                RESPONSE.text = new_response
                RESPONSE.use_text = True
                success = 'The new text message has been set.'
        else:
            error = 'Invalid password.'
    return render_template('editor.html', message=RESPONSE.text, success=success, error=error)


if __name__ == "__main__":
    app.run(host='0.0.0.0')
