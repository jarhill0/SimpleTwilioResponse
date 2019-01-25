from storage import Secrets

if __name__ == '__main__':
    try:
        print('The password is {!r}.'.format(Secrets()['password']))
    except KeyError:
        print('No password is set. Use set_password.py to set a password.')
