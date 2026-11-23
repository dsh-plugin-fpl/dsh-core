export function applySessionHeaders(request: Request) { const token = document.cookie.split('; ').find(v => v.startsWith('core_csrf='))?.split('=').slice(1).join('='); if (token && !['GET','HEAD'].includes(request.method)) request.headers.set('X-Core-CSRF', decodeURIComponent(token)); return request; }
export function notifyAuthRequired() { window.dispatchEvent(new Event('core:auth-required')); }

export function getCsrfToken(){return decodeURIComponent(document.cookie.split('; ').find(v=>v.startsWith('core_csrf='))?.split('=')[1]||'');}
