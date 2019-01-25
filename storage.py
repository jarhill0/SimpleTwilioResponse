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
        return (row[0] for row in cursor.execute('SELECT {col} from {tab}'.format(col=column_name,
                                                                                  tab=self.TABLE_NAME)))

    def _iterate_columns(self, *columns):
        cursor = self.connection().cursor()
        return cursor.execute('SELECT {cols} from {tab}'.format(cols=', '.join(columns), tab=self.TABLE_NAME))

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
    TABLE_SCHEMA = 'id INTEGER PRIMARY KEY NOT NULL, number TEXT NOT NULL, timestamp DATETIME NOT NULL'

    def __iter__(self):
        return self._iterate_columns('number', 'timestamp')

    def add(self, number, time):
        conn = self.connection()
        conn.cursor().execute('INSERT OR IGNORE INTO {} VALUES (NULL, ?, ?)'.format(self.TABLE_NAME),
                              (number, time))
        conn.commit()

    def filter_ignored(self):
        """Get the numbers that aren't ignored.

        Brittle, hardcoded method.
        """
        cursor = self.connection().cursor()
        return cursor.execute('SELECT number, timestamp FROM {tab} '.format(tab=self.TABLE_NAME) +
                              'WHERE number not in (SELECT number FROM {ig_tab})'.format(
                                  ig_tab=Ignored.TABLE_NAME)).fetchall()


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
