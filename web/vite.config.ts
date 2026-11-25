import {bootLoading} from "./boot-loading-plugin";
import {defineConfig} from 'vite';
import {resolve} from 'node:path';
export default defineConfig({plugins:[bootLoading()],define:{"import.meta.env.VITE_FPL_LOCAL_BRAND":JSON.stringify(process.env.DSH_FPL_INCLUDE_LOCAL_BRAND || "0")},resolve:{alias:Object.fromEntries(['app','features','shared','platform','generated'].map(n=>['@'+n,resolve(import.meta.dirname,'src',n)]))},build:{outDir:'dist',emptyOutDir:true},server:{host:'127.0.0.1',port:9311,strictPort:true}});
