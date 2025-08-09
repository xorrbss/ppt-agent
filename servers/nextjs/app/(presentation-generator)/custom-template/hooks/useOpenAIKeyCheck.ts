import { useState, useEffect } from "react";

export const useOpenAIKeyCheck = () => {
  const [hasOpenAIKey, setHasOpenAIKey] = useState(false);
  const [isOpenAIKeyLoading, setIsOpenAIKeyLoading] = useState(true);

  useEffect(() => {
    fetch("/api/has-openai-key")
      .then((res) => res.json())
      .then((data) => {
        setHasOpenAIKey(Boolean(data.hasKey));
        setIsOpenAIKeyLoading(false);
      })
      .catch(() => setIsOpenAIKeyLoading(false));
  }, []);

  return { hasOpenAIKey, isOpenAIKeyLoading };
}; 