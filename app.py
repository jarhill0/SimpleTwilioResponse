from csv import reader
from datetime import datetime
from os.path import dirname, join, splitext

from flask import Flask, render_template, request
from twilio.twiml.voice_response import VoiceResponse

import config_secrets

app = Flask(__name__)

ANALYTICS_PATH = join(dirname(__file__), 'analytics.csv')


class SavedResponse:
    """Keeps a response in sync with disk, lazily loaded."""
    FOLDER = join(dirname(__file__), 'static')
    TEXT_PATH = join(FOLDER, 'response.txt')
    AUDIO_PATH = join(FOLDER, 'audio.mp3')
    TYPE_PATH = join(FOLDER, 'type.txt')

    @staticmethod
    def audio_url():
        return '/static/audio.mp3'

    def __init__(self):
        self._text = self._audio = self._use_text = None

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

    @property
    def use_text(self):
        if self._use_text is None:
            try:
                with open(self.TYPE_PATH) as f:
                    self._use_text = f.read().lower().strip() == 'text'
            except IOError:
                self._use_text = True
        return self._use_text

    @use_text.setter
    def use_text(self, value):
        with open(self.TYPE_PATH, 'w') as f:
            f.write('text' if value else 'audio')
        self._use_text = value


class IgnoredUsers:
    IGNORED_PATH = join(dirname(__file__), 'ignored.csv')

    def __init__(self):
        self._ignored = None

    @property
    def ignored(self):
        if self._ignored is None:
            with open(self.IGNORED_PATH, newline='') as f:
                self._ignored = set(str(line[0]) for line in reader(f, delimiter=','))
        return self._ignored

    def add(self, number):
        number = str(number)
        if number not in self._ignored:
            with open(self.IGNORED_PATH, 'a') as f:
                f.write(number + '\n')
        self._ignored.add(number)


RESPONSE = SavedResponse()
IGNORED = IgnoredUsers()


@app.route('/analytics', methods=['GET', 'POST'])
def analytics():
    if request.method == 'POST' and request.values.get('pw') == config_secrets.password:
        if 'num' in request.values:  # adding an ignored number:
            IGNORED.add(request.values['num'])
        with open(ANALYTICS_PATH, newline='') as csvfile:
            table = list(tuple(row) for row in reader(csvfile, delimiter=',') if str(row[0]) not in IGNORED.ignored)
        return render_template('analytics.html', table=table, uniques=len(set(row[0] for row in table)) - 1,
                               ignored=IGNORED.ignored)
    return render_template('auth.html')


def log_request():
    if request.method == 'POST':
        data = [request.values['Caller'].replace(',', ''),  # just in case bad input
                datetime.now().strftime('%c')]
        with open(ANALYTICS_PATH, 'a') as f:
            f.write(','.join(data) + '\n')


@app.route('/answer', methods=['GET', 'POST'])
def voice():
    """Respond to incoming phone calls."""
    try:
        log_request()
    except Exception:
        pass
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
    return render_template('editor.html', message=RESPONSE.text, checked=not RESPONSE.use_text,
                           success=success, error=error)


if __name__ == "__main__":
    app.run(host='0.0.0.0')
