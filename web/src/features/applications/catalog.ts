import { registrationRows } from "./registrationRows";
import { useQuery } from "@tanstack/react-query";
import { fetchApiJson } from "@platform/api/client";
import {
  Boxes,
  Image,
  Notebook,
  Film,
  Code,
  AudioLines,
  Presentation,
  Table,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { components } from "@generated/api";
export type ApplicationEntry = components["schemas"]["ApplicationOut"];
export type ApplicationCategory = ApplicationEntry["category"];
export type ApplicationDefinition = ApplicationEntry & {
  path: string;
  registrationIds: string[];
  brandImage?: string;
  iconComponent: LucideIcon;
};
export const APPLICATION_CATEGORIES = [
  { value: "all", label: "全部应用" },
  { value: "visual", label: "视觉与叙事" },
  { value: "knowledge", label: "知识与表达" },
  { value: "media", label: "影像与声音" },
  { value: "development", label: "开发与协作" },
] as const;
export function categoryLabel(category: string) {
  return (
    APPLICATION_CATEGORIES.find((c) => c.value === category)?.label ?? category
  );
}
const icons = {
  app: Boxes,
  image: Image,
  notebook: Notebook,
  film: Film,
  code: Code,
  audio: AudioLines,
  presentation: Presentation,
  table: Table,
};
export function useApplications() {
  return useQuery({
    queryKey: ["core-applications"],
    queryFn: async () => {
      const data = await fetchApiJson<{ items: ApplicationEntry[] }>(
        "/v1/applications",
      );
      return registrationRows(data.items).map((app) => ({
        ...app,
        path: "/apps/" + encodeURIComponent(app.id),
        brandImage: app.icon_url || undefined,
        iconComponent: icons[app.icon],
      }));
    },
    refetchInterval: 2000,
  });
}
export function statusLabel(app: ApplicationEntry) {
  return app.status === "ready"
    ? "可打开"
    : app.status === "offline"
      ? "应用未连接"
      : "等待应用入口";
}
