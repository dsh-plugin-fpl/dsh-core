"""Shared embedded HTTP routes, installed behind each product's own boundary."""

import hmac

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from .host import CoreError
from .http import CoreHttp
from .login import login_document


def install_routes(app, host, access, csrf, *, application_id, title, path, port,
                   launch_secret=None, active_tasks=lambda: 0, data_directory=None):
    """The product must check Host, Origin, session and CSRF before forwarding /v1/core.

    Only password-session (login-state authenticated) is exempt from the product
    session middleware. The launcher endpoint authenticates a private one-use key.
    """
    prefix = application_id.replace('-', '_')
    origin = f'http://127.0.0.1:{port}'

    @app.get('/v1/app/storage')
    async def product_storage(request: Request):
        profile = host.profile(request.cookies.get(access.cookie, ''))
        if not profile.get('legacy') or profile['role'] != 'admin':
            raise HTTPException(403, 'machine_owner_required')
        return {'path':str(data_directory or host.options.data_directory.parent)}

    def session_response(token, current, value):
        response = JSONResponse(value)
        response.set_cookie(access.cookie, token, httponly=True, samesite='strict', max_age=86400)
        response.set_cookie(prefix + '_csrf', csrf(current), samesite='strict', max_age=86400)
        return response

    @app.get('/__desktop/login')
    async def desktop_login(state: str = ''):
        if not state or len(state) > 120:
            raise HTTPException(400, 'login_state_required')
        return HTMLResponse(login_document(title, state, path), headers={'Cache-Control':'no-store'})

    @app.post('/v1/app/password-session')
    async def password_session(request: Request):
        if request.headers.get('origin') != origin:
            raise HTTPException(403, 'origin_not_allowed')
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(422, 'invalid_credentials')
        state = body.get('state', '')
        if not isinstance(state, str) or not state or not hmac.compare_digest(state, request.cookies.get(prefix + '_login_state', '')):
            raise HTTPException(403, 'login_state_invalid')
        if not isinstance(body.get('username'), str) or not isinstance(body.get('password'), str) or len(body['password']) > 256:
            raise HTTPException(422, 'invalid_credentials')
        try:
            token = host.password_product_session(body['username'], body['password'])
        except CoreError as error:
            raise HTTPException(401, 'invalid_credentials') from error
        current = await access.resolve(token)
        response = session_response(token, current, {'signed_in':True})
        response.delete_cookie(prefix + '_login_state')
        return response

    if launch_secret:
        launch_used = False

        @app.get('/__desktop/status')
        async def desktop_status(request: Request):
            if not hmac.compare_digest(request.headers.get('x-fpl-control', ''), launch_secret):
                raise HTTPException(401, 'desktop_control_required')
            return {'active_tasks':host.active_tasks() + active_tasks()}

        @app.post('/__desktop/session')
        async def desktop_session(request: Request):
            nonlocal launch_used
            if request.headers.get('origin') != origin:
                raise HTTPException(403, 'origin_not_allowed')
            if launch_used or not hmac.compare_digest(request.headers.get('x-fpl-launch', ''), launch_secret):
                raise HTTPException(401, 'launch_ticket_invalid')
            launch_used = True
            token = host.local_product_session()
            current = await access.resolve(token)
            return session_response(token, current, {'ready':True, 'url':path})

    core_http = CoreHttp(host, access.cookie)

    @app.api_route('/v1/core/{path:path}', methods=['GET','HEAD','POST','PUT','PATCH','DELETE'])
    async def embedded_http(path: str):
        class Forward(Response):
            async def __call__(self, scope, receive, send):
                await core_http(scope, receive, send)
        return Forward()
