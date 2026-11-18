"""Create one initial administrator interactively (no credentials in shell history)."""
import getpass
import re
import sys

import psycopg

from app import db_connection, hash_secret


def main():
    print('PayLite initial admin setup (one-time)')
    name = input('Admin name: ').strip()
    mobile = input('Mobile number (9-15 digits, optional + prefix): ').strip()
    password = getpass.getpass('Strong password (min 12 chars): ')
    pin = getpass.getpass('Wallet PIN (4-6 digits): ')
    if not 2 <= len(name) <= 80 or not re.fullmatch(r'\+?\d{9,15}', mobile):
        sys.exit('Invalid name/mobile number')
    if len(password) < 12 or len(password) > 128 or not re.fullmatch(r'\d{4,6}', pin):
        sys.exit('Password must be 12-128 chars and PIN must be 4-6 digits')
    try:
        with db_connection() as db:
            user = db.execute(
                '''INSERT INTO users(name,mobile,password_hash,pin_hash,role)
                   VALUES (%s,%s,%s,%s,'ADMIN') RETURNING id''',
                (name, mobile, hash_secret(password), hash_secret(pin)),
            ).fetchone()
            db.execute('INSERT INTO wallets(user_id,balance_cents) VALUES (%s,0)', (user['id'],))
        print('Admin account and zero-balance wallet created.')
    except psycopg.errors.UniqueViolation:
        sys.exit('Mobile number already exists. Choose a different admin mobile number.')


if __name__ == '__main__':
    main()
