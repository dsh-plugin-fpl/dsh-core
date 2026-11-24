"""Compatibility endpoints for clients with a remembered realtime voice selection."""

from fastapi import WebSocket


def register_voice(app, origin):
    @app.get("/v1/model-test/realtime-options")
    async def options():
        return {"models": []}

    @app.websocket("/v1/model-test/realtime")
    async def realtime(socket: WebSocket):
        if (
            socket.headers.get("host") != origin.split("://")[1]
            or socket.headers.get("origin") != origin
            or not app.state.access.session(socket.cookies.get("core_session"))
        ):
            await socket.close(code=1008)
            return
        await socket.accept()
        await socket.send_json(
            {
                "type": "error",
                "code": "capability_unavailable",
                "message": "当前没有可用的实时语音供应商",
            }
        )
        await socket.close(code=1000)
