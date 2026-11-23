import {createContext,useContext,type ReactNode} from 'react';
const Access = createContext(false);
export function CoreOwnerAccess({children}:{children:ReactNode}) { return <Access.Provider value={true}>{children}</Access.Provider>; }
export function useAccountPermission(_permission:string) { return useContext(Access); }

export function useAccountId(){return 'local-owner';}
