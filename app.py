from collections import Counter, defaultdict
from datetime import datetime
from functools import wraps
from os.path import splitext

from flask import Flask, Response, make_response, redirect, render_template, request, url_for
from twilio.twiml.voice_response import Gather, VoiceResponse

from storage import CallLog, CodedMessages, Cookies, Ignored, Secrets

app = Flask(__name__)

SECRETS = Secrets()
IGNORED = Ignored()
CALL_LOG = CallLog()
COOKIES = Cookies()
CODED = CodedMessages()


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
    code_counter = sorted(tuple(Counter(row[2] for row in table if row[2] is not None).items()),
                          key=lambda tup: (tup[1], tup[0]), reverse=True)
    return render_template('analytics.html', table=table, uniques=uniques, ignored=IGNORED, error=error,
                           success=success, code_counter=code_counter, unique_codes=count_unique_code_usages(table))


def count_unique_code_usages(table):
    temp = defaultdict(set)
    for number, _, code in table:
        temp[code].add(number)
    return {key: len(value) for key, value in temp.items()}


def log_request():
    if request.method == 'POST' and {'Caller', 'CallSid'}.issubset(request.values):
        CALL_LOG.add(request.values['Caller'], datetime.now().strftime('%c'), request.values['CallSid'])


def log_digits():
    if request.method == 'POST' and {'Digits', 'CallSid'}.issubset(request.values):
        CALL_LOG.set_code(request.values['CallSid'], request.values['Digits'])


@app.route('/answer', methods=['GET', 'POST'])
def voice():
    """Respond to incoming phone calls."""
    log_request()

    resp = VoiceResponse()
    gather = Gather(action=url_for('answer_digits'))
    gather.say('Enter a 3-digit code, if you have one. Then press pound. '
               'Or, press pound to continue without entering a code.')
    resp.append(gather)
    resp.redirect(url_for('answer_digits'))
    return str(resp)


@app.route('/answer/audio.mp3', methods=['GET', 'POST'])
def answer_audio():
    digits = request.values.get('code', '')
    audio = CODED.get_response_audio(digits)
    if not isinstance(audio, bytes):
        return ''
    return Response(audio, mimetype='audio/mpeg')


@app.route('/answer/digits', methods=['GET', 'POST'])
def answer_digits():
    """Respond to a call with a special message."""
    log_digits()
    resp = VoiceResponse()

    digits = request.values.get('Digits', '')
    message_is_text = CODED.get_response_type(digits)
    if message_is_text:
        resp.say(CODED.get_response_text(digits))
    else:
        resp.play(url_for('answer_audio', code=digits))
    return str(resp)


@app.route('/', methods=['GET', 'POST'])
@authenticated
def edit_message():
    error = ''
    success = ''
    if request.method == 'POST':
        if request.values.get('type') == 'audio':
            if 'audio-file' not in request.files:
                error = 'No file provided.'
            else:
                file = request.files['audio-file']
                if not file.filename:
                    error = 'Empty file.'
                elif not splitext(file.filename)[1].lower() == '.mp3':
                    error = 'Invalid file type. Only MP3 is supported.'
                else:
                    contents = file.read()
                    CODED.set_audio(request.values.get('code', ''), contents, file.filename)
                    file.close()
                    success = 'The new audio message has been set.'
        elif request.values.get('type') == 'text':
            CODED.set_text(request.values.get('code', ''), request.values['mess'])
            success = 'The new text message has been set.'
        else:
            error = 'Unknown response type {!r}.'.format(request.values.get('type'))
    return render_template('editor.html', coded_messages=CODED, success=success, error=error)


@app.route('/delete_code_response')
@authenticated
def delete_code_response():
    code = request.values.get('code')
    if code:
        CODED.delete_reponse(code)
    return redirect(url_for('edit_message'))


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
