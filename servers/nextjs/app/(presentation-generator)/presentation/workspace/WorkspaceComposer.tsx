import { FormEvent, useState } from "react";

interface WorkspaceComposerProps {
  disabled?: boolean;
  onSend: (command: string) => Promise<void>;
}

export function WorkspaceComposer({ disabled = false, onSend }: WorkspaceComposerProps) {
  const [value, setValue] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const command = value.trim();
    if (!command || disabled) return;
    try {
      await onSend(command);
      setValue("");
    } catch {
      // The timeline hook renders the durable request error next to the input.
    }
  };

  return (
    <form
      className="border-t border-slate-200 bg-white p-3"
      onSubmit={submit}
      data-testid="workspace-command-form"
    >
      <label htmlFor="workspace-command" className="sr-only">
        프레젠테이션 수정 요청
      </label>
      <textarea
        id="workspace-command"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            event.currentTarget.form?.requestSubmit();
          }
        }}
        rows={3}
        disabled={disabled}
        placeholder="슬라이드 수정을 요청하세요"
        className="block w-full resize-none rounded-xl border border-slate-300 px-3 py-2 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100 disabled:bg-slate-100"
      />
      <div className="mt-2 flex items-center justify-between gap-3">
        <p className="text-[11px] text-slate-500">Enter 전송 · Shift+Enter 줄바꿈</p>
        <button
          type="submit"
          disabled={disabled || value.trim().length === 0}
          className="rounded-lg bg-blue-600 px-3 py-2 text-xs font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          전송
        </button>
      </div>
    </form>
  );
}
