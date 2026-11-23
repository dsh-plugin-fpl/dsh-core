export function Feedback({ error }: { error?: string }) {
  return error ? (
    <p className="core-feedback" role="alert">
      {error}
    </p>
  ) : null;
}
