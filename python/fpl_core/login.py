"""Product-local account login document, shared by embedded products."""

import html
import json


def login_document(title, state, destination):
    config = json.dumps({'state':state,'destination':destination}).replace('<','\\u003c')
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>登录</title><style>body{font:16px system-ui;background:#f8f6f2;color:#292824;display:grid;place-items:center;min-height:95vh}main{padding:32px;background:white;border-radius:20px;width:min(360px,80vw)}label{display:block;margin:16px 0}input,button{font:inherit;padding:10px;box-sizing:border-box;width:100%;margin-top:6px}p{line-height:1.6}#error{color:#b13d22}</style>
<main><h1>'''+html.escape(title)+'''</h1><form id="login"><label>账号<input name="username" autocomplete="username" required maxlength="64"></label><label>密码<input name="password" type="password" autocomplete="current-password" required maxlength="256"></label><button>登录</button></form><p id="error" role="alert"></p><p>本机所有者可重新打开应用进入。</p></main>
<script>const config='''+config+''';document.getElementById('login').onsubmit=async event=>{event.preventDefault();const form=new FormData(event.target);const response=await fetch('/v1/app/password-session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:form.get('username'),password:form.get('password'),state:config.state})});if(response.ok){location.assign(config.destination)}else{document.getElementById('error').textContent='登录失败，请核对账号密码或稍后重试。'}};</script></html>'''
