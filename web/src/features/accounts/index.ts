export const accountQueryKeys = {root:['local-owner'] as const};
export async function listApiKeys() { return [] as {id:string;name:string;key_prefix:string}[]; }
