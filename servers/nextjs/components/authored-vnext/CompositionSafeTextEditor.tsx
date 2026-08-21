"use client";

import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type CompositionEvent,
  type KeyboardEvent,
} from "react";

export interface CompositionSafeTextEditorProps {
  value: string;
  label: string;
  disabled?: boolean;
  multiline?: boolean;
  className?: string;
  style?: CSSProperties;
  onCommit: (value: string) => void;
}

export function CompositionSafeTextEditor({
  value,
  label,
  disabled = false,
  multiline = true,
  className = "",
  style,
  onCommit,
}: CompositionSafeTextEditorProps) {
  const [draft, setDraft] = useState(value);
  const composingRef = useRef(false);
  const lastCommittedRef = useRef(value);

  useEffect(() => {
    if (!composingRef.current) {
      setDraft(value);
      lastCommittedRef.current = value;
    }
  }, [value]);

  const commit = (nextValue: string) => {
    if (composingRef.current || nextValue === lastCommittedRef.current) return;
    lastCommittedRef.current = nextValue;
    onCommit(nextValue);
  };

  const handleCompositionStart = () => {
    composingRef.current = true;
  };

  const handleCompositionEnd = (event: CompositionEvent<HTMLTextAreaElement>) => {
    composingRef.current = false;
    const nextValue = event.currentTarget.value;
    setDraft(nextValue);
    commit(nextValue);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Escape" && !composingRef.current) {
      event.preventDefault();
      setDraft(value);
      return;
    }
    if (!multiline && event.key === "Enter" && !composingRef.current) {
      event.preventDefault();
      commit(event.currentTarget.value);
      event.currentTarget.blur();
    }
  };

  return (
    <textarea
      aria-label={label}
      className={className}
      disabled={disabled}
      onBlur={(event) => commit(event.currentTarget.value)}
      onChange={(event) => setDraft(event.currentTarget.value)}
      onCompositionEnd={handleCompositionEnd}
      onCompositionStart={handleCompositionStart}
      onKeyDown={handleKeyDown}
      rows={multiline ? 3 : 1}
      spellCheck
      style={style}
      value={draft}
    />
  );
}
