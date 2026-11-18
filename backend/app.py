"""PayLite: simulated wallet demo, FastAPI + PostgreSQL + append-only ledger."""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import time
import uuid
from contextlib import contextmanager, asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import psycopg
from psycopg.rows import dict_row
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

ROOT = Path(__file__).resolve().parent
DB_SETTINGS = dict(host=os.getenv('DB_HOST', 'localhost'), port=int(os.getenv('DB_PORT', '5432')),
                   dbname=os.getenv('DB_NAME', 'paylite'), user=os.getenv('DB_USER', 'paylite'),
                   password=os.getenv('DB_PASSWORD', 'paylite_local_demo_2026'))
SEED_DEMO = os.getenv('SEED_DEMO', 'true').lower() in ('1', 'true', 'yes')
PBKDF2_ROUNDS = 220_000
LOCK_SECONDS = 300
SESSION_SECONDS = 86_400
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger('paylite')


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def money(value: int) -> str:
    return f'{Decimal(value) / Decimal(100):.2f}'


def cents(value: Decimal) -> int:
    if not value.is_finite() or value.as_tuple().exponent < -2:
        raise HTTPException(422, 'Amount must have at most two decimal places')
    amount = int(value * 100)
    if amount <= 0:
        raise HTTPException(422, 'Amount must be greater than zero')
    return amount


def hash_secret(value: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', value.encode(), salt, PBKDF2_ROUNDS)
    return f'pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}'


def verify_secret(value: str, encoded: str) -> bool:
    _, rounds, salt, expected = encoded.split('$')
    actual = hashlib.pbkdf2_hmac('sha256', value.encode(), bytes.fromhex(salt), int(rounds))
    return hmac.compare_digest(actual, bytes.fromhex(expected))


@contextmanager
def db_connection():
    # Each request/operation gets a transaction. Exiting normally commits;
    # any exception rolls back the entire transfer/top-up/reversal.
    with psycopg.connect(**DB_SETTINGS, row_factory=dict_row, connect_timeout=5) as conn:
        yield conn


def seed_accounts() -> None:
    """Idempotent demonstration seed. Schema is created by db/001_schema.sql."""
    if not SEED_DEMO:
        return
    accounts = [
        ('Alice Fernando', '0771111111', 'DemoPass123!', '1234', 'CUSTOMER', 100_000),
        ('Bob Silva', '0772222222', 'DemoPass123!', '5678', 'CUSTOMER', 20_000),
        ('Demo Admin', '0779999999', 'AdminPass123!', '9999', 'ADMIN', 0),
    ]
    with db_connection() as db:
        db.execute('SELECT pg_advisory_xact_lock(97531001)')
        for name, mobile, password, pin, role, initial_balance in accounts:
            user = db.execute(
                '''INSERT INTO users(name,mobile,password_hash,pin_hash,role)
                   VALUES (%s,%s,%s,%s,%s) ON CONFLICT(mobile) DO NOTHING RETURNING id''',
                (name, mobile, hash_secret(password), hash_secret(pin), role),
            ).fetchone()
            if user is None:
                continue  # Existing user and wallet are never reset on restart.
            user_id = user['id']
            db.execute('INSERT INTO wallets(user_id,balance_cents) VALUES (%s,%s)', (user_id, initial_balance))
            if initial_balance:
                db.execute(
                    '''INSERT INTO ledger_entries(wallet_id,entry_type,delta_cents,reference,balance_after_cents)
                       VALUES (%s,'TOPUP',%s,'SEED-DEMO',%s)''',
                    (user_id, initial_balance, initial_balance),
                )


def public_user(row: dict) -> dict:
    return {'id': row['id'], 'name': row['name'], 'mobile': row['mobile'], 'role': row['role']}


class RegisterInput(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    mobile: str = Field(pattern=r'^\+?[0-9]{9,15}$')
    password: str = Field(min_length=8, max_length=128)
    pin: str = Field(pattern=r'^[0-9]{4,6}$')

    @field_validator('name', 'mobile')
    @classmethod
    def strip(cls, value: str) -> str:
        return value.strip()


class LoginInput(BaseModel):
    mobile: str
    password: str


class TopupInput(BaseModel):
    amount: Decimal


class TransferInput(BaseModel):
    recipient_mobile: str
    amount: Decimal
    pin: str


class ReversalResponse(BaseModel):
    id: str
    status: str


def require_user(authorization: Annotated[str | None, Header()] = None) -> dict:
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401, 'Please log in', headers={'WWW-Authenticate': 'Bearer'})
    token = authorization.removeprefix('Bearer ')
    with db_connection() as db:
        row = db.execute(
            '''SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
               WHERE s.token_hash=%s AND s.expires_at > now()''',
            (hashlib.sha256(token.encode()).hexdigest(),),
        ).fetchone()
    if row is None:
        raise HTTPException(401, 'Session expired or invalid')
    return row


def require_admin(user: dict = Depends(require_user)) -> dict:
    if user['role'] != 'ADMIN':
        raise HTTPException(403, 'Admin access required')
    return user


def verify_pin(user_id: int, pin: str) -> None:
    error = None
    with db_connection() as db:
        user = db.execute('SELECT * FROM users WHERE id=%s FOR UPDATE', (user_id,)).fetchone()
        if user['pin_locked_until'] and user['pin_locked_until'] > utcnow():
            error = (429, 'PIN locked after 3 failures. Try again in 5 minutes')
        elif not verify_secret(pin, user['pin_hash']):
            failures = user['pin_failures'] + 1
            locked = utcnow() + timedelta(seconds=LOCK_SECONDS) if failures >= 3 else None
            db.execute('UPDATE users SET pin_failures=%s,pin_locked_until=%s WHERE id=%s',
                       (failures, locked, user_id))
            error = ((429, 'PIN locked after 3 failures. Try again in 5 minutes') if failures >= 3
                     else (400, f'Incorrect PIN. {3 - failures} attempts left'))
        else:
            db.execute('UPDATE users SET pin_failures=0,pin_locked_until=NULL WHERE id=%s', (user_id,))
    # Raise AFTER commit so failed attempt counters cannot be rolled back.
    if error:
        raise HTTPException(*error)


def lock_wallets(db, *user_ids: int) -> None:
    # Consistent ascending lock order prevents sender/recipient deadlocks.
    for user_id in sorted(set(user_ids)):
        row = db.execute('SELECT user_id FROM wallets WHERE user_id=%s FOR UPDATE', (user_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'Wallet not found')


def ledger_change(db, user_id: int, delta: int, kind: str, reference: str, counterparty: int | None = None) -> int:
    result = db.execute(
        '''UPDATE wallets SET balance_cents = balance_cents + %s
           WHERE user_id=%s AND balance_cents + %s >= 0 RETURNING balance_cents''',
        (delta, user_id, delta),
    ).fetchone()
    if not result:
        raise HTTPException(400, 'Insufficient balance')
    balance = result['balance_cents']
    db.execute(
        '''INSERT INTO ledger_entries(wallet_id,entry_type,delta_cents,reference,counterparty_id,balance_after_cents)
           VALUES (%s,%s,%s,%s,%s,%s)''',
        (user_id, kind, delta, reference, counterparty, balance),
    )
    return balance


def transfer_result(db, transfer: dict) -> dict:
    recipient = db.execute('SELECT name,mobile FROM users WHERE id=%s', (transfer['recipient_id'],)).fetchone()
    return {'id': transfer['id'], 'status': transfer['status'], 'amount': money(transfer['amount_cents']),
            'recipient': recipient['name'], 'recipient_mobile': recipient['mobile'],
            'created_at': transfer['created_at'].isoformat()}


def replay_or_conflict(db, previous: dict | None, amount: int, mobile: str):
    if previous is None:
        return None
    recipient = db.execute('SELECT mobile FROM users WHERE id=%s', (previous['recipient_id'],)).fetchone()
    if previous['amount_cents'] != amount or recipient['mobile'] != mobile:
        raise HTTPException(409, 'Idempotency key was already used for a different transfer')
    return JSONResponse(status_code=200, content={**transfer_result(db, previous), 'replayed': True})


def today_bounds():
    start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


@asynccontextmanager
async def lifespan(application: FastAPI):
    seed_accounts()
    yield


app = FastAPI(title='PayLite API', version='2.0.0',
              description='Simulated wallet; never use with real money', lifespan=lifespan)
app.state.daily_limit_cents = cents(Decimal(os.getenv('DAILY_TRANSFER_LIMIT', '10000')))
app.mount('/static', StaticFiles(directory=str(ROOT / 'static')), name='static')


@app.middleware('http')
async def request_log(request: Request, call_next):
    proposed = request.headers.get('X-Correlation-ID', '')
    correlation = proposed if re.fullmatch(r'[A-Za-z0-9_-]{8,64}', proposed) else uuid.uuid4().hex
    started = time.monotonic()
    response = await call_next(request)
    response.headers['X-Correlation-ID'] = correlation
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    logger.info(json.dumps({'event': 'http_request', 'correlation_id': correlation, 'method': request.method,
                            'path': request.url.path, 'status': response.status_code,
                            'duration_ms': round((time.monotonic()-started)*1000, 1)}))
    return response


@app.get('/', include_in_schema=False)
def index():
    return FileResponse(ROOT / 'static' / 'index.html')


@app.get('/api/health')
def health():
    with db_connection() as db:
        db.execute('SELECT 1')
    return {'status': 'ok', 'database': 'postgresql'}


@app.post('/api/auth/register', status_code=201)
def register(body: RegisterInput):
    with db_connection() as db:
        row = db.execute(
            '''INSERT INTO users(name,mobile,password_hash,pin_hash,role)
               VALUES (%s,%s,%s,%s,'CUSTOMER') ON CONFLICT(mobile) DO NOTHING RETURNING id''',
            (body.name, body.mobile, hash_secret(body.password), hash_secret(body.pin)),
        ).fetchone()
        if row is None:
            raise HTTPException(409, 'Mobile number is already registered')
        db.execute('INSERT INTO wallets(user_id,balance_cents) VALUES (%s,0)', (row['id'],))
        user = db.execute('SELECT id,name,mobile,role FROM users WHERE id=%s', (row['id'],)).fetchone()
    return {'message': 'Account and wallet created', 'user': public_user(user)}


@app.post('/api/auth/login')
def login(body: LoginInput):
    error = None
    result = None
    with db_connection() as db:
        user = db.execute('SELECT * FROM users WHERE mobile=%s FOR UPDATE', (body.mobile,)).fetchone()
        if user is None:
            error = (401, 'Invalid mobile number or password')
        elif user['login_locked_until'] and user['login_locked_until'] > utcnow():
            error = (429, 'Account locked after 3 failures. Try again in 5 minutes')
        elif not verify_secret(body.password, user['password_hash']):
            failures = user['login_failures'] + 1
            locked = utcnow() + timedelta(seconds=LOCK_SECONDS) if failures >= 3 else None
            db.execute('UPDATE users SET login_failures=%s,login_locked_until=%s WHERE id=%s',
                       (failures, locked, user['id']))
            error = ((429, 'Account locked after 3 failures. Try again in 5 minutes') if failures >= 3
                     else (401, 'Invalid mobile number or password'))
        else:
            db.execute('UPDATE users SET login_failures=0,login_locked_until=NULL WHERE id=%s', (user['id'],))
            token = secrets.token_urlsafe(32)
            expiration = utcnow() + timedelta(seconds=SESSION_SECONDS)
            db.execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES (%s,%s,%s)',
                       (hashlib.sha256(token.encode()).hexdigest(), user['id'], expiration))
            result = {'token': token, 'expires_at': expiration.isoformat(), 'user': public_user(user)}
    if error:
        raise HTTPException(*error)
    return result


@app.post('/api/auth/logout')
def logout(authorization: Annotated[str | None, Header()] = None, user: dict = Depends(require_user)):
    with db_connection() as db:
        db.execute('DELETE FROM sessions WHERE token_hash=%s',
                   (hashlib.sha256(authorization.removeprefix('Bearer ').encode()).hexdigest(),))
    return {'message': 'Logged out'}


@app.get('/api/wallet')
def wallet(user: dict = Depends(require_user)):
    start, end = today_bounds()
    with db_connection() as db:
        balance = db.execute('SELECT balance_cents FROM wallets WHERE user_id=%s', (user['id'],)).fetchone()['balance_cents']
        used = db.execute(
            'SELECT COALESCE(SUM(amount_cents),0) AS value FROM transfers WHERE sender_id=%s AND created_at >= %s AND created_at < %s',
            (user['id'], start, end),
        ).fetchone()['value']
    limit = app.state.daily_limit_cents
    return {'user': public_user(user), 'balance': money(balance), 'daily_limit': money(limit),
            'sent_today': money(used), 'remaining_limit': money(max(0, limit - used))}


@app.get('/api/wallet/integrity')
def integrity(user: dict = Depends(require_user)):
    with db_connection() as db:
        balance = db.execute('SELECT balance_cents FROM wallets WHERE user_id=%s', (user['id'],)).fetchone()['balance_cents']
        rebuilt = db.execute('SELECT COALESCE(SUM(delta_cents),0) AS value FROM ledger_entries WHERE wallet_id=%s',
                             (user['id'],)).fetchone()['value']
    return {'matches': balance == rebuilt, 'wallet_balance': money(balance), 'ledger_balance': money(rebuilt)}


@app.get('/api/users')
def users(user: dict = Depends(require_user)):
    with db_connection() as db:
        rows = db.execute('SELECT id,name,mobile,role FROM users WHERE id<>%s AND role=%s ORDER BY name',
                          (user['id'], 'CUSTOMER')).fetchall()
    return [public_user(row) for row in rows]


@app.post('/api/wallet/topup', status_code=201)
def topup(body: TopupInput, user: dict = Depends(require_user)):
    amount = cents(body.amount)
    if not 100 <= amount <= 500_000:
        raise HTTPException(400, 'Top-up amount must be between 1.00 and 5,000.00')
    with db_connection() as db:
        balance = ledger_change(db, user['id'], amount, 'TOPUP', 'TOPUP-' + uuid.uuid4().hex[:12])
    return {'message': 'Top-up successful', 'balance': money(balance)}


@app.post('/api/transfers', status_code=201)
def send(body: TransferInput, user: dict = Depends(require_user),
         idempotency_key: Annotated[str | None, Header(alias='Idempotency-Key')] = None):
    amount = cents(body.amount)
    if not idempotency_key or not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', idempotency_key):
        raise HTTPException(422, 'Idempotency-Key header is required (8-128 letters, digits, - or _)')
    mobile = body.recipient_mobile.strip()
    if mobile == user['mobile']:
        raise HTTPException(400, 'You cannot send money to yourself')
    with db_connection() as db:
        prior = db.execute('SELECT * FROM transfers WHERE sender_id=%s AND idem_key=%s',
                           (user['id'], idempotency_key)).fetchone()
        replay = replay_or_conflict(db, prior, amount, mobile)
    if replay:
        return replay
    verify_pin(user['id'], body.pin)
    with db_connection() as db:
        recipient = db.execute('SELECT id,name,mobile FROM users WHERE mobile=%s AND role=%s',
                               (mobile, 'CUSTOMER')).fetchone()
        if not recipient:
            raise HTTPException(404, 'Recipient mobile number not found')
        lock_wallets(db, user['id'], recipient['id'])
        # Recheck under wallet locks: simultaneous retries must only debit once.
        prior = db.execute('SELECT * FROM transfers WHERE sender_id=%s AND idem_key=%s',
                           (user['id'], idempotency_key)).fetchone()
        replay = replay_or_conflict(db, prior, amount, mobile)
        if replay:
            return replay
        start, end = today_bounds()
        used = db.execute(
            '''SELECT COALESCE(SUM(amount_cents),0) AS value FROM transfers
               WHERE sender_id=%s AND created_at >= %s AND created_at < %s''',
            (user['id'], start, end),
        ).fetchone()['value']
        remaining = max(0, app.state.daily_limit_cents - used)
        if amount > remaining:
            raise HTTPException(400, f'Daily transfer limit exceeded. Remaining: {money(remaining)}')
        transfer_id = uuid.uuid4().hex[:16].upper()
        saved = db.execute(
            '''INSERT INTO transfers(id,sender_id,recipient_id,amount_cents,idem_key,status)
               VALUES (%s,%s,%s,%s,%s,'COMPLETED') RETURNING *''',
            (transfer_id, user['id'], recipient['id'], amount, idempotency_key),
        ).fetchone()
        ledger_change(db, user['id'], -amount, 'SENT', transfer_id, recipient['id'])
        ledger_change(db, recipient['id'], amount, 'RECEIVED', transfer_id, user['id'])
        return {**transfer_result(db, saved), 'replayed': False}


def statement_rows(user_id: int, start: date | None, end: date | None) -> list[dict]:
    if start and end and start > end:
        raise HTTPException(422, 'From date must be before To date')
    start_dt = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc) if start else None
    end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc) if end else None
    conditions = ['l.wallet_id=%s']
    params = [user_id]
    if start_dt:
        conditions.append('l.created_at >= %s')
        params.append(start_dt)
    if end_dt:
        conditions.append('l.created_at < %s')
        params.append(end_dt)
    with db_connection() as db:
        rows = db.execute(
            '''SELECT l.*,u.name AS counterparty_name,u.mobile AS counterparty_mobile
               FROM ledger_entries l LEFT JOIN users u ON u.id=l.counterparty_id
               WHERE ''' + ' AND '.join(conditions) + ' ORDER BY l.id DESC', params,
        ).fetchall()
    return [{'id': row['id'], 'type': row['entry_type'], 'amount': money(abs(row['delta_cents'])),
             'direction': 'IN' if row['delta_cents'] > 0 else 'OUT',
             'signed_amount': money(row['delta_cents']), 'balance_after': money(row['balance_after_cents']),
             'reference': row['reference'], 'counterparty': row['counterparty_name'],
             'counterparty_mobile': row['counterparty_mobile'], 'at': row['created_at'].isoformat()}
            for row in rows]


@app.get('/api/statement')
def statement(user: dict = Depends(require_user), from_date: date | None = None, to_date: date | None = None):
    return statement_rows(user['id'], from_date, to_date)


@app.get('/api/statement/export')
def export_statement(user: dict = Depends(require_user), from_date: date | None = None, to_date: date | None = None):
    records = statement_rows(user['id'], from_date, to_date)
    output = io.StringIO()
    columns = ['id', 'at', 'type', 'direction', 'counterparty', 'counterparty_mobile',
               'amount', 'signed_amount', 'balance_after', 'reference']
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(records)
    return Response(content=output.getvalue(), media_type='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=paylite-statement.csv'})


@app.get('/api/admin/transfers')
def admin_transfers(_admin: dict = Depends(require_admin)):
    with db_connection() as db:
        rows = db.execute(
            '''SELECT t.*,a.name AS sender_name,a.mobile AS sender_mobile,
               b.name AS recipient_name,b.mobile AS recipient_mobile FROM transfers t
               JOIN users a ON a.id=t.sender_id JOIN users b ON b.id=t.recipient_id
               ORDER BY t.created_at DESC,t.id DESC LIMIT 100'''
        ).fetchall()
    return [{'id': row['id'], 'sender': row['sender_name'], 'sender_mobile': row['sender_mobile'],
             'recipient': row['recipient_name'], 'recipient_mobile': row['recipient_mobile'],
             'amount': money(row['amount_cents']), 'status': row['status'],
             'created_at': row['created_at'].isoformat()} for row in rows]


@app.post('/api/admin/transfers/{transfer_id}/reverse', response_model=ReversalResponse)
def reverse_transfer(transfer_id: str, _admin: dict = Depends(require_admin)):
    with db_connection() as db:
        # Find participants, then lock both wallet rows in consistent order.
        transfer = db.execute('SELECT * FROM transfers WHERE id=%s', (transfer_id,)).fetchone()
        if transfer is None:
            raise HTTPException(404, 'Transfer not found')
        lock_wallets(db, transfer['sender_id'], transfer['recipient_id'])
        transfer = db.execute('SELECT * FROM transfers WHERE id=%s FOR UPDATE', (transfer_id,)).fetchone()
        if transfer['status'] == 'REVERSED':
            raise HTTPException(409, 'Transfer was already reversed')
        reference = 'REV-' + transfer_id
        ledger_change(db, transfer['recipient_id'], -transfer['amount_cents'], 'REVERSAL',
                      reference, transfer['sender_id'])
        ledger_change(db, transfer['sender_id'], transfer['amount_cents'], 'REVERSAL',
                      reference, transfer['recipient_id'])
        db.execute("UPDATE transfers SET status='REVERSED',reversed_at=now() WHERE id=%s", (transfer_id,))
    return {'id': transfer_id, 'status': 'REVERSED'}
