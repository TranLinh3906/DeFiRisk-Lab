
import os
import json
import urllib.request
from datetime import datetime, timezone

RPC_URL = os.environ.get("ALCHEMY_RPC_URL")

if not RPC_URL:
    raise SystemExit(
        'Chưa có ALCHEMY_RPC_URL. Hãy đặt biến môi trường trong PowerShell.'
    )


def rpc(method, params):
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params
    }).encode("utf-8")

    request = urllib.request.Request(
        RPC_URL,
        data=payload,
        headers={"Content-Type": "application/json"}
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))

    if "error" in data:
        raise RuntimeError(f"{method}: {data['error']}")

    return data["result"]


def get_block(number):
    return rpc("eth_getBlockByNumber", [hex(number), False])


def find_block_at_or_before(target_utc):
    """Tìm block cuối cùng có timestamp <= thời điểm mục tiêu."""
    target_ts = int(target_utc.timestamp())

    latest = rpc("eth_blockNumber", [])
    low, high = 0, int(latest, 16)
    answer = None

    while low <= high:
        mid = (low + high) // 2
        block = get_block(mid)

        if block is None:
            raise RuntimeError(f"Không tìm thấy block {mid}")

        block_ts = int(block["timestamp"], 16)

        if block_ts <= target_ts:
            answer = block
            low = mid + 1
        else:
            high = mid - 1

    if answer is None:
        raise RuntimeError("Không tìm được block phù hợp.")

    return answer


print("1. Kiểm tra mạng...")
chain_id = int(rpc("eth_chainId", []), 16)
print("Chain ID:", chain_id)

if chain_id != 1:
    raise SystemExit("Sai mạng: cần Ethereum Mainnet (Chain ID 1).")

targets = [
    datetime(2026, 10, 13, 0, 0, tzinfo=timezone.utc),
    datetime(2026, 10, 15, 0, 0, tzinfo=timezone.utc),
]

blocks = []

print("\n2. Tìm block theo UTC...")
for target in targets:
    block = find_block_at_or_before(target)
    number = int(block["number"], 16)
    timestamp = datetime.fromtimestamp(
        int(block["timestamp"], 16), tz=timezone.utc
    )

    print(f"\nMục tiêu UTC: {target.isoformat()}")
    print("Block number:", number)
    print("Block timestamp:", timestamp.isoformat())
    print("Block hash:", block["hash"])

    blocks.append(number)

print("\n3. Kiểm tra trạng thái lịch sử Aave Pool...")
aave_pool = "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2"

for number in blocks:
    code = rpc("eth_getCode", [aave_pool, hex(number)])
    if code and code != "0x":
        print(f"PASS: Có bytecode tại block {number}")
    else:
        print(f"FAIL: Không có bytecode tại block {number}")
