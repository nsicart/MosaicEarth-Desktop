"""Durable processing Checkout intents, with wallet and allowance reservations.

The exact request survives a lost Stripe response. Reservations are released only
after a verified unpaid expiration; ambiguous requests are never assumed unpaid.
"""
import copy
import time
import uuid

from . import stripe_payments
from .pricing import money


def project_row(store, pid):
    project = next((p for p in store.tables['dbo.WEB_PROJECTS'] if int(p['ID']) == int(pid)), None)
    if not project:
        raise stripe_payments.RetryPayment('Processing project is unavailable')
    return project


def prepare(store, uid, pid, form, metadata, cents, account, live):
    with store.stripe_transaction():
        project = project_row(store, pid)
        if int(project['CREATE_ID']) != int(uid):
            raise stripe_payments.InvalidPayment('Wrong processing owner')
        existing = project.get('PROCESSING_CHECKOUT_RESERVATION')
        if existing and existing['state'] not in {'EXPIRED', 'CONSUMED'}:
            if (existing['account'] != account or existing['live'] != live
                    or existing['metadata']['mosaicearth_task_kind'] != metadata['mosaicearth_task_kind']
                    or existing['metadata'].get('mosaicearth_upload_id') != metadata.get('mosaicearth_upload_id')):
                raise stripe_payments.InvalidPayment('Checkout configuration changed')
            return copy.deepcopy(existing)
        if project.get('PAYMENT_STATUS') in {'COVERED_BY_PLAN', 'AUTHORIZED_PAID_UPFRONT'}:
            raise stripe_payments.InvalidPayment('Task is already authorized')
        quote = copy.deepcopy(project.get('PENDING_PROCESSING_QUOTE'))
        if not quote:
            raise stripe_payments.RetryPayment('Request a processing quote first')
        user = next(u for u in store.tables['dbo.USERS'] if int(u['ID']) == int(uid))
        period = store.ensure_processing_quota_period(uid)
        summary = store.get_user_storage_summary(uid)
        covered = int(quote.get('covered_images') or 0) if metadata['mosaicearth_task_kind'] != 'gaussian' else 0
        if covered > max(0, summary['monthly_images_limit'] - summary['monthly_images_used']):
            raise stripe_payments.RetryPayment('Monthly allowance changed; request a fresh quote')
        wallet = max(money(0), money(quote['retail_price_eur']) - money(cents / 100))
        if wallet > money(user.get('CREATOR_EARNINGS_EUR') or 0) or cents < 50:
            raise stripe_payments.RetryPayment('Available wallet balance changed; request a fresh quote')
        if money(cents / 100) + wallet != money(quote['retail_price_eur']):
            raise stripe_payments.InvalidPayment('Checkout amount does not match the quote')
        key = 'mosaicearth-processing-' + uuid.uuid4().hex
        metadata = {**metadata, 'mosaicearth_checkout_key': key}
        form = {**form, 'metadata[mosaicearth_checkout_key]': key, 'expires_at': str(int(time.time()) + 3600)}
        reservation = dict(key=key, state='INTENT', created=time.time(), form=form, metadata=metadata,
                           cents=cents, user_id=uid, project_id=pid, account=account, live=live,
                           wallet=float(wallet), covered_images=covered, period=period, quote=quote)
        user['CREATOR_EARNINGS_EUR'] = float(money(user.get('CREATOR_EARNINGS_EUR') or 0) - wallet)
        user['MONTHLY_IMAGES_USED'] = int(user.get('MONTHLY_IMAGES_USED') or 0) + covered
        project['PROCESSING_CHECKOUT_RESERVATION'] = reservation
        if metadata.get('mosaicearth_capacity_hold'):
            store.runpod_engine_jobs().commit_capacity(metadata['mosaicearth_capacity_hold'])
        store.persist_table('dbo.USERS')
        store.persist_table('dbo.WEB_PROJECTS')
        return copy.deepcopy(reservation)


async def submit(store, client, secret, reservation):
    r = reservation
    if not r.get('session_id') and (r['state'] == 'REVIEW_REQUIRED' or time.time() - r['created'] >= 23 * 3600):
        with store.stripe_transaction():
            project_row(store, r['project_id'])['PROCESSING_CHECKOUT_RESERVATION']['state'] = 'REVIEW_REQUIRED'
            store.persist_table('dbo.WEB_PROJECTS')
        raise stripe_payments.RetryPayment('Checkout requires reconciliation')
    if r.get('session_id'):
        response = await client.get('https://api.stripe.com/v1/checkout/sessions/' + r['session_id'], auth=(secret, ''))
    else:
        response = await client.post('https://api.stripe.com/v1/checkout/sessions', data=r['form'],
                                    auth=(secret, ''), headers={'Idempotency-Key': r['key']})
    response.raise_for_status()
    data = response.json()
    stripe_payments.record_order(store, data, live=r['live'], user_id=r['user_id'], mode='payment',
                                 cents=r['cents'], metadata=r['metadata'], account=r['account'])
    with store.stripe_transaction():
        current = project_row(store, r['project_id'])['PROCESSING_CHECKOUT_RESERVATION']
        if current['key'] != r['key'] or current.get('session_id') not in (None, data['id']):
            raise stripe_payments.InvalidPayment('Processing Checkout identity changed')
        current['session_id'] = data['id']
        if current['state'] == 'INTENT':
            current['state'] = 'OPEN'
        store.persist_table('dbo.WEB_PROJECTS')
    if data.get('status') != 'open' or not data.get('url'):
        raise stripe_payments.RetryPayment('Checkout is settling or expired; refresh the task status')
    return data


def release(store, metadata):
    """Caller must have verified an expired, unpaid session with the owning account."""
    with store.stripe_transaction():
        project = project_row(store, metadata['mosaicearth_project_id'])
        r = project.get('PROCESSING_CHECKOUT_RESERVATION')
        if not r or r['key'] != metadata.get('mosaicearth_checkout_key') or r['state'] in {'EXPIRED', 'CONSUMED'}:
            return
        user = next(u for u in store.tables['dbo.USERS'] if int(u['ID']) == int(r['user_id']))
        user['CREATOR_EARNINGS_EUR'] = float(money(user.get('CREATOR_EARNINGS_EUR') or 0) + money(r['wallet']))
        if store.ensure_processing_quota_period(r['user_id']) == r['period']:
            user['MONTHLY_IMAGES_USED'] = max(0, int(user.get('MONTHLY_IMAGES_USED') or 0) - r['covered_images'])
        r['state'] = 'EXPIRED'
        project.pop('PENDING_PROCESSING_QUOTE', None)
        store.persist_table('dbo.USERS')
        store.persist_table('dbo.WEB_PROJECTS')


async def recover(store, client, secret, account):
    with store.stripe_transaction():
        pending = [copy.deepcopy(p['PROCESSING_CHECKOUT_RESERVATION']) for p in store.tables.get('dbo.WEB_PROJECTS', [])
                   if p.get('PROCESSING_CHECKOUT_RESERVATION', {}).get('state') == 'INTENT']
    for r in pending:
        if r['account'] != account or r['live'] != stripe_payments.live_mode(secret):
            continue
        try:
            await submit(store, client, secret, r)
        except Exception as exc:
            # A lost response can be retried with the frozen request, never a fresh key.
            print(f'[MosaicEarth payments] Checkout recovery pending: {type(exc).__name__}', flush=True)
