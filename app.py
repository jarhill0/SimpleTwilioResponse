from os.path import dirname, join

from flask import Flask, render_template, request
from twilio.twiml.voice_response import VoiceResponse

import config_secrets

app = Flask(__name__)


class SavedResponse:
    """Keeps a response in sync with disk, lazily loaded."""
    FILE_PATH = join(dirname(__file__), 'static', 'response.txt')

    def __init__(self):
        self._resp = None

    @property
    def text(self):
        if self._resp is None:
            with open(self.FILE_PATH) as f:
                self._resp = f.read()
        return self._resp

    @text.setter
    def text(self, value):
        with open(self.FILE_PATH, 'w') as f:
            f.write(value)
        self._resp = value


RESPONSE = SavedResponse()


@app.route('/answer', methods=['GET', 'POST'])
def voice():
    """Respond to incoming phone calls."""
    resp = VoiceResponse()
    resp.say(RESPONSE.text)
    return str(resp)


@app.route('/', methods=['GET', 'POST'])
def edit_message():
    saved = False
    valid_pass = True
    if request.method == 'POST':
        valid_pass = request.values.get('pw', '') == config_secrets.password
        if valid_pass and 'mess' in request.values:
            new_response = request.values['mess']
            RESPONSE.text = new_response
            saved = True
    return render_template('editor.html', message=RESPONSE.text, saved=saved, valid_pass=valid_pass)


if __name__ == "__main__":
    app.run(host='0.0.0.0')
