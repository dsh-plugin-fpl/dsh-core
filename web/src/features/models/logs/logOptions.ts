import {
  providerKindLabel,
  type ProviderKind,
} from "@features/models/metadata";

export const providerLabels: Record<string, string> = providerKindLabel;

const LOG_PROVIDER_KINDS: ProviderKind[] = [
  "openai",
  "deepseek",
  "anthropic",
  "local_qwen_tts",
  "local_zonos_tts",
];

export const logProviderKindOptions = [
  { value: "", label: "全部接入协议" },
  ...LOG_PROVIDER_KINDS.map((value) => ({
    value,
    label: providerKindLabel[value],
  })),
];
