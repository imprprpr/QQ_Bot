import fs from 'node:fs';
import path from 'node:path';
import {randomUUID} from 'node:crypto';

// Errors whose message is written for the operator and safe to show in the page.
export class UserError extends Error {
  constructor(message, status = 400) { super(message); this.name = 'UserError'; this.status = status; }
}
export const fail = (message, status) => { throw new UserError(message, status); };
// Revision mismatch: someone else saved first. The page offers "重新读取".
export const conflict = message => fail(message, 409);

export function crossSite(req) {
  const origin = req.headers?.origin;
  if (!origin) return false;
  try { return new URL(origin).host !== req.headers.host; } catch { return true; }
}

// One handler for every NapCat route: no-store, same-origin only, operator-safe errors.
// Unexpected failures (disk, permissions, corrupt JSON) are logged with details server-side
// and reported with the route's generic message, so internals never reach the browser.
export function jsonRoute(ctx, fn, {fallback = '操作失败，请稍后重试', status = 500} = {}) {
  return async (req, res) => {
    res.setHeader('Cache-Control', 'no-store');
    if (crossSite(req)) { res.status(403).json({code: 403, message: '跨站请求被拒绝'}); return; }
    try {
      res.json({code: 0, data: await fn(req.body || {})});
    } catch (error) {
      if (error instanceof UserError) {
        res.status(error.status).json({code: error.status, message: error.message.slice(0, 200)});
        return;
      }
      ctx.logger?.error?.(`AnimaBot ${req.method || ''} ${req.path || req.url || ''} failed:`, error);
      res.status(status).json({code: status, message: fallback});
    }
  };
}

export function atomicWriteJson(file, data, {maxBytes = 256 * 1024, mode = 0o640} = {}) {
  const content = JSON.stringify(data, null, 2) + '\n';
  if (Buffer.byteLength(content) > maxBytes) fail('配置文件过大');
  fs.mkdirSync(path.dirname(file), {recursive: true});
  const temporary = file + '.' + randomUUID() + '.tmp';
  try {
    const fd = fs.openSync(temporary, 'wx', mode);
    try { fs.writeFileSync(fd, content); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
    fs.renameSync(temporary, file);
  } finally {
    // Never let cleanup hide the original write error.
    try { fs.rmSync(temporary, {force: true}); } catch { /* best effort */ }
  }
  return data;
}
