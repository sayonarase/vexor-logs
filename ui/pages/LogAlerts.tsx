import React, { useState } from 'react';
import {
  Stack, Title, Paper, Table, Button, Group, Modal, TextInput,
  NumberInput, Select, Switch, Textarea, ActionIcon, Badge, Text,
} from '@mantine/core';
import { IconPlus, IconTrash, IconPencil } from '@tabler/icons-react';
import { useLogAlerts, useSaveLogAlert, useDeleteLogAlert, type LogAlertRule } from '../hooks/useLogs';

type Draft = Partial<LogAlertRule> & {
  name: string; query: string; window_sec: number; threshold: number;
  severity: string; notify_to: string; enabled: boolean;
};

const EMPTY: Draft = {
  name: '', query: '', window_sec: 300, threshold: 1,
  severity: 'warning', notify_to: '', enabled: true,
};

export default function LogAlerts(): React.JSX.Element {
  const { data: rules = [], isLoading } = useLogAlerts();
  const save = useSaveLogAlert();
  const del  = useDeleteLogAlert();
  const [opened, setOpened] = useState(false);
  const [draft, setDraft]   = useState<Draft>(EMPTY);

  const open = (r?: LogAlertRule) => { setDraft(r ? { ...r } : EMPTY); setOpened(true); };

  const submit = async () => { await save.mutateAsync(draft); setOpened(false); };

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Title order={2}>Log Alerts</Title>
        <Button leftSection={<IconPlus size={16} />} onClick={() => open()}>New rule</Button>
      </Group>
      <Paper p="md" withBorder>
        {isLoading ? <Text c="dimmed">loading…</Text> : (
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Name</Table.Th><Table.Th>Severity</Table.Th>
                <Table.Th>Window</Table.Th><Table.Th>Threshold</Table.Th>
                <Table.Th>Last count</Table.Th><Table.Th>Last fired</Table.Th>
                <Table.Th>Enabled</Table.Th><Table.Th></Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rules.map((r) => (
                <Table.Tr key={r.id}>
                  <Table.Td>{r.name}</Table.Td>
                  <Table.Td><Badge color={r.severity === 'critical' ? 'red' : 'yellow'}>{r.severity}</Badge></Table.Td>
                  <Table.Td>{r.window_sec}s</Table.Td>
                  <Table.Td>{r.threshold}</Table.Td>
                  <Table.Td>{r.last_count}</Table.Td>
                  <Table.Td>{r.last_fired ?? '—'}</Table.Td>
                  <Table.Td>{r.enabled ? '✓' : '—'}</Table.Td>
                  <Table.Td>
                    <Group gap={4}>
                      <ActionIcon variant="subtle" onClick={() => open(r)}><IconPencil size={16} /></ActionIcon>
                      <ActionIcon variant="subtle" color="red" onClick={() => del.mutate(r.id)}><IconTrash size={16} /></ActionIcon>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Paper>

      <Modal opened={opened} onClose={() => setOpened(false)} title={draft.id ? 'Edit rule' : 'New log alert rule'} size="lg">
        <Stack>
          <TextInput label="Name" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.currentTarget.value })} required />
          <Textarea label="LogsQL query" value={draft.query} autosize minRows={2}
                    onChange={(e) => setDraft({ ...draft, query: e.currentTarget.value })} required
                    styles={{ input: { fontFamily: 'monospace' } }} />
          <Group grow>
            <NumberInput label="Window (s)" value={draft.window_sec} min={10}
                         onChange={(v) => setDraft({ ...draft, window_sec: Number(v) || 0 })} />
            <NumberInput label="Threshold" value={draft.threshold} min={1}
                         onChange={(v) => setDraft({ ...draft, threshold: Number(v) || 0 })} />
            <Select label="Severity" value={draft.severity}
                    data={['info', 'warning', 'critical']}
                    onChange={(v) => setDraft({ ...draft, severity: v ?? 'warning' })} />
          </Group>
          <TextInput label="Notify to (contact group)" value={draft.notify_to}
                     onChange={(e) => setDraft({ ...draft, notify_to: e.currentTarget.value })} />
          <Switch label="Enabled" checked={draft.enabled}
                  onChange={(e) => setDraft({ ...draft, enabled: e.currentTarget.checked })} />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setOpened(false)}>Cancel</Button>
            <Button onClick={submit} loading={save.isPending}>Save</Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
