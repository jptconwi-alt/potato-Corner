"""
PayMongo payment helper — GCash & Maya (PayMaya)
Docs: https://developers.paymongo.com/docs
"""
import os
import base64
import requests

PAYMONGO_SECRET_KEY = os.environ.get('PAYMONGO_SECRET_KEY', '')
PAYMONGO_BASE = 'https://api.paymongo.com/v1'

SOURCE_TYPES = {
    'GCash': 'gcash',
    'Maya':  'paymaya',
}


def _auth_header():
    encoded = base64.b64encode(f'{PAYMONGO_SECRET_KEY}:'.encode()).decode()
    return {
        'Authorization': f'Basic {encoded}',
        'Content-Type':  'application/json',
    }


def create_source(order_number: str, amount_php: float,
                  payment_method: str, success_url: str,
                  failed_url: str) -> dict:
    """
    Create a PayMongo Source for GCash or Maya.
    Returns the full source object on success, raises on error.

    amount_php  — total in Philippine Pesos (e.g. 179.0)
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
                'billing': {
                    'name': order_number,
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
    resp.raise_for_status()
    return resp.json()['data']


def get_source(source_id: str) -> dict:
    """Fetch a source by ID to check its status."""
    resp = requests.get(
        f'{PAYMONGO_BASE}/sources/{source_id}',
        headers=_auth_header(),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()['data']


def create_payment(source_id: str, amount_php: float,
                   order_number: str) -> dict:
    """
    Charge the source once it is in 'chargeable' state.
    Call this from the success redirect or webhook.
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
    resp.raise_for_status()
    return resp.json()['data']
