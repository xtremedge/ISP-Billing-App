
import requests
import sys
API = "http://127.0.0.1:8765/api"

def check_server():
    try:
        r = requests.get(f"{API}/settings", timeout=3)
        return r.status_code == 200
    except Exception as e:
        print(f"API server not available: {e}")
        return False

def test_add_customer():
    import time
    unique_username = f"testuser{int(time.time())}"
    data = {
        "username": unique_username,
        "full_name": "Test User",
        "mobile": "03001234567",
        "expiring": "2026-12-31",
        "package_id": "1",
        "area_id": "1",
        "status": "active",
        "notes": "Test notes"
    }
    r = requests.post(f"{API}/customers", json=data)
    if r.status_code != 200:
        print(f"Add customer failed: {r.status_code} {r.text}")
    assert r.status_code == 200
    assert "id" in r.json()

def test_get_customers():
    r = requests.get(f"{API}/customers")
    assert r.status_code == 200
    assert isinstance(r.json(), list)

def test_add_package():
    import time
    unique_pkg_name = f"Test Package {int(time.time())}"
    data = {
        "name": unique_pkg_name,
        "speed": "10 Mbps",
        "monthly_fee": 1000,
        "description": "Test package desc"
    }
    r = requests.post(f"{API}/packages", json=data)
    if r.status_code != 200:
        print(f"Add package failed: {r.status_code} {r.text}")
    assert r.status_code == 200
    assert "id" in r.json()

def test_add_area():
    import time
    unique_area = f"Test Area {int(time.time())}"
    data = {"name": unique_area}
    r = requests.post(f"{API}/areas", json=data)
    if r.status_code != 200:
        print(f"Add area failed: {r.status_code} {r.text}")
    assert r.status_code == 200
    assert "id" in r.json()

def test_add_bill():
    # You may need to adjust customer_id and package_fee for your data
    data = {
        "customer_id": "1",
        "month": "2026-12",
        "package_fee": 1000,
        "due_date": "2026-12-28"
    }
    r = requests.post(f"{API}/bills", json=data)
    if r.status_code != 200:
        print(f"Add bill failed: {r.status_code} {r.text}")
    assert r.status_code == 200
    assert "id" in r.json()

def test_settings():
    r = requests.get(f"{API}/settings")
    assert r.status_code == 200
    assert "isp_name" in r.json() or r.json() == {}

if __name__ == "__main__":
    if not check_server():
        print("API server is not running. Please start the SS Net backend and try again.")
        sys.exit(1)
    test_add_customer()
    test_get_customers()
    test_add_package()
    test_add_area()
    test_add_bill()
    test_settings()
    print("All API tests passed.")
