import re
import traceback
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from functools import wraps
from os.path import splitext

import requests
from flask import Flask, Response, make_response, redirect, render_template, request, url_for
from twilio.twiml.voice_response import Gather, VoiceResponse

from storage import CallLog, CodedMessages, Config, Contacts, Cookies, IdNumbers, Ignored, OpenHours, Secrets

app = Flask(__name__)

SECRETS = Secrets()
IGNORED = Ignored()
CALL_LOG = CallLog()
COOKIES = Cookies()
CODED = CodedMessages()
CONFIG = Config()
OPEN_HOURS = OpenHours()
CONTACTS = Contacts()
ID_NUMBERS = IdNumbers()

PACIFIC_TIME = timezone(timedelta(hours=-7))


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
                           success=success, code_counter=code_counter, unique_codes=count_unique_code_usages(table),
                           contacts=CONTACTS)


def count_unique_code_usages(table):
    temp = defaultdict(set)
    for number, _, code, __ in table:
        temp[code].add(number)
    return {key: len(value) for key, value in temp.items()}


def log_request():
    if request.method == 'POST' and {'Caller', 'CallSid'}.issubset(request.values):
        if not CALL_LOG.has_called(request.values['Caller']):
            send_welcome_message(request.values['Caller'])
        CALL_LOG.add(request.values['Caller'], datetime.now().strftime('%c'), request.values['CallSid'])


def send_welcome_message(phone_num):
    try:
        url = SECRETS['welcome_url']
        exchange = SECRETS['welcome_exchange_name']
        password = SECRETS['welcome_system_password']
    except KeyError:
        return
    if not all((url, exchange, password)):
        return
    try:
        requests.post(url, data={'phone_num': phone_num,
                                 'exchange': exchange,
                                 'password': password})
    except requests.exceptions.RequestException as e:
        traceback.print_exc()
        return


@app.route('/configure_welcome', methods=['GET'])
@authenticated
def configure_welcome():
    welcome_data = {'url': SECRETS.get('welcome_url'),
                    'exchange': SECRETS.get('welcome_exchange_name'),
                    'password': SECRETS.get('welcome_system_password')}
    return render_template('configure_welcome.html', welcome_data=welcome_data)


@app.route('/configure_welcome', methods=['POST'])
@authenticated
def configure_welcome_post():
    url = request.values.get('url')
    exchange = request.values.get('exchange')
    password = request.values.get('password')
    if None in (url, exchange, password):
        return 'url, exchange, and password are required.', 400
    SECRETS['welcome_url'] = url
    SECRETS['welcome_exchange_name'] = exchange
    SECRETS['welcome_system_password'] = password
    return redirect(url_for('configure_welcome'))


def log_digits():
    if request.method == 'POST' and {'Digits', 'CallSid'}.issubset(request.values):
        CALL_LOG.set_code(request.values['CallSid'], request.values['Digits'])


def log_id():
    if request.method == 'POST' and {'Digits', 'CallSid'}.issubset(request.values):
        CALL_LOG.set_idnum(request.values['CallSid'], request.values['Digits'])


@app.route('/contacts', methods=['GET'])
@authenticated
def contacts():
    return render_template('contacts.html', contacts=CONTACTS)


@app.route('/contacts/delete', methods=['POST'])
@authenticated
def delete_contact():
    number = request.values.get('number')
    if not number:
        return 'No number!', 400
    try:
        del CONTACTS[number]
    except KeyError:
        return 'Unknown number!', 400
    return redirect(url_for('contacts'))


@app.route('/contacts/add', methods=['POST'])
@authenticated
def add_contact():
    number = request.values.get('number')
    name = request.values.get('name')
    if not number or not name:
        return 'Name and number are required!', 400
    number = ''.join(s for s in number if s.isnumeric())
    if len(number) == 10:
        number = '1' + number
    number = '+' + number
    CONTACTS[number] = name
    return redirect(url_for('contacts'))


@app.route('/answer', methods=['GET', 'POST'])
def voice():
    """Respond to incoming phone calls."""
    log_request()

    resp = VoiceResponse()
    if is_open():
        if 'prompt' in CODED:
            gather = Gather(action=url_for('answer_digits'))
            add_message(gather, 'prompt')
            resp.append(gather)
        resp.redirect(url_for('answer_digits'))
    else:
        if 'closed' in CODED:
            do_prompt = 'prompt' in CODED
            if do_prompt:
                gather = Gather(action=url_for('answer_digits'))
                resp.append(gather)
            else:
                gather = resp
            add_message(gather, 'closed')
    return str(resp)


def is_open():
    now = datetime.now(tz=PACIFIC_TIME)
    open_, close = OPEN_HOURS.get(now.weekday())
    if None in (open_, close):
        return True
    now_str = now.strftime('%H:%M')
    return open_ <= now_str < close


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

    message_options = get_options(CODED.get_options(digits))
    require_id = message_options.get('require_id')
    register_id = message_options.get('register_id')
    if require_id or register_id:
        gather = Gather(
            action=url_for('answer_id', original_digits=digits, require_id=require_id, register_id=register_id))
        add_message(gather, 'id-prompt')
        resp.append(gather)
    else:
        add_message(resp, digits)
    return str(resp)


@app.route('/answer/id', methods=['GET', 'POST'])
def answer_id():
    """Respond to a call by prompting for an ID number."""
    log_id()

    resp = VoiceResponse()
    id_num = request.values.get('Digits', '')
    digits = request.values.get('original_digits', '')
    require_id = request.values.get('require_id', '') == 'True'
    register_id = request.values.get('register_id', '') == 'True'

    if register_id:
        if check_new_id(id_num):
            ID_NUMBERS.add(id_num)
            add_message(resp, 'good-id')
            add_message(resp, digits)
        else:
            add_message(resp, 'bad-id')
    elif require_id:
        if id_num in ID_NUMBERS:
            add_message(resp, digits)  # this isn't secure because audio can be accessed directly at URL by anyone.
        else:
            add_message(resp, 'unknown-id')
    return str(resp)


def check_new_id(id_num):
    id_regex = SECRETS.get('id_regex')
    if id_regex is None:
        return True
    return re.match(id_regex, id_num)


def add_message(thing, code):
    """Add the message from code to thing."""
    message_is_text = CODED.get_response_type(code)
    if message_is_text:
        thing.say(CODED.get_response_text(code))
    else:
        thing.play(url_for('answer_audio', code=code))


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
                    code = request.values.get('code', '')
                    CODED.set_audio(code, contents, file.filename)
                    file.close()
                    add_options(code)
                    success = 'The new audio message has been set.'
        elif request.values.get('type') == 'text':
            code = request.values.get('code', '')
            CODED.set_text(code, request.values['mess'])
            add_options(code)
            success = 'The new text message has been set.'
        else:
            error = 'Unknown response type {!r}.'.format(request.values.get('type'))
    return render_template('editor.html', coded_messages=CODED, success=success, error=error)


def add_options(code):
    require_id = request.values.get('require-id', 'off') == 'on'
    register_id = request.values.get('register-id', 'off') == 'on'
    CODED.set_options(code, set_options(require_id=require_id, register_id=register_id))


def set_options(*, require_id=False, register_id=False):
    """Convert boolean options to a bitmasked integer."""
    opts = 0
    opts |= require_id
    opts |= (register_id << 1)
    return opts


def get_options(num):
    """Convert bitmasked int options into a dict."""
    return {
        'require_id': bool(num & 1),
        'register_id': bool(num & (1 << 1)),
    }


@app.route('/prompt', methods=['GET', 'POST'])
@authenticated
def edit_prompt():
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
                    CODED.set_audio('prompt', contents, file.filename)
                    file.close()
                    success = 'The new audio prompt has been set.'
        elif request.values.get('type') == 'text':
            CODED.set_text('prompt', request.values['mess'])
            success = 'The new text prompt has been set.'
        elif request.values.get('type') == 'none':
            CODED.delete_reponse('prompt')
            success = 'The prompt has been removed.'
        else:
            error = 'Unknown response type {!r}.'.format(request.values.get('type'))
    return render_template('prompt-editor.html', coded_messages=CODED, success=success, error=error)


@app.route('/delete_code_response')
@authenticated
def delete_code_response():
    code = request.values.get('code')
    if code:
        CODED.delete_reponse(code)
    return redirect(url_for('edit_message'))


@app.route('/open_hours', methods=['GET'])
@authenticated
def open_hours():
    time_table = iter(OPEN_HOURS)
    if len(OPEN_HOURS) == 0:
        time_table = (
            (i, None, None)
            for i in range(7)
        )
    return render_template('open_hours.html', time_table=time_table)


@app.route('/open_hours', methods=['POST'])
@authenticated
def update_open():
    if not validate_open():
        return 'Error! All closings must be later than openings!'
    opens = {i: request.values['open-{}'.format(i)] for i in range(7)}
    closes = {i: request.values['close-{}'.format(i)] for i in range(7)}
    OPEN_HOURS.set(opens, closes)
    return redirect(url_for('open_hours'))


def validate_open():
    for i in range(7):
        open_ = request.values.get('open-{}'.format(i))
        close = request.values.get('close-{}'.format(i))
        if not (open_ and close):
            return False
        if not (validate_time(open_) and validate_time(close)):
            return False
        if not close >= open_:  # string comparison is sufficient
            return False
    return True


def validate_time(t):
    return (len(t) == 5 and
            t[2] == ':' and
            t[:2].isnumeric() and
            t[3:].isnumeric())


@app.route('/ids', methods=['GET'])
@authenticated
def id_management():
    return render_template('id_management.html', id_numbers=iter(ID_NUMBERS), id_regex=SECRETS.get('id_regex'))


@app.route('/ids/set_regex', methods=['POST'])
@authenticated
def set_id_regex():
    regex = request.values.get('regex')
    if regex:
        SECRETS['id_regex'] = regex
    else:
        del SECRETS['id_regex']
    return redirect(url_for('id_management'))


@app.route('/login', methods=['GET', 'POST'])
def log_in():
    if 'auth' in request.cookies and COOKIES.check(request.cookies['auth']):
        return redirect(request.values.get('dest') or url_for('edit_message'))

    if request.method == 'GET':
        COOKIES.prune()
        return render_template('auth.html')
    elif request.values.get('pw', '') == SECRETS['password']:
        resp = make_response(redirect(request.values.get('dest') or url_for('edit_message')))
        resp.set_cookie('auth', COOKIES.new(), max_age=int(COOKIES.VALID_LENGTH.total_seconds()))
        return resp
    else:
        return render_template('auth.html', error='Incorrect password.')


@app.route('/logout', methods=['GET'])
def log_out():
    cookie = request.cookies.get('auth')
    if cookie:
        COOKIES.remove(cookie)
    return redirect(url_for('log_in'))


@app.route('/hacker.css', methods=['GET'])
def main_theme():
    return Response(render_template('hacker.css', main_color=CONFIG.get('main_color', '#00ff00')), mimetype='text/css')


if __name__ == "__main__":
    app.run()
