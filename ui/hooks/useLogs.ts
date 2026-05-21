import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import apiClient from '../../api/client';

export interface LogRow { _time?: string; _msg?: string; [k: string]: unknown }
export interface LogAlertRule {
  id: number;
  name: string;
  query: string;
  window_sec: number;
  threshold: number;
  severity: string;
  notify_to: string;
  enabled: boolean;
  last_fired?: string | null;
  last_count: number;
}

export function useLogQuery(query: string, limit = 500, enabled = true) {
  return useQuery({
    queryKey: ['logs', 'query', query, limit],
    enabled: enabled && query.length > 0,
    queryFn: async () => {
      const r = await apiClient.get<{ rows: LogRow[]; count: number }>(
        '/v1/logs/query', { params: { query, limit } }
      );
      return r.data;
    },
  });
}

export function useLogStreams() {
  return useQuery({
    queryKey: ['logs', 'streams'],
    queryFn: async () => {
      const r = await apiClient.get<{ streams: unknown[] }>('/v1/logs/streams');
      return r.data.streams;
    },
  });
}

export function useLogAlerts() {
  return useQuery({
    queryKey: ['log-alerts'],
    queryFn: async () => (await apiClient.get<LogAlertRule[]>('/v1/log-alerts')).data,
  });
}

export function useSaveLogAlert() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (r: Partial<LogAlertRule> & { id?: number }) => {
      if (r.id) {
        return (await apiClient.put(`/v1/log-alerts/${r.id}`, r)).data;
      }
      return (await apiClient.post('/v1/log-alerts', r)).data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['log-alerts'] }),
  });
}

export function useDeleteLogAlert() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) =>
      (await apiClient.delete(`/v1/log-alerts/${id}`)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['log-alerts'] }),
  });
}

export function useModules() {
  return useQuery({
    queryKey: ['modules'],
    queryFn: async () => {
      try {
        const r = await apiClient.get<{ modules: string[] }>('/v1/modules');
        return r.data.modules ?? [];
      } catch {
        return [] as string[];
      }
    },
    staleTime: 60_000,
  });
}
