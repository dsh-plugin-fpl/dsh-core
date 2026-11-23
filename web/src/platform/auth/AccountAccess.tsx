import { createContext, useContext, type ReactNode } from "react";
export type CoreProfile = {
  id: string;
  username: string;
  display_name: string;
  role: "admin" | "user";
  revision: number;
  active: boolean;
  legacy: boolean;
};
const Access = createContext<{
  profile: CoreProfile;
  permissions: string[];
} | null>(null);
export function CoreOwnerAccess({
  children,
  profile,
  permissions,
}: {
  children: ReactNode;
  profile: CoreProfile;
  permissions: string[];
}) {
  return (
    <Access.Provider value={{ profile, permissions }}>
      {children}
    </Access.Provider>
  );
}
export function useAccountPermission(permission: string) {
  return useAccessValue()?.permissions.includes(permission) ?? false;
}
function useAccessValue() {
  return useContext(Access);
}
export function useCoreAccount() {
  const value = useAccessValue();
  if (!value) throw new Error("Core account context missing");
  return value.profile;
}
export function useAccountId() {
  return useCoreAccount().id;
}
