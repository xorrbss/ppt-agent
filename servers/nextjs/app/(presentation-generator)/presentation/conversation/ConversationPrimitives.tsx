import type { HTMLAttributes, PropsWithChildren } from "react";

export type ConversationMessageAppearance = "user" | "assistant";

export const conversationTimelineClassName =
  "min-h-0 flex-1 space-y-7 overflow-y-auto bg-white px-4 py-5";

export const conversationAssistantMarkdownClassName =
  "break-words text-[15px] leading-[1.65] text-slate-900 [&_a]:text-blue-700 [&_a]:underline [&_blockquote]:border-l-2 [&_blockquote]:border-slate-300 [&_blockquote]:pl-3 [&_code]:rounded [&_code]:bg-slate-100 [&_code]:px-1 [&_li]:ml-5 [&_ol]:list-decimal [&_p+p]:mt-3 [&_pre]:my-3 [&_pre]:max-w-full [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:bg-slate-100 [&_pre]:p-3 [&_table]:my-3 [&_table]:block [&_table]:max-w-full [&_table]:overflow-x-auto [&_ul]:list-disc";

export function conversationAttachmentClassName(
  appearance: ConversationMessageAppearance,
): string {
  return `flex min-w-0 items-center gap-2 rounded-lg px-2.5 py-2 ${
    appearance === "user"
      ? "bg-white/75 text-slate-700"
      : "bg-slate-50 text-slate-700"
  }`;
}

export function ConversationMessageFrame({
  appearance,
  className = "",
  children,
  ...props
}: PropsWithChildren<
  HTMLAttributes<HTMLDivElement> & {
    appearance: ConversationMessageAppearance;
  }
>) {
  return (
    <div
      {...props}
      className={`${
        appearance === "user"
          ? "ml-auto max-w-[85%] rounded-[20px] bg-slate-100 px-4 py-3 text-slate-900"
          : "mr-auto w-full max-w-full text-slate-900"
      } ${className}`.trim()}
    >
      {children}
    </div>
  );
}

export function ConversationComposerFrame({
  className = "",
  children,
  ...props
}: PropsWithChildren<HTMLAttributes<HTMLDivElement>>) {
  return (
    <div
      {...props}
      className={`rounded-[24px] border border-slate-300 bg-white px-3 py-2 shadow-sm transition focus-within:border-blue-400 focus-within:ring-2 focus-within:ring-blue-100 ${className}`.trim()}
    >
      {children}
    </div>
  );
}
