import { useEffect, useState } from 'react';
import { getExplanation } from '../api/client';
import type { Explanation } from '../api/types';

/** Запросы объяснений по датасету, версии плана и заявке: карточка заявки и панель «Почему» делят один запрос. */
const requests = new Map<string, Promise<Explanation>>();

/** Для тестов: запросы живут на уровне модуля. */
export function clearExplanationCache(): void {
  requests.clear();
}

function load(datasetId: string, requestId: string, version: number): Promise<Explanation> {
  const key = `${datasetId}:${version}:${requestId}`;
  const known = requests.get(key);
  if (known) return known;
  const request = getExplanation(datasetId, requestId);
  requests.set(key, request);
  // Сбой не запоминается: следующий показ заявки спросит сервер заново.
  request.catch(() => {
    if (requests.get(key) === request) requests.delete(key);
  });
  return request;
}

export interface ExplanationLoad {
  explanation: Explanation | null;
  error: string | null;
  loading: boolean;
}

/** Объяснение по заявке для текущего плана: заново запрашивается, когда меняется версия плана. */
export function useExplanation(datasetId: string | null, requestId: string | null, version: number): ExplanationLoad {
  const [explanation, setExplanation] = useState<Explanation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!datasetId || !requestId) {
      setExplanation(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    load(datasetId, requestId, version)
      .then((data) => {
        if (!cancelled) setExplanation(data);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setExplanation(null);
        setError(err instanceof Error ? err.message : 'Не удалось получить объяснение');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [datasetId, requestId, version]);

  return { explanation, error, loading };
}
