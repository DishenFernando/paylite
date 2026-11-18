"""Full end-to-end tests against the running Docker app and PostgreSQL database.
Run: docker compose --profile test run --build --rm tests
Tests create new demo accounts and transactions in the persistent demo database.
"""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx

BASE = os.getenv('TEST_BASE_URL', 'http://127.0.0.1:8000')


def api(method, path, **kwargs):
    with httpx.Client(base_url=BASE, timeout=20) as client:
        return client.request(method, path, **kwargs)


def make_user(pin='1234'):
    mobile = '07' + str(uuid.uuid4().int % 100_000_000).zfill(8)
    password = 'TestAccount123!'
    created = api('POST', '/api/auth/register', json={
        'name': 'Integration Test', 'mobile': mobile, 'password': password, 'pin': pin})
    assert created.status_code == 201, created.text
    login = api('POST', '/api/auth/login', json={'mobile': mobile, 'password': password})
    assert login.status_code == 200, login.text
    return mobile, {'Authorization': 'Bearer ' + login.json()['token']}


def balance(headers):
    r = api('GET', '/api/wallet', headers=headers)
    assert r.status_code == 200, r.text
    return r.json()['balance']


def topup(headers, amount):
    return api('POST', '/api/wallet/topup', headers=headers, json={'amount': amount})


def send(headers, mobile, amount, key=None, pin='1234'):
    return api('POST', '/api/transfers', headers={**headers, 'Idempotency-Key': key or uuid.uuid4().hex},
               json={'recipient_mobile': mobile, 'amount': amount, 'pin': pin})


def test_01_health_and_authentication():
    assert api('GET', '/api/health').json() == {'status': 'ok', 'database': 'postgresql'}
    assert api('GET', '/api/wallet').status_code == 401
    mobile, headers = make_user()
    assert balance(headers) == '0.00'
    assert api('POST', '/api/auth/register', json={
        'name': 'Duplicate User', 'mobile': mobile,
        'password': 'TestAccount123!', 'pin': '1234'}).status_code == 409
    assert topup(headers, '100.50').status_code == 201
    assert balance(headers) == '100.50'
    assert api('GET', '/api/wallet/integrity', headers=headers).json()['matches']
    assert api('POST', '/api/auth/logout', headers=headers).status_code == 200
    assert api('GET', '/api/wallet', headers=headers).status_code == 401


def test_02_transfer_retry_statement_and_reversal():
    _, alice = make_user()
    bob_mobile, bob = make_user()
    admin_login = api('POST', '/api/auth/login', json={
        'mobile': '0779999999', 'password': 'AdminPass123!'})
    assert admin_login.status_code == 200
    admin = {'Authorization': 'Bearer ' + admin_login.json()['token']}
    assert topup(alice, '200').status_code == 201
    key = uuid.uuid4().hex
    transfer = send(alice, bob_mobile, '50', key)
    assert transfer.status_code == 201, transfer.text
    transfer_id = transfer.json()['id']
    assert balance(alice) == '150.00' and balance(bob) == '50.00'
    replay = send(alice, bob_mobile, '50', key)
    assert replay.status_code == 200 and replay.json()['replayed']
    assert send(alice, bob_mobile, '51', key).status_code == 409
    assert api('GET', '/api/statement', headers=alice).json()[0]['type'] == 'SENT'
    assert 'SENT' in api('GET', '/api/statement/export', headers=alice).text
    assert api('GET', '/api/admin/transfers', headers=alice).status_code == 403
    reversed_resp = api('POST', f'/api/admin/transfers/{transfer_id}/reverse', headers=admin)
    assert reversed_resp.status_code == 200, reversed_resp.text
    assert api('POST', f'/api/admin/transfers/{transfer_id}/reverse', headers=admin).status_code == 409
    assert balance(alice) == '200.00' and balance(bob) == '0.00'
    for headers in (alice, bob):
        assert api('GET', '/api/wallet/integrity', headers=headers).json()['matches']


def test_03_twenty_concurrent_transfers_never_overdraw():
    _, alice = make_user()
    recipient, bob = make_user()
    assert topup(alice, '100').status_code == 201

    def attempt(i):
        return send(alice, recipient, '10', f'parallel-test-{uuid.uuid4().hex}-{i}')

    with ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(attempt, range(20)))
    assert sum(r.status_code == 201 for r in results) == 10, [(r.status_code, r.text) for r in results]
    assert sum(r.status_code == 400 for r in results) == 10
    assert balance(alice) == '0.00' and balance(bob) == '100.00'
    assert api('GET', '/api/wallet/integrity', headers=alice).json()['matches']
    assert api('GET', '/api/wallet/integrity', headers=bob).json()['matches']


def test_04_daily_limits_and_validation():
    _, alice = make_user()
    recipient, _ = make_user()
    assert topup(alice, '5000').status_code == 201
    assert topup(alice, '5000').status_code == 201
    assert topup(alice, '5000').status_code == 201
    assert topup(alice, '5000.01').status_code == 400
    assert send(alice, recipient, '9999').status_code == 201
    denied = send(alice, recipient, '2')
    assert denied.status_code == 400 and 'Remaining: 1.00' in denied.json()['detail']
    assert balance(alice) == '5001.00'


def test_05_pin_and_login_lockouts():
    mobile, alice = make_user()
    recipient, _ = make_user()
    for i in range(3):
        response = send(alice, recipient, '1', pin='0000')
    assert response.status_code == 429
    assert send(alice, recipient, '1').status_code == 429
    for i in range(3):
        response = api('POST', '/api/auth/login', json={'mobile': mobile, 'password': 'badpassword'})
    assert response.status_code == 429
    assert api('POST', '/api/auth/login', json={
        'mobile': mobile, 'password': 'TestAccount123!'}).status_code == 429


def test_06_insufficient_reversal_is_atomic():
    _, alice = make_user()
    bob_mobile, bob = make_user()
    charlie_mobile, charlie = make_user()
    assert topup(alice, '100').status_code == 201
    original = send(alice, bob_mobile, '100')
    assert original.status_code == 201
    assert send(bob, charlie_mobile, '70').status_code == 201
    admin_login = api('POST', '/api/auth/login', json={
        'mobile': '0779999999', 'password': 'AdminPass123!'})
    admin = {'Authorization': 'Bearer ' + admin_login.json()['token']}
    failed = api('POST', '/api/admin/transfers/' + original.json()['id'] + '/reverse', headers=admin)
    assert failed.status_code == 400, failed.text
    assert balance(alice) == '0.00'
    assert balance(bob) == '30.00'
    assert balance(charlie) == '70.00'
    for headers in (alice, bob, charlie):
        assert api('GET', '/api/wallet/integrity', headers=headers).json()['matches']
