import {useEffect, useState} from 'react';

/** Product data moves together; the standalone Core migration UI is inapplicable. */
export function ProductStorage() {
  const [path, setPath] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    void fetch('/v1/app/storage', {credentials:'include'}).then(async response => {
      if (!response.ok) throw new Error('无法读取存储位置');
      setPath((await response.json()).path);
    }).catch(error => setError(String(error)));
  }, []);
  return <section className="page"><h1>存储</h1>
    <p>项目、素材和公共设置由本工坊统一管理。迁移数据前请退出工坊，并保留备份。</p>
    {error ? <p role="alert">{error}</p> : <p>数据目录：<code>{path || '正在读取…'}</code></p>}
  </section>;
}
