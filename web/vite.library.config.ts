import {mergeConfig, defineConfig} from 'vite';
import {resolve} from 'node:path';
import base from './vite.config';
export default mergeConfig(base, defineConfig({
  build: {outDir: '../packages/core-ui/dist', emptyOutDir: true,
    lib: {entry: {index: resolve(import.meta.dirname, 'src/embedded.tsx'), files: resolve(import.meta.dirname, 'src/features/resources/ProductFilePicker.tsx')}, formats: ['es'], cssFileName: 'index', fileName: (_format, entryName) => entryName + '.js'},
    rollupOptions: {external: ['react','react-dom','react/jsx-runtime','@tanstack/react-query']}}
}));
