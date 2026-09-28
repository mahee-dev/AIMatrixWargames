"""I-088 — enforce the request-body bound while ASGI chunks arrive."""

from __future__ import annotations

import asyncio

from fastapi import Request

from app.api import create_app


def test_i088_001_chunked_body_stops_at_limit_and_accepted_body_is_replayable(
    db_engine,
) -> None:
    app = create_app(engine=db_engine)
    reached: list[bytes] = []

    @app.post("/_body-limit-probe")
    async def probe(request: Request) -> dict[str, int]:
        body = await request.body()
        reached.append(body)
        return {"size": len(body)}

    async def request(chunks: list[bytes]) -> tuple[list[dict], int]:
        messages = [
            {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
            for index, chunk in enumerate(chunks)
        ]
        receive_calls = 0
        sent: list[dict] = []

        async def receive() -> dict:
            nonlocal receive_calls
            receive_calls += 1
            return messages.pop(0)

        async def send(message: dict) -> None:
            sent.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/_body-limit-probe",
            "raw_path": b"/_body-limit-probe",
            "query_string": b"",
            "headers": [(b"host", b"testserver")],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "root_path": "",
        }
        await app(scope, receive, send)
        return sent, receive_calls

    oversized, calls = asyncio.run(request([b"a" * 32_768, b"b" * 32_769, b"unread"]))
    assert next(message for message in oversized if message["type"] == "http.response.start")[
        "status"
    ] == 413
    assert calls == 2
    assert reached == []

    accepted, calls = asyncio.run(request([b"a" * 32_768, b"b" * 32_768]))
    assert next(message for message in accepted if message["type"] == "http.response.start")[
        "status"
    ] == 200
    assert calls == 2
    assert reached == [b"a" * 32_768 + b"b" * 32_768]
