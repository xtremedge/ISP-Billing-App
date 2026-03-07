
import os
import requests
import sys
API = "http://127.0.0.1:8765/api"
ADMIN_USERNAME = os.getenv("NETPULSE_TEST_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("NETPULSE_TEST_ADMIN_PASS", "Admin1234")
ADMIN_RECOVERY = os.getenv("NETPULSE_TEST_ADMIN_RECOVERY", "netpulse-recovery")
_AUTH_HEADERS = None


def _ensure_auth_headers():
    global _AUTH_HEADERS
    if _AUTH_HEADERS:
        return _AUTH_HEADERS

    status_r = requests.get(f"{API}/auth/status")
    assert status_r.status_code == 200, status_r.text
    status = status_r.json()
    if status.get("setup_required"):
        signup_r = requests.post(
            f"{API}/auth/signup",
            json={
                "username": ADMIN_USERNAME,
                "password": ADMIN_PASSWORD,
                "recovery_key": ADMIN_RECOVERY,
            },
        )
        assert signup_r.status_code == 200, signup_r.text

    login_r = requests.post(
        f"{API}/auth/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    assert login_r.status_code == 200, (
        "Login failed for tests. Set NETPULSE_TEST_ADMIN_USER/NETPULSE_TEST_ADMIN_PASS "
        "if your existing admin credentials are different.\n"
        f"{login_r.status_code} {login_r.text}"
    )
    token = login_r.json()["token"]
    _AUTH_HEADERS = {"Authorization": f"Bearer {token}"}
    return _AUTH_HEADERS

def _create_area_and_package(tag: str):
    area_name = f"Test Area {tag}"
    pkg_name = f"Test Package {tag}"
    headers = _ensure_auth_headers()
    area_r = requests.post(f"{API}/areas", json={"name": area_name}, headers=headers)
    pkg_r = requests.post(
        f"{API}/packages",
        json={
            "name": pkg_name,
            "speed": "10 Mbps",
            "monthly_fee": 1000,
            "description": "Test package desc",
        },
        headers=headers,
    )
    assert area_r.status_code == 200, area_r.text
    assert pkg_r.status_code == 200, pkg_r.text
    return area_r.json()["id"], pkg_r.json()["id"]

def check_server():
    try:
        r = requests.get(f"{API}/auth/status", timeout=3)
        return r.status_code == 200
    except Exception as e:
        print(f"API server not available: {e}")
        return False

def test_add_customer():
    import time
    tag = str(time.time_ns())
    unique_username = f"testuser{tag}"
    area_id, package_id = _create_area_and_package(tag)
    headers = _ensure_auth_headers()
    data = {
        "username": unique_username,
        "full_name": "Test User",
        "mobile": "03001234567",
        "expiring": "2026-12-31",
        "package_id": package_id,
        "area_id": area_id,
        "status": "active",
        "notes": "Test notes"
    }
    r = requests.post(f"{API}/customers", json=data, headers=headers)
    if r.status_code != 200:
        print(f"Add customer failed: {r.status_code} {r.text}")
    assert r.status_code == 200
    assert "id" in r.json()

def test_get_customers():
    r = requests.get(f"{API}/customers", headers=_ensure_auth_headers())
    assert r.status_code == 200
    assert isinstance(r.json(), list)

def test_add_package():
    import time
    unique_pkg_name = f"Test Package {time.time_ns()}"
    headers = _ensure_auth_headers()
    data = {
        "name": unique_pkg_name,
        "speed": "10 Mbps",
        "monthly_fee": 1000,
        "description": "Test package desc"
    }
    r = requests.post(f"{API}/packages", json=data, headers=headers)
    if r.status_code != 200:
        print(f"Add package failed: {r.status_code} {r.text}")
    assert r.status_code == 200
    assert "id" in r.json()

def test_add_area():
    import time
    unique_area = f"Test Area {time.time_ns()}"
    headers = _ensure_auth_headers()
    data = {"name": unique_area}
    r = requests.post(f"{API}/areas", json=data, headers=headers)
    if r.status_code != 200:
        print(f"Add area failed: {r.status_code} {r.text}")
    assert r.status_code == 200
    assert "id" in r.json()

def test_add_bill():
    import time
    tag = str(time.time_ns())
    area_id, package_id = _create_area_and_package(tag)
    headers = _ensure_auth_headers()
    cust_r = requests.post(
        f"{API}/customers",
        json={
            "username": f"billuser{tag}",
            "full_name": "Bill Test User",
            "mobile": "03001230000",
            "expiring": "2026-12-31",
            "package_id": package_id,
            "area_id": area_id,
            "status": "active",
            "notes": "Billing test customer",
        },
        headers=headers,
    )
    assert cust_r.status_code == 200, cust_r.text
    customer_id = cust_r.json()["id"]

    data = {
        "customer_id": customer_id,
        "month": "2026-12",
        "package_fee": 1000,
        "due_date": "2026-12-28"
    }
    r = requests.post(f"{API}/bills", json=data, headers=headers)
    if r.status_code != 200:
        print(f"Add bill failed: {r.status_code} {r.text}")
    assert r.status_code == 200
    assert "id" in r.json()

def test_settings():
    r = requests.get(f"{API}/settings", headers=_ensure_auth_headers())
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
