"""Stripe Checkout orders and transactional entitlement settlement."""
import hashlib
import hmac
import json
import re
import sqlite3
import time
import httpx


SESSION_ID = re.compile(r"cs_(live|test)_[a-zA-Z0-9]+")
EVENT_ID = re.compile(r"evt_[a-zA-Z0-9]+")


class InvalidPayment(Exception):
    pass


class RetryPayment(Exception):
    pass


async def cancel_plan_subscription(store, uid, subscription):
    if not re.fullmatch(r"sub_[A-Za-z0-9]+", subscription):
        raise InvalidPayment("Invalid subscription")
    with store.stripe_transaction() as conn:
        order = conn.execute(
            "SELECT metadata, stripe_account, live FROM stripe_checkout_orders WHERE user_id=? AND subscription=? AND result IS NOT NULL",
            (uid, subscription),
        ).fetchone()
    if not order or json.loads(order[0]).get("mosaicearth_kind") != "subscription_plan":
        raise InvalidPayment("Subscription ownership is unverified")
    secret = store.get_setting("StripeSecretKey", "")
    if live_mode(secret) != bool(order[2]):
        raise InvalidPayment("Subscription account mode mismatch")
    async with httpx.AsyncClient(timeout=20) as client:
        if await stripe_account(client, secret) != order[1]:
            raise InvalidPayment("Subscription account mismatch")
        url = "https://api.stripe.com/v1/subscriptions/" + subscription
        response = await client.get(url, auth=(secret, ""))
        response.raise_for_status()
        sub = response.json()
        if sub.get("status") != "canceled":
            response = await client.delete(url, auth=(secret, ""))
            response.raise_for_status()
            sub = response.json()
        if sub.get("id") != subscription or sub.get("status") != "canceled" or sub.get("livemode") != bool(order[2]):
            raise RetryPayment("Subscription cancellation is unconfirmed")
    with store.stripe_transaction() as conn:
        user = store.get_user_by_id(uid) or {}
        if user.get("STRIPE_PLAN_SUBSCRIPTION") == subscription:
            store.apply_stripe_cancellation(uid, subscription, json.loads(order[0]))
        conn.execute("INSERT OR IGNORE INTO stripe_lifecycle_effects(event_type, object_id) VALUES (?,?)",
                     ("customer.subscription.deleted", subscription))


async def reconcile_processing_refunds(store):
    """Refund only recorded task authorizations; provider retries share one idempotency key."""
    with store.stripe_transaction():
        pending = [dict(p) for p in store.tables.get("dbo.WEB_PROJECTS", [])
                   if p.get("PAYMENT_STATUS") == "REFUND_PENDING_REVIEW"
                   and "UPFRONT_STRIPE_CENTS" in p and "UPFRONT_WALLET_EUR" in p]
    for project in pending:
        try:
            await _reconcile_processing_refund(store, project)
        except (httpx.HTTPError, InvalidPayment, RetryPayment, ValueError, KeyError):
            print(f"[MosaicEarth payments] Refund for task #{project['ID']} awaits provider reconciliation", flush=True)


async def _reconcile_processing_refund(store, project):
    pid, uid = int(project["ID"]), int(project["CREATE_ID"])
    sid = project.get("AUTHORIZED_STRIPE_SESSION") or ""
    cents = int(project["UPFRONT_STRIPE_CENTS"])
    wallet = float(project["UPFRONT_WALLET_EUR"])
    refund_id = ""
    if cents:
        order = read_order(store, sid)
        if not order or not order["result"] or order["cents"] != cents or order["user_id"] != uid or not order["payment_intent"]:
            return
        meta = json.loads(order["metadata"])
        if meta.get("mosaicearth_kind") != "processing_task" or meta.get("mosaicearth_project_id") != str(pid):
            return
        secret = store.get_setting("StripeSecretKey", "")
        if live_mode(secret) != bool(order["live"]):
            return
        async with httpx.AsyncClient(timeout=15) as client:
            if await stripe_account(client, secret) != order["stripe_account"]:
                return
            existing_refund = project.get("STRIPE_REFUND_ID")
            if existing_refund and re.fullmatch(r"re_[A-Za-z0-9]+", existing_refund):
                response = await client.get("https://api.stripe.com/v1/refunds/" + existing_refund, auth=(secret, ""))
            else:
                response = await client.post("https://api.stripe.com/v1/refunds", auth=(secret, ""),
                    headers={"Idempotency-Key": "mosaicearth-task-failure-" + sid},
                    data={"payment_intent":order["payment_intent"], "amount":str(cents),
                          "metadata[mosaicearth_project_id]":str(pid)})
            response.raise_for_status()
            refund = response.json()
            if (not re.fullmatch(r"re_[A-Za-z0-9]+", str(refund.get("id") or "")) or refund.get("amount") != cents
                    or refund.get("currency") != "eur" or refund.get("payment_intent") != order["payment_intent"]):
                return
            refund_id = refund["id"]
            if refund.get("status") != "succeeded":
                with store.stripe_transaction():
                    current = next((p for p in store.tables["dbo.WEB_PROJECTS"] if int(p.get("ID") or 0) == pid), None)
                    if current and current.get("AUTHORIZED_STRIPE_SESSION") == sid and current.get("PAYMENT_STATUS") == "REFUND_PENDING_REVIEW":
                        current["STRIPE_REFUND_ID"] = refund_id
                        store.persist_table("dbo.WEB_PROJECTS")
                return
    elif sid or wallet <= 0:
        return
    with store.stripe_transaction():
        current = next((p for p in store.tables["dbo.WEB_PROJECTS"] if int(p.get("ID") or 0) == pid), None)
        if (not current or current.get("PAYMENT_STATUS") != "REFUND_PENDING_REVIEW"
                or (current.get("AUTHORIZED_STRIPE_SESSION") or "") != sid):
            return
        account = next((u for u in store.tables["dbo.USERS"] if int(u.get("ID") or 0) == uid), None)
        if not account:
            return
        account["CREATOR_EARNINGS_EUR"] = round(float(account.get("CREATOR_EARNINGS_EUR") or 0) + wallet, 2)
        current.update(PAYMENT_STATUS="REFUNDED_ON_FAILURE", REFUNDED_EUR=round(cents/100+wallet,2),
                       REFUND_PENDING_EUR=0, STRIPE_REFUND_ID=refund_id)
        for payment in store.tables.get("dbo.MOSAICEARTH_TASK_PAYMENTS", []):
            if int(payment.get("PROJECT_ID") or 0) == pid and payment.get("STATUS") == "REFUND_PENDING_REVIEW":
                payment.update(STATUS="REFUNDED_ON_FAILURE", STRIPE_REFUND_ID=refund_id)
        for table in ("dbo.USERS", "dbo.WEB_PROJECTS", "dbo.MOSAICEARTH_TASK_PAYMENTS"):
            store.persist_table(table)

def live_mode(secret):
    for prefix in ("sk_", "rk_"):
        if secret.startswith(prefix + "live_"):
            return True
        if secret.startswith(prefix + "test_"):
            return False
    raise RetryPayment("Stripe account mode is unavailable")


def valid_signature(secret, raw, header, now=None):
    if not secret:
        return False
    parts = [item.strip().partition("=") for item in header.split(",")]
    timestamps = [value for key, _, value in parts if key == "t"]
    if len(timestamps) != 1 or not re.fullmatch(r"[0-9]{1,12}", timestamps[0]):
        return False
    timestamp = timestamps[0]
    if abs((time.time() if now is None else now) - int(timestamp)) > 300:
        return False
    expected = hmac.new(secret.encode(), timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(value, expected) for key, _, value in parts
               if key == "v1" and re.fullmatch(r"[0-9a-f]{64}", value))


def init_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS stripe_checkout_orders (
        session_id TEXT PRIMARY KEY, live INTEGER NOT NULL, user_id INTEGER NOT NULL,
        mode TEXT NOT NULL, cents INTEGER NOT NULL, metadata TEXT NOT NULL,
        stripe_account TEXT NOT NULL, result TEXT, payment_intent TEXT UNIQUE,
        subscription TEXT UNIQUE
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS stripe_webhook_events (
        event_id TEXT PRIMARY KEY, session_id TEXT, event_type TEXT NOT NULL
    )""")

    conn.execute("""CREATE TABLE IF NOT EXISTS stripe_processing_outbox (
        session_id TEXT PRIMARY KEY, project_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        target TEXT NOT NULL, dispatched INTEGER NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS stripe_lifecycle_inbox (
        event_id TEXT PRIMARY KEY, object_id TEXT NOT NULL, related_id TEXT NOT NULL,
        event_type TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'REQUIRES_REVIEW'
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS stripe_lifecycle_effects (
        event_type TEXT NOT NULL, object_id TEXT NOT NULL, PRIMARY KEY(event_type, object_id)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS stripe_credit_pack_grants (
        session_id TEXT PRIMARY KEY, credits INTEGER NOT NULL,
        history_id INTEGER NOT NULL, balance_after INTEGER NOT NULL
    )""")


def _credit_history(store, uid):
    return [row for row in store.tables.get("dbo.HISTORICO_USUARIOS", [])
            if int(row.get("USER_ID") or 0) == uid]


def _refund_unused_pack(store, conn, order, obj, verified_refund):
    sid, uid, cents, metadata, account, live = order
    if (not verified_refund or verified_refund != (account, bool(live))
            or json.loads(metadata).get("mosaicearth_kind") != "credit_pack"
            or obj.get("currency") != "eur" or obj.get("amount_captured") != cents
            or obj.get("amount_refunded") != cents or obj.get("refunded") is not True):
        return False
    grant = conn.execute(
        "SELECT credits, history_id, balance_after FROM stripe_credit_pack_grants WHERE session_id = ?",
        (sid,),
    ).fetchone()
    if not grant:
        return False
    credits, history_id, balance_after = grant
    user = store.get_user_by_id(uid)
    history = _credit_history(store, uid)
    if (not user or user.get("CREDITS") != balance_after
            or max((int(row["ID"]) for row in history), default=0) != history_id):
        return False
    success, _ = store.adjust_user_credits(uid, -credits, "Stripe refund of unused credit pack")
    return success


def record_lifecycle_event(store, event_id, kind, obj, *, verified_refund=None):
    expected_object = {"invoice.paid": "invoice", "customer.subscription.deleted": "subscription",
                       "charge.refunded": "charge"}[kind]
    object_id = obj.get("id")
    if obj.get("object") != expected_object or not isinstance(object_id, str) or not object_id:
        raise InvalidPayment("Invalid Stripe lifecycle event")
    related = (obj.get("subscription") or obj.get("payment_intent") or
               ((obj.get("parent") or {}).get("subscription_details") or {}).get("subscription") or "")
    if kind == "customer.subscription.deleted":
        related = object_id
    with store.stripe_transaction() as conn:
        existing = conn.execute(
            "SELECT object_id, event_type, status FROM stripe_lifecycle_inbox WHERE event_id = ?", (event_id,),
        ).fetchone()
        if existing and existing[:2] != (object_id, kind):
            raise InvalidPayment("Stripe lifecycle event already belongs to a different operation")
        if existing and existing[2] != "REQUIRES_REVIEW":
            return
        status = "REQUIRES_REVIEW"
        order = conn.execute(
            """SELECT user_id, cents, metadata FROM stripe_checkout_orders
               WHERE subscription = ? AND result IS NOT NULL""", (related,),
        ).fetchone()
        applied = conn.execute("SELECT 1 FROM stripe_lifecycle_effects WHERE event_type = ? AND object_id = ?",
                               (kind, object_id)).fetchone()
        if applied:
            status = "ALREADY_APPLIED"
        elif kind == "charge.refunded":
            refunded_order = conn.execute(
                """SELECT session_id, user_id, cents, metadata, stripe_account, live
                   FROM stripe_checkout_orders
                   WHERE payment_intent = ? AND mode = 'payment' AND result IS NOT NULL""",
                (related,),
            ).fetchone()
            if refunded_order and _refund_unused_pack(store, conn, refunded_order, obj, verified_refund):
                status = "APPLIED"
        elif order and kind != "charge.refunded":
            uid, cents, metadata = order
            meta = json.loads(metadata)
            product = meta["mosaicearth_kind"]
            if kind == "invoice.paid" and obj.get("billing_reason") == "subscription_create":
                status = "INITIAL_CHECKOUT"
            elif (kind == "invoice.paid" and obj.get("billing_reason") == "subscription_cycle"
                  and obj.get("status") == "paid" and obj.get("currency") == "eur"
                  and obj.get("amount_paid") == cents and obj.get("paid_out_of_band") is not True
                  and not conn.execute(
                      """SELECT 1 FROM stripe_lifecycle_effects
                         WHERE event_type = 'customer.subscription.deleted' AND object_id = ?""",
                      (related,),
                  ).fetchone()):
                if product == "credit_pack":
                    success, _ = store.adjust_user_credits(uid, int(meta["mosaicearth_credits"]), "Stripe credit pack renewal")
                    if not success:
                        raise RetryPayment("Renewal could not be applied")
                status = "APPLIED"
            elif kind == "customer.subscription.deleted" and obj.get("status") == "canceled":
                status = "APPLIED" if store.apply_stripe_cancellation(uid, object_id, meta) else "REQUIRES_REVIEW"
        if status == "APPLIED":
            conn.execute("INSERT INTO stripe_lifecycle_effects VALUES (?, ?)", (kind, object_id))
        conn.execute(
            """INSERT INTO stripe_lifecycle_inbox VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(event_id) DO UPDATE SET status=excluded.status""",
            (event_id, object_id, str(related), kind, status),
        )


async def verify_full_refund(client, secret, obj):
    charge_id = obj.get("id") or ""
    if not re.fullmatch(r"ch_[a-zA-Z0-9]+", charge_id):
        raise InvalidPayment("Invalid Stripe charge")
    auth = (secret, "")
    account = await stripe_account(client, secret)
    response = await client.get(f"https://api.stripe.com/v1/charges/{charge_id}", auth=auth)
    response.raise_for_status()
    charge = response.json()
    live = live_mode(secret)
    if (charge.get("id") != charge_id or charge.get("livemode") is not live
            or charge.get("payment_intent") != obj.get("payment_intent")):
        raise InvalidPayment("Stripe refund does not match the charge")
    if charge.get("refunded") is not True or charge.get("disputed") is not False:
        return charge, None
    response = await client.get("https://api.stripe.com/v1/refunds", auth=auth,
                                params={"charge": charge_id, "limit": 100})
    response.raise_for_status()
    refunds = response.json()
    succeeded = sum(item["amount"] for item in refunds["data"]
                    if item.get("status") == "succeeded" and item.get("currency") == charge.get("currency")
                    and item.get("charge") == charge_id)
    if refunds.get("has_more") or succeeded != charge.get("amount_captured"):
        raise RetryPayment("Stripe has not confirmed the full refund")
    return charge, (account, live)


def verify_settlement(session, order):
    if session.get("status") != "complete" or session.get("payment_status") != "paid" or order["result"]:
        return
    if order["mode"] == "payment":
        intent = session.get("payment_intent") or {}
        charge = intent.get("latest_charge") or {}
        if (intent.get("status") != "succeeded" or intent.get("currency") != "eur"
                or intent.get("amount_received") != order["cents"] or charge.get("paid") is not True
                or charge.get("captured") is not True or charge.get("amount_refunded") != 0
                or charge.get("disputed") is not False):
            raise RetryPayment("Stripe payment intent requires review")
    else:
        subscription = session.get("subscription") or {}
        if subscription.get("status") != "active":
            raise RetryPayment("Stripe subscription requires review")


async def stripe_account(client, secret):
    response = await client.get("https://api.stripe.com/v1/account", auth=(secret, ""))
    if response.status_code != 200:
        raise RetryPayment("Stripe account verification is unavailable")
    account = response.json().get("id", "")
    if not re.fullmatch(r"acct_[a-zA-Z0-9]+", account):
        raise RetryPayment("Stripe account verification is unavailable")
    return account


def record_order(store, session, *, live, user_id, mode, cents, metadata, account):
    sid = session.get("id", "")
    match = SESSION_ID.fullmatch(sid)
    if (not match or (match[1] == "live") != live or session.get("livemode") is not live
            or session.get("mode") != mode or session.get("currency") != "eur"
            or type(session.get("amount_total")) is not int or session["amount_total"] != cents
            or session.get("metadata") != metadata or metadata.get("mosaicearth_user_id") != str(user_id)):
        raise InvalidPayment("Stripe Checkout does not match the order")
    with sqlite3.connect(store.sqlite_path, timeout=30) as conn:
        conn.execute(
            """INSERT OR IGNORE INTO stripe_checkout_orders
               (session_id, live, user_id, mode, cents, metadata, stripe_account)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (sid, live, user_id, mode, cents, json.dumps(metadata, sort_keys=True), account),
        )
        existing = conn.execute("SELECT live,user_id,mode,cents,metadata,stripe_account FROM stripe_checkout_orders WHERE session_id=?", (sid,)).fetchone()
        if existing != (int(live), user_id, mode, cents, json.dumps(metadata, sort_keys=True), account):
            raise InvalidPayment("An existing Checkout order has different terms")


def read_order(store, session_id):
    with sqlite3.connect(store.sqlite_path, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM stripe_checkout_orders WHERE session_id = ?", (session_id,)).fetchone()


def settle(store, order, session, event_id, event_type, grant):
    sid = order["session_id"]
    if (session.get("id") != sid or session.get("object") != "checkout.session"
            or session.get("livemode") is not bool(order["live"]) or session.get("mode") != order["mode"]
            or session.get("currency") != "eur" or type(session.get("amount_total")) is not int
            or session["amount_total"] != order["cents"] or session.get("metadata") != json.loads(order["metadata"])):
        raise InvalidPayment("Stripe Checkout does not match the order")
    if session.get("status") != "complete" or session.get("payment_status") != "paid":
        return None
    intent = (session.get("payment_intent") or {}).get("id")
    subscription = (session.get("subscription") or {}).get("id")
    if order["mode"] == "payment" and not (isinstance(intent, str) and re.fullmatch(r"pi_[a-zA-Z0-9]+", intent)):
        raise InvalidPayment("Stripe payment intent is unavailable")
    if order["mode"] == "subscription" and not (isinstance(subscription, str) and re.fullmatch(r"sub_[a-zA-Z0-9]+", subscription)):
        raise InvalidPayment("Stripe subscription is unavailable")
    with store.stripe_transaction() as conn:
        if event_id:
            event = conn.execute("SELECT session_id, event_type FROM stripe_webhook_events WHERE event_id = ?", (event_id,)).fetchone()
            if event and event != (sid, event_type):
                raise InvalidPayment("Stripe event already belongs to a different operation")
        row = conn.execute("SELECT result FROM stripe_checkout_orders WHERE session_id = ?", (sid,)).fetchone()
        if row[0]:
            result = json.loads(row[0])
        else:
            blocked = conn.execute(
                """SELECT 1 FROM stripe_lifecycle_inbox WHERE related_id IN (?, ?)
                   AND event_type IN ('charge.refunded', 'customer.subscription.deleted')""",
                (intent, subscription),
            ).fetchone()
            if blocked:
                raise RetryPayment("A Stripe reversal requires reconciliation")
            metadata = json.loads(order["metadata"])
            result = grant(conn, order["user_id"], metadata, sid)
            if order["mode"] == "payment" and metadata.get("mosaicearth_kind") == "credit_pack":
                uid = order["user_id"]
                history = _credit_history(store, uid)
                latest = max(history, key=lambda row: int(row["ID"]))
                conn.execute(
                    "INSERT INTO stripe_credit_pack_grants VALUES (?, ?, ?, ?)",
                    (sid, int(metadata["mosaicearth_credits"]), int(latest["ID"]), store.get_user_by_id(uid)["CREDITS"]),
                )
            if subscription:
                store.bind_stripe_subscription(order["user_id"], subscription, json.loads(order["metadata"]))
            conn.execute("UPDATE stripe_checkout_orders SET result = ?, payment_intent = ?, subscription = ? WHERE session_id = ?",
                         (json.dumps(result), intent, subscription, sid))
        if event_id:
            conn.execute("INSERT OR IGNORE INTO stripe_webhook_events VALUES (?, ?, ?)", (event_id, sid, event_type))
        return result


def release_processing_capacity(store, metadata):
    from .processing_checkout import release
    release(store, metadata)
    hold = metadata.get('mosaicearth_capacity_hold')
    if not hold:
        return
    with store.stripe_transaction():
        project = next((p for p in store.tables.get('dbo.WEB_PROJECTS', [])
                        if str(p.get('ID')) == metadata.get('mosaicearth_project_id')), None)
        if project and project.get('RUNPOD_CAPACITY_HOLD') == hold and project.get('PAYMENT_STATUS') not in {'COVERED_BY_PLAN', 'AUTHORIZED_PAID_UPFRONT'}:
            store.runpod_engine_jobs().release_capacity(hold)
            project.pop('RUNPOD_CAPACITY_HOLD', None)
            project.pop('PENDING_PROCESSING_QUOTE', None)
            store.persist_table('dbo.WEB_PROJECTS')


async def reconcile_processing_checkouts(store):
    """Release abandoned capacity only after Stripe confirms an unpaid expiration."""
    secret = store.get_setting('StripeSecretKey', '')
    if not secret:
        return
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        account = await stripe_account(client, secret)
        from .processing_checkout import recover
        await recover(store, client, secret, account)
    with store.stripe_transaction() as conn:
        orders = [dict(zip([c[0] for c in cur.description], row)) for cur in [conn.execute(
            "SELECT * FROM stripe_checkout_orders WHERE result IS NULL AND json_extract(metadata, '$.mosaicearth_kind')='processing_task' ORDER BY rowid LIMIT 100")]
            for row in cur.fetchall()]
    pending = orders
    if not pending:
        return
    secret = store.get_setting('StripeSecretKey', '')
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        account = await stripe_account(client, secret)
        for order in pending:
            if bool(order['live']) != live_mode(secret) or order['stripe_account'] != account:
                continue
            response = await client.get('https://api.stripe.com/v1/checkout/sessions/' + order['session_id'], auth=(secret, ''))
            response.raise_for_status()
            session = response.json()
            if session.get('id') != order['session_id'] or session.get('livemode') != bool(order['live']):
                continue
            if session.get('status') == 'expired' and session.get('payment_status') == 'unpaid':
                release_processing_capacity(store, json.loads(order['metadata']))
                with store.stripe_transaction() as conn:
                    conn.execute("UPDATE stripe_checkout_orders SET result=? WHERE session_id=? AND result IS NULL",
                                 (json.dumps({'ok': False, 'expired': True}), order['session_id']))
            elif session.get('status') == 'complete' and session.get('payment_status') == 'paid':
                from .app import _fulfill_stripe_checkout_session
                await _fulfill_stripe_checkout_session(order['session_id'])
