def test_health_is_open_and_ready_reports_state(client):
    # /health needs no auth
    r = client.get("/health", headers={})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json()["ready"] is True
