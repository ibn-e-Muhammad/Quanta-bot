import os
import time
import requests
import json

BASE_URL = "https://api.binance.com"

def run_diagnostics():
    print("--- API Health Check Diagnostics ---")
    
    # Check .env
    env_path = ".env"
    if os.path.exists(env_path):
        try:
            with open(env_path, "r") as f:
                lines = f.readlines()
            print(f"[OK] .env file found and readable. ({len(lines)} lines)")
        except Exception as e:
            print(f"[ERROR] Failed to read .env file: {e}")
    else:
        print("[WARN] .env file not found. Proceeding with public endpoints.")

    print("\nStarting tests...")

    # Test 1: Ping
    print("\n--- Test 1: Ping ---")
    try:
        start_time = time.time()
        response = requests.get(f"{BASE_URL}/api/v3/ping", timeout=5)
        response.raise_for_status()
        latency = (time.time() - start_time) * 1000
        print(f"[OK] Ping successful. Latency: {latency:.2f} ms")
    except Exception as e:
        print(f"[ERROR] Ping failed: {e}")

    # Test 2: Exchange Time
    print("\n--- Test 2: Exchange Time ---")
    try:
        local_time_ms = int(time.time() * 1000)
        start_request_time = time.time()
        response = requests.get(f"{BASE_URL}/api/v3/time", timeout=5)
        response.raise_for_status()
        data = response.json()
        server_time_ms = data.get("serverTime")
        if server_time_ms:
            # Note: The server time might be slightly behind the local time due to network latency in the request.
            delta_ms = server_time_ms - local_time_ms
            print(f"[OK] Fetched server time: {server_time_ms}")
            print(f"[INFO] Local time: {local_time_ms}")
            print(f"[INFO] Delta (Server - Local): {delta_ms} ms")
        else:
            print("[ERROR] serverTime not found in response.")
    except Exception as e:
        print(f"[ERROR] Fetching exchange time failed: {e}")

    # Test 3: Klines
    print("\n--- Test 3: Fetch 1 Recent Candle (BTCUSDT) ---")
    try:
        params = {
            "symbol": "BTCUSDT",
            "interval": "1m",
            "limit": 1
        }
        response = requests.get(f"{BASE_URL}/api/v3/klines", params=params, timeout=5)
        response.raise_for_status()
        data = response.json()
        
        # Extract X-MBX-USED-WEIGHT-1M header
        weight_used = response.headers.get('X-MBX-USED-WEIGHT-1M')
        
        print(f"[OK] Fetched candle data successfully.")
        print(f"[INFO] Candle Data: {json.dumps(data)}")
        if weight_used:
            print(f"[INFO] X-MBX-USED-WEIGHT-1M: {weight_used}")
        else:
            print("[WARN] X-MBX-USED-WEIGHT-1M header not found in the response.")
    except Exception as e:
        print(f"[ERROR] Fetching candle data failed: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"[ERROR] Response details: {e.response.text}")

    print("\n--- Diagnostics Complete ---")

if __name__ == "__main__":
    run_diagnostics()
