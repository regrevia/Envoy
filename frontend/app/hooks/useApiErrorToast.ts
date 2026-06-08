"use client";

import { useEffect } from 'react';
import { ApiError } from '@/lib/api';

export function useApiErrorToast(error: unknown, onMessage: (message: string) => void) {
  useEffect(() => {
    if (!error) return;
    const message = error instanceof ApiError ? error.message : error instanceof Error ? error.message : '请求失败';
    onMessage(message);
  }, [error, onMessage]);
}
