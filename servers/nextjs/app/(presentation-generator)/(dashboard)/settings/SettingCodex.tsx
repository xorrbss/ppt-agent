"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import {
    Check,
    ChevronUp,
    Loader2,
    RefreshCw,
    Trash2,
    UserCheck,
} from "lucide-react";
import {
    Command,
    CommandEmpty,
    CommandGroup,
    CommandInput,
    CommandItem,
    CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { notify } from "@/components/ui/sonner";
import { getApiUrl } from "@/utils/api";
import { Button } from "@/components/ui/button";
import {
    CHATGPT_MODELS,
    DEFAULT_CODEX_MODEL,
} from "@/components/CodexConfig";

interface CodexConfigProps {
    codexModel: string;
    onInputChange: (value: string | boolean, field: string) => void;
}

type AuthStatus = "checking" | "unauthenticated" | "polling" | "authenticated";

interface StatusResponse {
    status: string;
    account_id?: string;
    username?: string;
    email?: string;
    is_pro?: boolean;
    detail?: string;
}

export default function CodexConfig({
    codexModel,
    onInputChange,
}: CodexConfigProps) {
    const [authStatus, setAuthStatus] = useState<AuthStatus>("checking");
    const [accountId, setAccountId] = useState<string | null>(null);
    const [username, setUsername] = useState<string | null>(null);
    const [email, setEmail] = useState<string | null>(null);
    const [sessionId, setSessionId] = useState<string | null>(null);
    const [manualCode, setManualCode] = useState("");
    const [isExchanging, setIsExchanging] = useState(false);
    const [isLoggingOut, setIsLoggingOut] = useState(false);
    const [isRefreshing, setIsRefreshing] = useState(false);
    const [openModelSelect, setOpenModelSelect] = useState(false);
    const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

    const stopPolling = useCallback(() => {
        if (pollIntervalRef.current) {
            clearInterval(pollIntervalRef.current);
            pollIntervalRef.current = null;
        }
    }, []);

    const applyProfile = useCallback((data: Partial<StatusResponse>) => {
        setAccountId(data.account_id ?? null);
        setUsername(data.username ?? null);
        setEmail(data.email ?? null);
    }, []);

    const checkCurrentAuthStatus = useCallback(async () => {
        try {
            const res = await fetch(getApiUrl("/api/v1/ppt/codex/auth/status"));
            if (!res.ok) {
                setAuthStatus("unauthenticated");
                applyProfile({});
                return;
            }
            const data: StatusResponse = await res.json();
            if (data.status === "authenticated") {
                setAuthStatus("authenticated");
                applyProfile(data);
            } else {
                setAuthStatus("unauthenticated");
                applyProfile({});
            }
        } catch {
            setAuthStatus("unauthenticated");
            applyProfile({});
        }
    }, [applyProfile]);

    useEffect(() => {
        void checkCurrentAuthStatus();
        return stopPolling;
    }, [checkCurrentAuthStatus, stopPolling]);

    const handleSignIn = async () => {
        try {
            onInputChange('codex', 'LLM');
            const res = await fetch(getApiUrl("/api/v1/ppt/codex/auth/initiate"), {
                method: "POST",
            });
            if (!res.ok) throw new Error("Failed to initiate auth");
            const data = await res.json();
            const { session_id, url } = data;

            setSessionId(session_id);
            setAuthStatus("polling");
            window.open(url, "_blank", "noopener,noreferrer");

            pollIntervalRef.current = setInterval(async () => {
                try {
                    const pollRes = await fetch(
                        getApiUrl(`/api/v1/ppt/codex/auth/status/${session_id}`)
                    );
                    if (!pollRes.ok) return;
                    const pollData: StatusResponse = await pollRes.json();

                    if (pollData.status === "success") {
                        stopPolling();
                        setAuthStatus("authenticated");
                        applyProfile(pollData);
                        setSessionId(null);
                        if (!codexModel) {
                            onInputChange(DEFAULT_CODEX_MODEL, "codex_model");
                        }
                        notify.success(
                            "ChatGPT 로그인 완료",
                            "ChatGPT 계정이 연결되어 사용할 준비가 되었습니다."
                        );
                    } else if (pollData.status === "failed") {
                        stopPolling();
                        setAuthStatus("unauthenticated");
                        applyProfile({});
                        notify.error(
                            "로그인 실패",
                            "인증이 완료되지 않았습니다. 다시 로그인해 주세요."
                        );
                    }
                } catch {
                    // keep polling on transient errors
                }
            }, 2000);
        } catch {
            notify.error(
                "로그인 실패",
                "로그인을 시작할 수 없습니다. 다시 시도해 주세요."
            );
            setAuthStatus("unauthenticated");
            applyProfile({});
        }
    };

    const handleManualExchange = async () => {
        if (!sessionId || !manualCode.trim()) return;
        setIsExchanging(true);
        try {
            const res = await fetch(getApiUrl("/api/v1/ppt/codex/auth/exchange"), {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ session_id: sessionId, code: manualCode.trim() }),
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || "인증 코드 교환에 실패했습니다");
            }
            const data = await res.json();
            stopPolling();
            setAuthStatus("authenticated");
            applyProfile(data);
            setSessionId(null);
            setManualCode("");
            if (!codexModel) {
                onInputChange(DEFAULT_CODEX_MODEL, "codex_model");
            }
            notify.success(
                "ChatGPT 로그인 완료",
                "ChatGPT 계정이 연결되어 사용할 준비가 되었습니다."
            );
        } catch (err: any) {
            notify.error(
                "로그인 실패",
                err.message || "인증 코드를 사용할 수 없습니다. 다시 시도해 주세요."
            );
        } finally {
            setIsExchanging(false);
        }
    };

    const handleCancelPolling = () => {
        stopPolling();
        setSessionId(null);
        setManualCode("");
        setAuthStatus("unauthenticated");
    };

    const handleSignOut = async () => {
        setIsLoggingOut(true);
        try {
            await fetch(getApiUrl("/api/v1/ppt/codex/auth/logout"), { method: "POST" });
            setAuthStatus("unauthenticated");
            applyProfile({});
            onInputChange("codex", "LLM");
            onInputChange('', "codex_model");
            notify.success(
                "로그아웃 완료",
                "ChatGPT 연결이 해제되었습니다."
            );
        } catch {
            notify.error(
                "로그아웃 실패",
                "ChatGPT 연결을 해제할 수 없습니다. 다시 시도해 주세요."
            );
        } finally {
            setIsLoggingOut(false);
        }
    };

    const handleRefreshToken = async () => {
        setIsRefreshing(true);
        try {
            const res = await fetch(getApiUrl("/api/v1/ppt/codex/auth/refresh"), {
                method: "POST",
            });
            if (!res.ok) throw new Error("Refresh failed");
            const data = await res.json();
            applyProfile(data);
            notify.success(
                "세션 갱신 완료",
                "ChatGPT 연결이 성공적으로 갱신되었습니다."
            );
        } catch {
            notify.error(
                "세션 갱신 실패",
                "ChatGPT 세션을 갱신할 수 없습니다. 다시 로그인해 주세요."
            );
            setAuthStatus("unauthenticated");
            applyProfile({});
        } finally {
            setIsRefreshing(false);
        }
    };

    if (authStatus === "checking") {
        return (
            <div className="flex items-center gap-2 py-3 text-gray-400">
                <Loader2 className="w-4 h-4 animate-spin" />
                <span className="text-xs">상태 확인 중…</span>
            </div>
        );
    }

    if (authStatus === "polling") {
        return (
            <div className="space-y-4">
                <div className="flex items-center gap-3 py-2">
                    <Loader2 className="w-4 h-4 text-gray-500 animate-spin" />
                    <span className="text-sm text-gray-600">로그인 대기 중…</span>
                    <button
                        onClick={handleCancelPolling}
                        className="text-xs text-gray-400 hover:text-gray-600 underline underline-offset-2 ml-auto"
                    >
                        취소
                    </button>
                </div>

                <div className="space-y-2">
                    <p className="text-xs text-gray-400">
                        자동으로 리디렉션되지 않으면 리디렉션 URL 또는 코드를 붙여넣으세요
                    </p>
                    <div className="flex gap-2">
                        <input
                            type="text"
                            placeholder="URL 또는 코드 붙여넣기…"
                            className="flex-1 px-2 py-2 outline-none border border-gray-300 rounded-lg text-xs focus:border-gray-400 transition-colors"
                            value={manualCode}
                            onChange={(e) => setManualCode(e.target.value)}
                        />
                        <button
                            onClick={handleManualExchange}
                            disabled={isExchanging || !manualCode.trim()}
                            className="px-3 py-2 bg-[#EDEEEF] hover:bg-[#E4E5E6] disabled:opacity-40 rounded-lg text-xs font-medium text-[#101323] transition-colors"
                        >
                            {isExchanging ? (
                                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                            ) : (
                                "제출"
                            )}
                        </button>
                    </div>
                </div>
            </div>
        );
    }

    if (authStatus === "authenticated") {
        return (
            <div className="space-y-4">
                <div className="flex items-center gap-3 p-3  border border-[#EDEEEF] rounded-lg">
                    <UserCheck className="w-5 h-5 text-black shrink-0" />
                    <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 min-w-0">
                            <p className="text-sm font-medium text-gray-800 truncate">
                                {username || email || (accountId ? `${accountId} 계정` : "ChatGPT 계정")}
                            </p>

                        </div>
                        {email && username && (
                            <p className="text-xs text-gray-500 truncate">{email}</p>
                        )}
                        {!email && accountId && (
                            <p className="text-xs text-gray-500 truncate">ID: {accountId}</p>
                        )}
                        <p className="text-xs text-gray-400">ChatGPT에 로그인됨</p>
                    </div>
                    <div className="flex gap-1.5 shrink-0">
                        <button
                            onClick={handleRefreshToken}
                            disabled={isRefreshing}
                            title="토큰 갱신"
                            className="w-8 h-8 flex items-center justify-center rounded-full bg-[#EDEEEF] hover:bg-[#E4E5E6] disabled:opacity-40 transition-colors"
                        >
                            {isRefreshing ? (
                                <Loader2 className="w-3.5 h-3.5 animate-spin text-gray-500" />
                            ) : (
                                <RefreshCw className="w-3.5 h-3.5 text-gray-500" />
                            )}
                        </button>
                        <button
                            onClick={handleSignOut}
                            disabled={isLoggingOut}
                            title="로그아웃"
                            className="w-8 h-8 flex items-center justify-center rounded-full bg-[#EDEEEF] hover:bg-[#E4E5E6] disabled:opacity-40 transition-colors"
                        >
                            {isLoggingOut ? (
                                <Loader2 className="w-3.5 h-3.5 animate-spin text-gray-500" />
                            ) : (
                                <Trash2 className="w-3.5 h-3.5 text-gray-500" />
                            )}
                        </button>
                    </div>
                </div>

                <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                        GPT 모델 선택
                    </label>
                    <Popover open={openModelSelect} onOpenChange={setOpenModelSelect}>
                        <PopoverTrigger asChild>
                            <Button
                                variant="outline"
                                role="combobox"
                                aria-expanded={openModelSelect}
                                className="w-full h-10 px-3 outline-none border border-gray-300 rounded-lg hover:border-gray-400 justify-between"
                            >
                                <span className="text-sm text-gray-900">
                                    {codexModel
                                        ? (CHATGPT_MODELS.find((m) => m.id === codexModel)?.name ?? codexModel)
                                        : "모델 선택"}
                                </span>
                                <ChevronUp className="w-4 h-4 text-gray-400" />
                            </Button>
                        </PopoverTrigger>
                        <PopoverContent
                            className="p-0"
                            align="start"
                            style={{ width: "var(--radix-popover-trigger-width)" }}
                        >
                            <Command>
                                <CommandInput placeholder="모델 검색…" />
                                <CommandList>
                                    <CommandEmpty>모델을 찾을 수 없습니다.</CommandEmpty>
                                    <CommandGroup>
                                        {CHATGPT_MODELS.map((model) => (
                                            <CommandItem
                                                key={model.id}
                                                value={model.id}
                                                onSelect={(value) => {
                                                    onInputChange(value, "codex_model");
                                                    setOpenModelSelect(false);
                                                }}
                                            >
                                                <Check
                                                    className={cn(
                                                        "mr-2 h-4 w-4",
                                                        codexModel === model.id ? "opacity-100" : "opacity-0"
                                                    )}
                                                />
                                                <span className="text-sm text-gray-900">
                                                    {model.name}
                                                </span>
                                            </CommandItem>
                                        ))}
                                    </CommandGroup>
                                </CommandList>
                            </Command>
                        </PopoverContent>
                    </Popover>
                </div>
            </div>
        );
    }

    return (
        <button
            onClick={handleSignIn}
            className="mt-8 py-2.5 px-3.5 bg-[#EDEEEF] hover:bg-[#E4E5E6] rounded-[48px] text-xs font-semibold text-[#101323] transition-colors"
        >
            ChatGPT로 로그인
        </button>
    );
}
