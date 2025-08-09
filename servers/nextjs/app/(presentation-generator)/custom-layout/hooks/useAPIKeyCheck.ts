import { useState, useEffect } from "react";

export const useAPIKeyCheck = () => {
  const [hasRequiredKey, setHasAnthropicKey] = useState(false);
  const [isRequiredKeyLoading, setIsAnthropicKeyLoading] = useState(true);

  useEffect(() => {
    fetch("/api/has-required-key")
      .then((res) => res.json())
      .then((data) => {
        setHasAnthropicKey(data.hasKey);
        setIsAnthropicKeyLoading(false);
      });
  }, []);

  return { hasRequiredKey, isRequiredKeyLoading };
}; 