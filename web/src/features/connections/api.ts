import { fetchApiJson } from "@platform/api/client";
import type { components } from "@generated/api";

export type BridgeEntry = components["schemas"]["BridgeOut"];
export const fetchBridges = () =>
  fetchApiJson<components["schemas"]["BridgeListOut"]>("/v1/bridges");
export const isDshConnection = (bridge: BridgeEntry) => bridge.kind === "dsh";
