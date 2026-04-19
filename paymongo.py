"""
PayMongo payment helper — GCash & Maya (PayMaya)
Docs: https://developers.paymongo.com/docs/source-payment-workflow
"""
import os
import base64
import requests

PAYMONGO_SECRET_KEY = os.environ.get('PAYMONGO_SECRET_KEY', '')
PAYMONGO_BASE = 'https://api.paymongo.com/v1'

# PayMongo source type strings
SOURCE_TYPES = {
    'GCash': 'gcash',
    'Maya':  'paymaya',   # PayMongo still uses 'paymaya' as the type string
}


def _auth_header():
    encoded = base64.b64encode(f'{PAYMONGO_SECRET_KEY}:'.encode()).decode()
    return {
        'Authorization': f'Basic {encoded}',
        'Content-Type':  'application/json',
    }


def _raise_with_detail(resp):
    """Raise an exception with the actual PayMongo error message."""
    try:
        errors = resp.json().get('errors', [])
        detail = '; '.join(e.get('detail', '') for e in errors)
        raise ValueError(f'PayMongo {resp.status_code}: {detail}')
    except (ValueError, KeyError):
        resp.raise_for_status()


def create_source(order_number: str, amount_php: float,
                  payment_method: str, success_url: str,
                  failed_url: str) -> dict:
    """
    Create a PayMongo Source for GCash or Maya.
    Returns the full source data dict on success, raises ValueError on error.

    Required fields per PayMongo Sources API:
      - amount   (integer, in centavos)
      - currency (must be 'PHP')
      - type     ('gcash' or 'paymaya')
      - redirect.success
      - redirect.failed

    NOTE: Do NOT include 'billing' — not a valid field for /v1/sources,
    causes 400 Bad Request.
    """
    source_type = SOURCE_TYPES.get(payment_method)
    if not source_type:
        raise ValueError(f'Unsupported payment method: {payment_method}')

    amount_centavos = int(round(amount_php * 100))

    payload = {
        'data': {
            'attributes': {
                'amount':   amount_centavos,
                'currency': 'PHP',
                'type':     source_type,
                'redirect': {
                    'success': success_url,
                    'failed':  failed_url,
                },
            }
        }
    }

    resp = requests.post(
        f'{PAYMONGO_BASE}/sources',
        json=payload,
        headers=_auth_header(),
        timeout=15,
    )

    if not resp.ok:
        _raise_with_detail(resp)

    return resp.json()['data']


def get_source(source_id: str) -> dict:
    """Fetch a source by ID to check its status."""
    resp = requests.get(
        f'{PAYMONGO_BASE}/sources/{source_id}',
        headers=_auth_header(),
        timeout=15,
    )
    if not resp.ok:
        _raise_with_detail(resp)
    return resp.json()['data']


def create_payment(source_id: str, amount_php: float,
                   order_number: str) -> dict:
    """
    Charge the source once it is in 'chargeable' state.
    """
    amount_centavos = int(round(amount_php * 100))

    payload = {
        'data': {
            'attributes': {
                'amount':      amount_centavos,
                'currency':    'PHP',
                'description': f'Potato Corner Order #{order_number}',
                'source': {
                    'id':   source_id,
                    'type': 'source',
                },
            }
        }
    }

    resp = requests.post(
        f'{PAYMONGO_BASE}/payments',
        json=payload,
        headers=_auth_header(),
        timeout=15,
    )

    if not resp.ok:
        _raise_with_detail(resp)

    return resp.json()['data']