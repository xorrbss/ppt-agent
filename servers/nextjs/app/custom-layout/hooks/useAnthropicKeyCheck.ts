import { useState, useEffect } from "react";

export const useAnthropicKeyCheck = () => {
  const [hasAnthropicKey, setHasAnthropicKey] = useState(false);
  const [isAnthropicKeyLoading, setIsAnthropicKeyLoading] = useState(true);

  useEffect(() => {
    fetch("/api/has-anthropic-key")
      .then((res) => res.json())
      .then((data) => {
        setHasAnthropicKey(data.hasKey);
        setIsAnthropicKeyLoading(false);
      });
  }, []);

  return { hasAnthropicKey, isAnthropicKeyLoading };
}; 