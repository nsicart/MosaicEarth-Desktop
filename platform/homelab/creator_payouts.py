"""Creator withdrawal ledger and Stripe Connect orchestration.

No bank details are collected here. Live operation requires an explicit operator
switch after Connect activation. Legacy requests stay reserved for reconciliation.
A transfer is never reported as a bank payout. Ambiguous old POSTs fail closed.
"""
import json
import os
import re
import time
from decimal import Decimal

import httpx
from .pricing import money
from . import stripe_payments

TABLE = 'dbo.MOSAICEARTH_CREATOR_PAYOUTS'
ACCOUNTS = 'dbo.MOSAICEARTH_CONNECT_ACCOUNTS'
PENDING = {'RESERVED', 'TRANSFER_INTENT', 'TRANSFERRED', 'PAYOUT_INTENT', 'PENDING', 'IN_TRANSIT',
           'FAILED', 'REVIEW_REQUIRED', 'REQUESTED_STRIPE_PAYOUT'}


def enabled():
    return os.environ.get('MOSAICEARTH_CONNECT_ENABLED') == '1' and os.environ.get('MOSAICEARTH_DESKTOP_LOCAL') != '1'


def account_row(store, uid):
    return next((a for a in store.tables.get(ACCOUNTS, []) if a['ID'] == uid), None)


def summary(store, uid):
    with store.stripe_transaction():
        user = next((u for u in store.tables.get('dbo.USERS', []) if int(u.get('ID') or 0) == uid), {})
        rows = [p for p in store.tables.get(TABLE, []) if int(p.get('USER_ID') or 0) == uid]
        account = account_row(store, uid) or {}
        processing_held = sum((money(r.get('wallet') or 0) for p in store.tables.get('dbo.WEB_PROJECTS', [])
                               for r in [p.get('PROCESSING_CHECKOUT_RESERVATION') or {}]
                               if int(p.get('CREATE_ID') or 0) == uid and r.get('state') in {'INTENT', 'OPEN', 'REVIEW_REQUIRED'}), Decimal(0))
        return dict(availableEur=float(money(user.get('CREATOR_EARNINGS_EUR') or 0)),
                    processingHeldEur=float(processing_held),
                    pendingEur=float(sum((money(p['AMOUNT_EUR']) for p in rows if p.get('STATUS') in PENDING), Decimal(0))),
                    enabled=enabled(), ready=bool(account.get('READY')), hasAccount=bool(account.get('ACCOUNT')), country=account.get('COUNTRY', ''),
                    payouts=[dict(id=p['ID'], amountEur=p['AMOUNT_EUR'], status=p.get('STATUS'),
                                  createdAt=p.get('CREATED_AT'), canCancel=p.get('STATUS') == 'RESERVED',
                                  canRetry=p.get('STATUS') == 'FAILED') for p in sorted(rows, key=lambda p: p['ID'], reverse=True)])


def reserve_payout(store, uid):
    if not enabled():
        return {'ok': False, 'code': 'connect_unavailable', 'message': 'Bank withdrawals are not yet enabled. Your earnings remain available in your wallet.'}
    with store.stripe_transaction():
        user = next((u for u in store.tables.get('dbo.USERS', []) if int(u.get('ID') or 0) == uid), None)
        if not user:
            return {'ok': False, 'message': 'User not found.'}
        account = account_row(store, uid) or {}
        if not account.get('READY'):
            return {'ok': False, 'message': 'Complete your Stripe payout account setup first.'}
        payouts = store.tables.setdefault(TABLE, [])
        if any(int(p.get('USER_ID') or 0) == uid and p.get('STATUS') in PENDING for p in payouts):
            return {'ok': False, 'message': 'A withdrawal is already pending. Check its status before requesting another.'}
        amount = money(user.get('CREATOR_EARNINGS_EUR') or 0)
        if amount < 10:
            return {'ok': False, 'message': 'Minimum bank withdrawal: 10.00 €. Smaller balances can be used inside MosaicEarth.'}
        pid = max([int(p['ID']) for p in payouts] + [0]) + 1
        payouts.append(dict(ID=pid, USER_ID=uid, AMOUNT_EUR=float(amount), STATUS='RESERVED',
                            CREATED_AT=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                            CONNECT_ACCOUNT=account['ACCOUNT'], PLATFORM_ACCOUNT=account['PLATFORM'],
                            LIVE=account['LIVE'], ATTEMPT=1))
        user['CREATOR_EARNINGS_EUR'] = float(money(user.get('CREATOR_EARNINGS_EUR') or 0) - amount)
        store.persist_table('dbo.USERS')
        store.persist_table(TABLE)
        return {'ok': True, 'payoutId': pid, 'amountEur': float(amount), 'status': 'RESERVED',
                'message': f'{amount:.2f} € reserved for bank withdrawal. It is not paid yet; track the status below.'}


def change_request(store, uid, pid, action):
    with store.stripe_transaction():
        row = next((p for p in store.tables.get(TABLE, []) if p['ID'] == pid and p['USER_ID'] == uid), None)
        if not row:
            return {'ok': False, 'message': 'Withdrawal not found.'}
        if action == 'cancel' and row['STATUS'] == 'RESERVED':
            user = next(u for u in store.tables['dbo.USERS'] if u['ID'] == uid)
            user['CREATOR_EARNINGS_EUR'] = float(money(user.get('CREATOR_EARNINGS_EUR') or 0) + money(row['AMOUNT_EUR']))
            row['STATUS'] = 'CANCELED'
            store.persist_table('dbo.USERS')
        elif action == 'retry' and row['STATUS'] == 'FAILED' and row.get('TRANSFER_ID'):
            row.update(STATUS='TRANSFERRED', ATTEMPT=int(row.get('ATTEMPT', 1)) + 1, PAYOUT_ID=None)
            row.pop('PAYOUT_INTENT_AT', None)
        else:
            return {'ok': False, 'message': 'This withdrawal cannot be changed in its current state.'}
        store.persist_table(TABLE)
        return {'ok': True}


class Connect:
    def __init__(self, store, client=None):
        self.store = store
        self.secret = store.get_setting('StripeSecretKey', '')
        self.live = stripe_payments.live_mode(self.secret)
        self.client = client or httpx.Client(timeout=20, trust_env=False, follow_redirects=False)

    def call(self, method, path, *, data=None, key=None, account=None):
        headers = {}
        if key:
            headers['Idempotency-Key'] = key
        if account:
            headers['Stripe-Account'] = account
        r = self.client.request(method, 'https://api.stripe.com/v1/' + path, auth=(self.secret, ''), headers=headers, data=data)
        r.raise_for_status()
        result = r.json()
        if not isinstance(result, dict):
            raise ValueError('Invalid provider response')
        return result

    def platform(self):
        account = self.call('GET', 'account')
        if not re.fullmatch(r'acct_[A-Za-z0-9]+', account.get('id', '')):
            raise ValueError('Invalid platform account')
        return account['id']

    def verify_account(self, account, uid):
        if (account.get('metadata', {}).get('mosaicearth_user_id') != str(uid)
                or account.get('type') != 'express' or account.get('settings', {}).get('payouts', {}).get('schedule', {}).get('interval') != 'manual'):
            raise ValueError('Payout account ownership or schedule mismatch')
        return bool(account.get('payouts_enabled') and account.get('capabilities', {}).get('transfers') == 'active')

    def onboarding(self, uid, country, base):
        if not enabled() or not re.fullmatch('[A-Z]{2}', country):
            raise ValueError('Connect is unavailable or country is invalid')
        platform = self.platform()
        with self.store.stripe_transaction():
            user = self.store.get_user_by_id(uid)
            if not user:
                raise ValueError('Unknown user')
            row = account_row(self.store, uid)
            if row and (row['PLATFORM'] != platform or row['LIVE'] != self.live or row['COUNTRY'] != country):
                raise ValueError('Payout account configuration mismatch')
            if not row:
                row = dict(ID=uid, PLATFORM=platform, LIVE=self.live, COUNTRY=country, INTENT_AT=time.time())
                self.store.tables.setdefault(ACCOUNTS, []).append(row)
                self.store.persist_table(ACCOUNTS)
            row = dict(row)
        if not row.get('ACCOUNT'):
            if time.time() - row['INTENT_AT'] > 23 * 3600:
                raise ValueError('Account creation requires operator reconciliation')
            account = self.call('POST', 'accounts', key=f'mosaicearth-connect-{platform}-{uid}', data={
                'type': 'express', 'country': country, 'email': user.get('EMAIL', ''),
                'capabilities[transfers][requested]': 'true',
                'settings[payouts][schedule][interval]': 'manual',
                'metadata[mosaicearth_user_id]': str(uid),
                'business_profile[product_description]': 'Geospatial data creator on MosaicEarth'})
            if not re.fullmatch(r'acct_[A-Za-z0-9]+', account.get('id', '')):
                raise ValueError('Invalid connected account')
            with self.store.stripe_transaction():
                account_row(self.store, uid)['ACCOUNT'] = account['id']
                self.store.persist_table(ACCOUNTS)
            row['ACCOUNT'] = account['id']
        account = self.call('GET', 'accounts/' + row['ACCOUNT'])
        ready = self.verify_account(account, uid)
        with self.store.stripe_transaction():
            account_row(self.store, uid)['READY'] = ready
            self.store.persist_table(ACCOUNTS)
        link = self.call('POST', 'account_links', data={'account': row['ACCOUNT'], 'type': 'account_onboarding',
            'refresh_url': base + '/en/CreditPacks?connect=refresh', 'return_url': base + '/en/CreditPacks?connect=return'})
        if not str(link.get('url', '')).startswith('https://connect.stripe.com/'):
            raise ValueError('Invalid onboarding URL')
        return link['url']

    def refresh(self, uid):
        with self.store.stripe_transaction():
            row = dict(account_row(self.store, uid) or {})
        if not row.get('ACCOUNT'):
            return
        if self.platform() != row['PLATFORM'] or self.live != row['LIVE']:
            raise ValueError('Payout platform mismatch')
        ready = self.verify_account(self.call('GET', 'accounts/' + row['ACCOUNT']), uid)
        with self.store.stripe_transaction():
            account_row(self.store, uid)['READY'] = ready
            self.store.persist_table(ACCOUNTS)

    def dashboard(self, uid):
        self.refresh(uid)
        with self.store.stripe_transaction():
            row = dict(account_row(self.store, uid) or {})
        if not row.get('ACCOUNT'):
            raise ValueError('Complete payout account setup first')
        link = self.call('POST', 'accounts/' + row['ACCOUNT'] + '/login_links')
        if not str(link.get('url', '')).startswith('https://connect.stripe.com/'):
            raise ValueError('Invalid Express dashboard URL')
        return link['url']

    def reconcile(self, pid):
        from .processing_architecture import cross_process_file_lock
        with cross_process_file_lock(self.store.data_root / 'locks' / f'creator-payout-{pid}.lock'):
            self._reconcile(pid)

    def _save(self, pid, **fields):
        with self.store.stripe_transaction():
            row = next(p for p in self.store.tables[TABLE] if p['ID'] == pid)
            row.update(fields)
            self.store.persist_table(TABLE)
            return dict(row)

    def _reconcile(self, pid):
        with self.store.stripe_transaction():
            row = dict(next(p for p in self.store.tables[TABLE] if p['ID'] == pid))
        if row['STATUS'] not in PENDING | {'PAID'} or row['STATUS'] in {'FAILED', 'REVIEW_REQUIRED', 'REQUESTED_STRIPE_PAYOUT'}:
            return
        if self.live != row['LIVE'] or self.platform() != row['PLATFORM_ACCOUNT']:
            raise ValueError('Payout platform mismatch')
        account = row['CONNECT_ACCOUNT']
        if not row.get('PAYOUT_ID') and not self.verify_account(self.call('GET', 'accounts/' + account), row['USER_ID']):
            return
        cents = int(money(row['AMOUNT_EUR']) * 100)
        # Atomically claim before making any POST. Cancellation can only win before this point.
        if row['STATUS'] == 'RESERVED':
            balance = self.call('GET', 'balance')
            if sum(int(x['amount']) for x in balance.get('available', []) if x.get('currency') == 'eur') < cents:
                return
            with self.store.stripe_transaction():
                current = next(p for p in self.store.tables[TABLE] if p['ID'] == pid)
                if current['STATUS'] != 'RESERVED':
                    return
                current.update(STATUS='TRANSFER_INTENT', TRANSFER_INTENT_AT=time.time())
                self.store.persist_table(TABLE)
                row = dict(current)
        if not row.get('TRANSFER_ID'):
            if time.time() - row['TRANSFER_INTENT_AT'] > 23 * 3600:
                self._save(pid, STATUS='REVIEW_REQUIRED')
                return
            try:
                transfer = self.call('POST', 'transfers', key=f'mosaicearth-creator-transfer-{pid}-{row.get("TRANSFER_ATTEMPT", 1)}', data={
                    'amount': str(cents), 'currency': 'eur', 'destination': account,
                    'transfer_group': f'mosaicearth-withdrawal-{pid}', 'metadata[mosaicearth_payout_id]': str(pid)})
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 400 and exc.response.json().get('error', {}).get('code') == 'balance_insufficient':
                    # Stripe has explicitly confirmed no transfer was created. A
                    # later attempt can use a fresh key after balance settlement.
                    self._save(pid, STATUS='RESERVED', TRANSFER_ATTEMPT=int(row.get('TRANSFER_ATTEMPT', 1)) + 1)
                    return
                raise
            if (not re.fullmatch(r'tr_[A-Za-z0-9]+', transfer.get('id', '')) or transfer.get('amount') != cents
                    or transfer.get('currency') != 'eur' or transfer.get('destination') != account
                    or transfer.get('livemode') != self.live):
                raise ValueError('Unverified transfer')
            row = self._save(pid, TRANSFER_ID=transfer['id'], STATUS='TRANSFERRED')
        if not row.get('PAYOUT_ID'):
            if row['STATUS'] == 'TRANSFERRED':
                balance = self.call('GET', 'balance', account=account)
                available = sum(int(x['amount']) for x in balance.get('available', []) if x.get('currency') == 'eur')
                if available < cents:
                    return
                row = self._save(pid, STATUS='PAYOUT_INTENT', PAYOUT_INTENT_AT=time.time())
            if time.time() - row['PAYOUT_INTENT_AT'] > 23 * 3600:
                self._save(pid, STATUS='REVIEW_REQUIRED')
                return
            payout = self.call('POST', 'payouts', key=f'mosaicearth-creator-bank-{pid}-{row["ATTEMPT"]}', account=account,
                               data={'amount': str(cents), 'currency': 'eur', 'metadata[mosaicearth_payout_id]': str(pid)})
        else:
            payout = self.call('GET', 'payouts/' + row['PAYOUT_ID'], account=account)
        if (not re.fullmatch(r'po_[A-Za-z0-9]+', payout.get('id', '')) or payout.get('amount') != cents
                or payout.get('currency') != 'eur' or payout.get('livemode') != self.live
                or payout.get('metadata', {}).get('mosaicearth_payout_id') != str(pid)):
            raise ValueError('Unverified bank payout')
        status = {'pending': 'PENDING', 'in_transit': 'IN_TRANSIT', 'paid': 'PAID', 'failed': 'FAILED', 'canceled': 'FAILED'}.get(payout.get('status'))
        if not status:
            raise ValueError('Unknown payout status')
        self._save(pid, PAYOUT_ID=payout['id'], STATUS=status, UPDATED_AT=time.time())


def reconcile_all(store):
    if not enabled():
        return
    with store.stripe_transaction():
        ids = [p['ID'] for p in store.tables.get(TABLE, []) if p.get('STATUS') in PENDING | {'PAID'}]
    with httpx.Client(timeout=20, trust_env=False, follow_redirects=False) as client:
        service = Connect(store, client)
        for pid in ids:
            try:
                service.reconcile(pid)
            except (httpx.HTTPError, ValueError, KeyError, stripe_payments.RetryPayment):
                print(f'[MosaicEarth payments] Creator withdrawal #{pid} awaits reconciliation', flush=True)
