# Viewing PayLite's REAL PostgreSQL Database

After `docker compose up --build -d`, open **http://localhost:8081** in your web browser.

Use `PostgreSQL` / Server: `db` / User: `paylite` / Password: `paylite_local_demo_2026` / Database: `paylite`.

Adminer displays five actual relational tables created by `db/001_schema.sql`.

## Table relationships

```text
users 1 ───── 1 wallets
  │                │
  │                └──── 1 ───── many ledger_entries
  ├──── 1 ───── many sessions
  └──── 1 ───── many transfers (sender_id and recipient_id)
```

All money amounts are stored as integer **cents**, e.g. `150000` is Rs. 1,500.00. This ensures exact addition and subtraction.

## Queries you can paste in Adminer > SQL command

### 1. Show all users and wallet balances

```sql
SELECT u.id, u.name, u.mobile, u.role,
       w.balance_cents / 100.0 AS balance_rs
FROM users u
JOIN wallets w ON w.user_id = u.id
ORDER BY u.id;
```

### 2. Show recent P2P transfers

```sql
SELECT t.id, sender.name AS sender, receiver.name AS recipient,
       t.amount_cents / 100.0 AS amount_rs, t.status, t.created_at
FROM transfers t
JOIN users sender ON sender.id = t.sender_id
JOIN users receiver ON receiver.id = t.recipient_id
ORDER BY t.created_at DESC
LIMIT 20;
```

### 3. Show the append-only ledger

```sql
SELECT l.id, u.mobile, l.entry_type, l.delta_cents / 100.0 AS change_rs,
       l.balance_after_cents / 100.0 AS balance_after_rs,
       l.reference, l.created_at
FROM ledger_entries l
JOIN users u ON u.id = l.wallet_id
ORDER BY l.id DESC
LIMIT 50;
```

### 4. Prove wallets equal their ledger sums

```sql
SELECT u.mobile, w.balance_cents / 100.0 AS wallet_rs,
       COALESCE(SUM(l.delta_cents), 0) / 100.0 AS ledger_rs,
       w.balance_cents = COALESCE(SUM(l.delta_cents), 0) AS matches
FROM users u
JOIN wallets w ON w.user_id = u.id
LEFT JOIN ledger_entries l ON l.wallet_id = w.user_id
GROUP BY u.mobile, w.balance_cents
ORDER BY u.mobile;
```

### 5. Count transactions by type

```sql
SELECT entry_type, COUNT(*) AS total_entries,
       SUM(delta_cents) / 100.0 AS net_rs
FROM ledger_entries
GROUP BY entry_type
ORDER BY entry_type;
```

## Live demonstration sequence

1. Log into the wallet as Alice at `http://localhost:8000`.
2. Top up Rs. 500, then send Rs. 100 to Bob using PIN `1234`.
3. Open Adminer. Refresh the `wallets` table to see Alice's lower balance and Bob's higher balance.
4. Inspect `transfers`: the new transaction exists exactly once.
5. Inspect `ledger_entries`: one `SENT` and one `RECEIVED` entry for that transfer.
6. Log in as admin on the wallet and reverse that transfer.
7. In Adminer, see `status=REVERSED` and two new `REVERSAL` entries while the original two entries remain.
8. Run the wallet/ledger equality SQL query above. Every row should have `matches=true`.
9. Stop/restart with `docker compose down` followed by `docker compose up -d`. Records remain because of the Docker volume.

### Important

The `ledger_entries` table cannot be updated or deleted due to its PostgreSQL trigger. Read-only queries are safe for observing application behavior. Avoid editing balances or dropping tables manually because that will invalidate the accounting model. Don't expose Adminer to the internet.
