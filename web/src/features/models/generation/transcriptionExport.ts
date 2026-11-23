import type {components} from "@generated/api";
export function buildTranscriptionFile(
  task: components["schemas"]["ModelTestTranscriptionTaskOut"],
) {
  return {
    kind: "future-props-transcription",
    schema_version: "1.1",
    exported_at: new Date().toISOString(),
    source: {
      kind: task.source.kind,
      asset_id: task.source.source_asset_id,
      filename: task.source.filename,
      mime_type: task.source.mime_type,
      byte_size: task.source.byte_size,
    },
    recognition: {
      task_id: task.id,
      provider_kind: task.provider_kind,
      provider_name: task.provider_name,
      model_slug: task.model_slug,
      language: task.language,
      created_at: task.created_at,
      completed_at: task.completed_at,
      text: task.result_text ?? "",
      segments: task.result_segments.map((segment) => ({ ...segment })),
      parameters: { ...task.parameters },
      result_detail: {
        ...task.result_detail,
        words: task.result_detail.words?.map((word) => ({ ...word })),
        warnings: task.result_detail.warnings?.map((warning) => ({
          ...warning,
        })),
        stages: task.result_detail.stages?.map((stage) => ({ ...stage })),
        overlap_intervals: task.result_detail.overlap_intervals?.map(
          (interval) => ({
            ...interval,
          }),
        ),
        audio_info: task.result_detail.audio_info
          ? { ...task.result_detail.audio_info }
          : null,
      },
    },
  };
}

export function safeFileBase(value: string): string {
  return (
    Array.from(value)
      .map((character) =>
        character.charCodeAt(0) < 32 || '<>:"/\\|?*'.includes(character)
          ? "-"
          : character,
      )
      .join("")
      .trim()
      .slice(0, 100) || "audio-annotation"
  );
}
