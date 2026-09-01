from __future__ import annotations

from cat_video_generator.interfaces import cli


def test_development_api_reload_uses_an_importable_app_factory(monkeypatch) -> None:
    calls: list[tuple[object, dict[str, object]]] = []
    monkeypatch.setattr(
        cli.uvicorn,
        "run",
        lambda app, **options: calls.append((app, options)),
    )

    cli.serve_api(
        host="127.0.0.1",
        port=8765,
        static_dir=None,
        reload=True,
    )

    assert calls == [
        (
            "cat_video_generator.interfaces.cli:create_runtime_app",
            {
                "host": "127.0.0.1",
                "port": 8765,
                "reload": True,
                "factory": True,
            },
        )
    ]

