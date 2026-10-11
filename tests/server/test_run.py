def test_main_builds_uvicorn_config(monkeypatch):
    import auris.server.run as run

    captured = {}
    monkeypatch.setattr(run.uvicorn, "run", lambda app, **kw: captured.update(kw))
    monkeypatch.setenv("AURIS_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("AURIS_PORT", "9100")

    run.main()
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 9100
    assert captured["workers"] == 1  # single process so weights load once
