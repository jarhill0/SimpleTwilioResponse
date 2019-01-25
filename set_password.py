from storage import Secrets

if __name__ == '__main__':
    Secrets()['password'] = input('Enter the new password: ')
