"""Bounded read-only connectivity probe. Never print credentials or URLs."""
import json
import os
from pathlib import Path

from dotenv import dotenv_values
import requests


def main():
    values = {}
    for path in [Path('N3_Compound_Q3_Overlap_VSCode/.env'), Path('.env')]:
        if path.exists():
            values.update({k: v for k, v in dotenv_values(path).items() if v})
    url = next((os.environ.get(k) or values.get(k) for k in ['ETHEREUM_ARCHIVE_RPC_URL', 'ALCHEMY_RPC_URL', 'ETH_RPC_URL'] if os.environ.get(k) or values.get(k)), None)
    report = {'rpc_configured': bool(url)}
    if url:
        payload = [{'jsonrpc': '2.0', 'id': 1, 'method': 'eth_chainId', 'params': []},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'eth_getCode', 'params': ['0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2', hex(20458637)]}]
        try:
            response = requests.post(url, json=payload, timeout=15)
            report['http_status'] = response.status_code
            if response.status_code == 200:
                body = response.json()
                body = body if isinstance(body, list) else [body]
                report['responses'] = [{'id': r.get('id'), 'error_code': r.get('error', {}).get('code'),
                    'chain_id': int(r['result'], 16) if r.get('id') == 1 and 'result' in r else None,
                    'archive_code_available': len(r.get('result', '')) > 2 if r.get('id') == 2 else None} for r in body]
        except Exception as exc:
            report['failure_type'] = type(exc).__name__
    out = Path('outputs/n5_completion_20261026')
    out.mkdir(exist_ok=True)
    (out/'network_probe.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
