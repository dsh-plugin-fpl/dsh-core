/** Shared desktop lifecycle. Products supply their own executable, arguments and state. */
import { app, BrowserWindow, dialog, nativeImage, session, shell } from 'electron';
import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { mkdir, open } from 'node:fs/promises';
import { join } from 'node:path';
import {createServer} from 'node:net';

async function availablePort(preferred) {
  return await new Promise((resolve, reject) => {
    const server = createServer();
    server.once('error', error => {
      if (error.code === 'EADDRINUSE') availablePort(0).then(resolve, reject);
      else reject(error);
    });
    server.listen(preferred, '127.0.0.1', () => {
      const port = server.address().port;
      server.close(error => error ? reject(error) : resolve(port));
    });
  });
}

export async function runDesktop(config) {
  if (!app.requestSingleInstanceLock()) { app.quit(); return; }
  let window, backend, quitting = false, checkingQuit = false, allowDiscard = false;
  app.on('second-instance', () => { window?.show(); window?.focus(); });
  app.setName(config.title);
  app.setPath('userData', process.env.FPL_DESKTOP_STATE_ROOT || join(app.getPath('appData'), 'FPL', config.applicationId));
  await app.whenReady();
  const icon = config.icon ? nativeImage.createFromPath(config.icon) : undefined;
  if (icon?.isEmpty()) throw new Error('无法读取应用图标：' + config.icon);
  if (icon && process.platform === 'darwin') app.dock.setIcon(icon);
  const root = process.env.FPL_DESKTOP_STATE_ROOT || app.getPath('userData');
  await mkdir(root, {recursive: true, mode: 0o700});
  const launch = randomBytes(32).toString('hex');
  const port = await availablePort(config.port);
  const origin = `http://127.0.0.1:${port}`;
  const log = await open(join(root, 'desktop-backend.log'), 'a', 0o600);
  backend = spawn(config.python, [...config.args, '--state-root', root, '--port', String(port)], {
    cwd: config.cwd, env: {...process.env, ...config.env, FPL_DESKTOP_LAUNCH_SECRET: launch},
    stdio: ['ignore', log.fd, log.fd],
  });
  await log.close();
  let failure;
  backend.on('error', error => { failure = error; });
  backend.on('exit', code => {
    if (!quitting) {
      failure = new Error(`后端已退出（${code}），诊断：${join(root, 'desktop-backend.log')}`);
      dialog.showErrorBox(config.title, failure.message);
      app.quit();
    }
  });
  app.on('before-quit', event => {
    if (quitting || !backend || backend.exitCode !== null) return;
    event.preventDefault();
    if (checkingQuit) return;
    checkingQuit = true;
    void (async () => {
      try {
        if (window && !window.isDestroyed()) {
          const dirty = await window.webContents.executeJavaScript(`(() => {
            const event = new Event('beforeunload', {cancelable: true});
            window.dispatchEvent(event); return event.defaultPrevented;
          })()`);
          if (dirty) {
            const choice = await dialog.showMessageBox(window, {
              type: 'question', message: '当前页面有未保存的更改',
              buttons: ['继续编辑', '放弃更改并退出'], defaultId: 0, cancelId: 0,
            });
            if (choice.response === 0) return;
            allowDiscard = true;
          }
        }
        const response = await fetch(origin + '/__desktop/status', {
          headers: {'x-fpl-control': launch}, signal: AbortSignal.timeout(3000),
        });
        if (response.ok && (await response.json()).active_tasks > 0) {
          const choice = await dialog.showMessageBox(window, {
            type: 'question', message: '仍有任务正在执行',
            detail: '退出会停止本机执行。未确认的外部调用会保留回执，重新打开后需要核对结果。',
            buttons: ['继续运行', '退出应用'], defaultId: 0, cancelId: 0,
          });
          if (choice.response === 0) return;
        }
        quitting = true;
        backend.once('exit', () => app.quit());
        backend.kill('SIGTERM');
      } catch (error) {
        dialog.showErrorBox(config.title, '退出检查未完成：' + String(error));
      } finally { checkingQuit = false; }
    })();
  });
  try {
    let ready = false;
    for (let i = 0; i < 150; i++) {
      if (failure) throw failure;
      try {
        const response = await fetch(origin + '/ready', {signal: AbortSignal.timeout(1000)});
        const body = await response.json();
        if (response.ok && body.application === config.applicationId) { ready = true; break; }
      } catch {}
      await new Promise(resolve => setTimeout(resolve, 200));
    }
    if (!ready) throw new Error('启动超时，请查看桌面后端日志。');
    const desktopSession = session.fromPartition(`persist:${config.applicationId}`);
    desktopSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    const response = await desktopSession.fetch(origin + '/__desktop/session', {
      method: 'POST', headers: {'Origin': origin, 'x-fpl-launch': launch},
    });
    if (!response.ok) throw new Error('无法建立本机会话，端口可能由另一实例占用。');
    const openWindow = async () => {
      window = new BrowserWindow({title: config.title, icon, width: 1440, height: 960,
        minWidth: 760, minHeight: 540, show: false,
        webPreferences: {session: desktopSession, nodeIntegration: false, contextIsolation: true, sandbox: true}});
      window.webContents.on('will-navigate', (event, url) => {
        if (new URL(url).origin !== origin) event.preventDefault();
      });
      window.webContents.setWindowOpenHandler(({url}) => {
        const parsed = new URL(url);
        if (parsed.origin === origin) { window.loadURL(url); }
        else if (parsed.protocol === 'https:') { void shell.openExternal(url); }
        return {action: 'deny'};
      });
      window.webContents.on('will-prevent-unload', event => {
        if (allowDiscard) { event.preventDefault(); return; }
        const choice = dialog.showMessageBoxSync(window, {
          type: 'question', message: '当前页面有未保存的更改',
          buttons: ['继续编辑', '放弃更改'], defaultId: 0, cancelId: 0,
        });
        if (choice === 1) event.preventDefault();
      });
      window.once('ready-to-show', () => window.show());
      await window.loadURL(origin + config.path);
    };
    await openWindow();
    app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) void openWindow(); });
  } catch (error) {
    dialog.showErrorBox(config.title, String(error));
    app.quit();
  }

}
