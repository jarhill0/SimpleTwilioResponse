import sqlite3
from datetime import datetime, timedelta
from os.path import dirname, join
from secrets import token_hex


class Storage:
    TABLE_NAME = None
    TABLE_SCHEMA = ''

    @staticmethod
    def connection():
        return sqlite3.connect(join(dirname(__file__), 'twilio_data.sqlite'))

    def __init__(self):
        if self.TABLE_NAME is None:
            raise NotImplementedError('`TABLE_NAME` needs to be specified.')
        conn = self.connection()
        cursor = conn.cursor()
        cursor.execute('CREATE TABLE IF NOT EXISTS {} ({})'.format(self.TABLE_NAME, self.TABLE_SCHEMA))
        conn.commit()

    def __len__(self):
        cursor = self.connection().cursor()
        return cursor.execute('SELECT Count(*) FROM {}'.format(self.TABLE_NAME)).fetchone()[0]

    def _contains(self, column_name, item):
        cursor = self.connection().cursor()
        return cursor.execute('SELECT {col} FROM {tab} WHERE {col}=?'.format(tab=self.TABLE_NAME,
                                                                             col=column_name),
                              (item,)).fetchone()

    def _iterate_column(self, column_name):
        cursor = self.connection().cursor()
        return (row[0] for row in cursor.execute('SELECT {col} from {tab} ORDER BY {col} ASC'.format(col=column_name,
                                                                                                     tab=self.TABLE_NAME)))

    def _iterate_columns(self, *columns, order_by=''):
        cursor = self.connection().cursor()
        return cursor.execute('SELECT {cols} from {tab} {order}'.format(cols=', '.join(columns), tab=self.TABLE_NAME,
                                                                        order=order_by))

    def _remove(self, column_name, value):
        conn = self.connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM {tab} WHERE {col}=?'.format(tab=self.TABLE_NAME, col=column_name), (value,))
        conn.commit()

    def clear(self):
        conn = self.connection()
        cursor = conn.cursor()
        cursor.execute('DROP TABLE {}'.format(self.TABLE_NAME))
        cursor.execute('CREATE TABLE IF NOT EXISTS {} ({})'.format(self.TABLE_NAME, self.TABLE_SCHEMA))
        conn.commit()


class CallLog(Storage):
    TABLE_NAME = 'calls'
    TABLE_SCHEMA = ('id INTEGER PRIMARY KEY NOT NULL, number TEXT NOT NULL, timestamp DATETIME NOT NULL, '
                    'call_sid TEXT, code TEXT')

    def __iter__(self):
        return self._iterate_columns('number', 'timestamp', 'code', order_by='ORDER BY timestamp ASC')

    def add(self, number, time, call_sid):
        conn = self.connection()
        conn.cursor().execute('INSERT INTO {} (number, timestamp, call_sid) VALUES (?, ?, ?)'.format(
            self.TABLE_NAME),
            (number, time, call_sid))
        conn.commit()

    def filter_ignored(self):
        """Get the numbers that aren't ignored.

        Brittle, hardcoded method.
        """
        cursor = self.connection().cursor()
        return cursor.execute('SELECT number, timestamp, code FROM {tab} '.format(tab=self.TABLE_NAME) +
                              'WHERE number not in (SELECT number FROM {ig_tab})'.format(
                                  ig_tab=Ignored.TABLE_NAME)).fetchall()

    def set_code(self, call_sid, code):
        conn = self.connection()
        conn.cursor().execute('UPDATE {} SET code=? WHERE call_sid=?'.format(self.TABLE_NAME), (code, call_sid))
        conn.commit()


class CodedMessages(Storage):
    TABLE_NAME = 'coded_messages'
    TABLE_SCHEMA = ('id INTEGER PRIMARY KEY NOT NULL, code TEXT NOT NULL UNIQUE, '
                    'use_text TINYINT NOT NULL, text_ TEXT, audio BLOB, file_name TEXT')

    def __contains__(self, item):
        return self._contains('code', item)

    def codes(self):
        return self._iterate_column('code')

    def delete_reponse(self, code):
        conn = self.connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM {} WHERE code=?'.format(self.TABLE_NAME), (code,))
        conn.commit()

    def get_response_audio(self, code):
        """Returns response audio."""
        cursor = self.connection().cursor()
        row = cursor.execute('SELECT audio FROM {} WHERE code=?'.format(self.TABLE_NAME), (str(code),)).fetchone()

        if not row:
            if code:  # unknown code; revert to default
                return self.get_response_audio('')
            else:  # no default case! This should never happen, but just in case...
                return None

        return row[0]

    def get_response_file_name(self, code):
        """Returns response file name."""
        cursor = self.connection().cursor()
        row = cursor.execute('SELECT file_name FROM {} WHERE code=?'.format(self.TABLE_NAME), (str(code),)).fetchone()

        if not row:
            if code:  # unknown code; revert to default
                return self.get_response_file_name('')
            else:  # no default case! This should never happen, but just in case...
                return ''

        return row[0]

    def get_response_text(self, code):
        """Returns response text."""
        cursor = self.connection().cursor()
        row = cursor.execute('SELECT text_ FROM {} WHERE code=?'.format(self.TABLE_NAME), (str(code),)).fetchone()

        if not row:
            if code:  # unknown code; revert to default
                return self.get_response_text('')
            else:  # no default case!
                return ''

        return row[0]

    def get_response_type(self, code):
        """Returns True if the response type is text."""
        cursor = self.connection().cursor()
        row = cursor.execute('SELECT use_text FROM {} WHERE code=?'.format(self.TABLE_NAME), (str(code),)).fetchone()

        if not row:
            if code:  # unknown code; revert to default
                return self.get_response_type('')
            else:  # no default case!
                return True

        return bool(row[0])

    def set_audio(self, code, audio, file_name):
        conn = self.connection()
        cursor = conn.cursor()
        cursor.execute('REPLACE INTO {} (code, use_text, audio, file_name) VALUES (?, 0, ?, ?)'.format(self.TABLE_NAME),
                       (code, audio, file_name))
        conn.commit()

    def set_text(self, code, text):
        conn = self.connection()
        cursor = conn.cursor()
        cursor.execute('REPLACE INTO {} (code, use_text, text_) VALUES (?, 1, ?)'.format(self.TABLE_NAME),
                       (code, text))
        conn.commit()


class Config(Storage):
    TABLE_NAME = 'config'
    TABLE_SCHEMA = 'name TEXT PRIMARY KEY NOT NULL, value TEXT'

    def __getitem__(self, item):
        cursor = self.connection().cursor()
        result = cursor.execute('SELECT value from {} where name=?'.format(self.TABLE_NAME), (item,)).fetchone()
        if result is None:
            raise KeyError('No known secret with name {!r}.'.format(item))
        return result[0]

    def __setitem__(self, key, value):
        conn = self.connection()
        cursor = conn.cursor()
        cursor.execute('REPLACE INTO {} VALUES (?, ?)'.format(self.TABLE_NAME), (key, value))
        conn.commit()

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default


class Cookies(Storage):
    TABLE_NAME = 'cookies'
    TABLE_SCHEMA = 'id INTEGER PRIMARY KEY NOT NULL, cookie TEXT NOT NULL, expiration DATETIME NOT NULL'

    def check(self, cookie):
        """Check if a cookie is valid."""
        cursor = self.connection().cursor()
        return cursor.execute('SELECT * FROM {} WHERE cookie=? AND expiration>?'.format(self.TABLE_NAME),
                              (cookie, int(datetime.now().timestamp()))).fetchone() is not None

    def new(self):
        """Store a new cookie and return it."""
        val = token_hex(30)
        exp_date = int((datetime.now() + timedelta(days=1)).timestamp())
        conn = self.connection()
        conn.cursor().execute('INSERT INTO {} VALUES (NULL, ?, ?)'.format(self.TABLE_NAME), (val, exp_date))
        conn.commit()
        return val

    def prune(self):
        """Remove cookies that are out-of-date."""
        now = int(datetime.now().timestamp())
        conn = self.connection()
        conn.cursor().execute('DELETE FROM {} WHERE expiration < ?'.format(self.TABLE_NAME), (now,))
        conn.commit()

    def remove(self, cookie):
        """Remove a cookie as part of a logout."""
        self._remove('cookie', cookie)


class Ignored(Storage):
    TABLE_NAME = 'ignored'
    TABLE_SCHEMA = 'number TEXT PRIMARY KEY NOT NULL'

    def __contains__(self, item):
        return self._contains('number', item)

    def __iter__(self):
        return self._iterate_column('number')

    def add(self, number):
        """Perform a set-style addition"""
        conn = self.connection()
        conn.cursor().execute('INSERT OR IGNORE INTO {} VALUES (?)'.format(self.TABLE_NAME), (number,))
        conn.commit()

    def remove(self, number):
        self._remove('number', number)


class OpenHours(Storage):
    TABLE_NAME = 'open_hours'
    TABLE_SCHEMA = 'weekday NUMBER NOT NULL UNIQUE, opening TIME NOT NULL, closing TIME NOT NULL'

    def __iter__(self):
        return self._iterate_columns('weekday', 'opening', 'closing', order_by='ORDER BY weekday ASC')

    def get(self, day):
        cursor = self.connection().cursor()
        resp = cursor.execute('SELECT opening, closing FROM {} WHERE weekday=?'.format(self.TABLE_NAME),
                              (day,)).fetchone()
        return resp or None, None

    def set(self, opens, closes):
        conn = self.connection()
        cursor = conn.cursor()
        for day in range(7):
            cursor.execute('REPLACE INTO {} (weekday, opening, closing) VALUES (?, ?, ?)'.format(self.TABLE_NAME),
                           (day, opens[day], closes[day]))
        conn.commit()


class Secrets(Storage):
    TABLE_NAME = 'secrets'
    TABLE_SCHEMA = 'name TEXT PRIMARY KEY NOT NULL, value TEXT'

    def __getitem__(self, item):
        cursor = self.connection().cursor()
        result = cursor.execute('SELECT value from {} where name=?'.format(self.TABLE_NAME), (item,)).fetchone()
        if result is None:
            raise KeyError('No known secret with name {!r}.'.format(item))
        return result[0]

    def __setitem__(self, key, value):
        conn = self.connection()
        cursor = conn.cursor()
        cursor.execute('REPLACE INTO {} VALUES (?, ?)'.format(self.TABLE_NAME), (key, value))
        conn.commit()

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default
