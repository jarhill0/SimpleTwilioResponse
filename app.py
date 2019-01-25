from datetime import datetime
from functools import wraps
from os.path import dirname, join, splitext

from flask import Flask, make_response, redirect, render_template, request, url_for
from twilio.twiml.voice_response import VoiceResponse

from storage import CallLog, Cookies, Ignored, Secrets

app = Flask(__name__)

SECRETS = Secrets()
IGNORED = Ignored()
CALL_LOG = CallLog()
COOKIES = Cookies()


class SavedResponse:
    """Keeps a response in sync with disk, lazily loaded."""
    AUDIO_PATH = join(dirname(__file__), 'static', 'audio.mp3')
    _RESPONSE_TYPE_NAME = 'response_type'
    _TEXT_NAME = 'text'

    @staticmethod
    def audio_url():
        return '/static/audio.mp3'

    @property
    def text(self):
        return SECRETS.get(self._TEXT_NAME, '')

    @text.setter
    def text(self, value):
        SECRETS[self._TEXT_NAME] = value

    @property
    def use_text(self):
        return SECRETS.get(self._RESPONSE_TYPE_NAME, 'text') == 'text'

    @use_text.setter
    def use_text(self, value):
        SECRETS[self._RESPONSE_TYPE_NAME] = 'text' if value else 'audio'


RESPONSE = SavedResponse()


def authenticated(route):
    """Wrap a function that needs to be authenticated."""

    @wraps(route)
    def auth_wrapper(*args, **kwargs):
        if 'auth' in request.cookies and COOKIES.check(request.cookies['auth']):
            return route(*args, **kwargs)
        return redirect(url_for('log_in', dest=request.path))

    return auth_wrapper


@app.route('/analytics', methods=['GET', 'POST'])
@authenticated
def analytics():
    error = ''
    success = ''
    if request.method == 'POST':
        if 'num' in request.values:  # adding/removing an ignored number:
            number = request.values['num'].strip()

            if not number.startswith('+'):
                error = 'Error: Number must begin with +'

            elif number not in IGNORED:
                IGNORED.add(number)
                success = 'Added {} to ignored numbers.'.format(number)
            else:
                IGNORED.remove(number)
                success = 'Removed {} from ignored numbers.'.format(number)

    table = CALL_LOG.filter_ignored()
    uniques = len(set(row[0] for row in table))
    return render_template('analytics.html', table=table, uniques=uniques, ignored=IGNORED, error=error,
                           success=success)


def log_request():
    if request.method == 'POST':
        CALL_LOG.add(request.values['Caller'], datetime.now().strftime('%c'))


@app.route('/answer', methods=['GET', 'POST'])
def voice():
    """Respond to incoming phone calls."""
    try:
        log_request()
    except KeyError:
        pass
    resp = VoiceResponse()
    if RESPONSE.use_text:
        resp.say(RESPONSE.text)
    else:
        resp.play(url=RESPONSE.audio_url())
    return str(resp)


@app.route('/', methods=['GET', 'POST'])
@authenticated
def edit_message():
    error = ''
    success = ''
    if request.method == 'POST':
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
            RESPONSE.text = request.values['mess']
            RESPONSE.use_text = True
            success = 'The new text message has been set.'
    return render_template('editor.html', message=RESPONSE.text, checked=not RESPONSE.use_text,
                           success=success, error=error)


@app.route('/login', methods=['GET', 'POST'])
def log_in():
    if 'auth' in request.cookies and COOKIES.check(request.cookies['auth']):
        return redirect(request.values.get('dest') or url_for('edit_message'))

    if request.method == 'GET':
        COOKIES.prune()
        return render_template('auth.html')
    elif request.values.get('pw', '') == SECRETS['password']:
        resp = make_response(redirect(request.values.get('dest') or url_for('edit_message')))
        resp.set_cookie('auth', COOKIES.new())
        return resp
    else:
        return render_template('auth.html', error='Incorrect password.')


@app.route('/logout', methods=['GET'])
def log_out():
    cookie = request.cookies.get('auth')
    if cookie:
        COOKIES.remove(cookie)
    return redirect(url_for('log_in'))


if __name__ == "__main__":
    app.run(host='0.0.0.0')
